from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_power.driver import MockPower

from . import sessions
from .sessions import LeaseEnded, NoSession, Session, matching_lease
from .testing import use_session_factory
from jumpstarter.client import DriverClient
from jumpstarter.client.grpc import Lease, LeaseList
from jumpstarter.common.utils import serve
from jumpstarter.config.env import JUMPSTARTER_HOST


@pytest.fixture(scope="module")
def tree():
    with serve(Composite(children={"dut": Composite(children={"power": MockPower()}), "storage": MockPower()})) as c:
        yield c


@dataclass
class Counter:
    """A session opener; registered factories are called without arguments, so they know their name."""

    tree: DriverClient
    name: str = "default"
    calls: list = field(default_factory=list)

    def __call__(self, name=None, **kwargs):
        name = name or self.name
        self.calls.append((name, kwargs))
        return Session.from_client(self.tree, name, exporter=f"exp-{len(self.calls)}")


class TestRegistry:
    def test_factory_opens_lazily_once(self, tree):
        factory = Counter(tree, "lab")
        sessions.register_factory("lab", factory)
        assert factory.calls == []
        assert sessions.opened("lab") is None
        first = sessions.get("lab")
        assert sessions.get("lab") is first
        assert len(factory.calls) == 1
        assert sessions.opened("lab") is first
        assert "lab" in sessions.names()


    def test_no_session(self):
        with pytest.raises(NoSession, match="no Jumpstarter session 'default'"):
            sessions.get()


    def test_default_and_switch(self, tree):
        assert sessions.default_name() == "default"
        assert sessions.default_switched() is False
        assert sessions.set_default("lab") == "default"
        assert sessions.default_switched() is True
        sessions.install(Session.from_client(tree, "lab"))
        assert sessions.get().name == "lab"
        sessions.reset()
        assert sessions.default_name() == "default"
        assert sessions.default_switched() is False

    def test_lease_end_policy(self):
        assert sessions.lease_end_policy() == "fatal"
        sessions.set_lease_end_policy("FAIL")
        assert sessions.lease_end_policy() == "fail"
        with pytest.raises(ValueError, match="on_lease_end"):
            sessions.set_lease_end_policy("ignore")

class TestShellFallback:
    def test_shell_used_when_nothing_registered(self, tree, monkeypatch):
        monkeypatch.setenv(JUMPSTARTER_HOST, "/tmp/nowhere.sock")
        shell = Counter(tree)
        with use_session_factory(shell=shell):
            assert sessions.get("lab").exporter == "exp-1"
        assert shell.calls == [("lab", {})]

class TestSession:
    def test_check_alive(self, tree):
        session = Session.from_client(tree)
        assert session.alive
        session.check_alive()
        session.close()
        assert not session.alive
        with pytest.raises(LeaseEnded, match="closed"):
            session.check_alive()


    def test_resolve(self, tree):
        session = Session.from_client(tree)
        assert session.resolve("") is tree
        assert isinstance(session.resolve("dut.power"), PowerClient)
        assert session.resolve("dut") is tree.children["dut"]
        assert isinstance(session.resolve("storage"), PowerClient)

    def test_resolve_errors_list_available_children(self, tree):
        session = Session.from_client(tree)
        with pytest.raises(KeyError) as error:
            session.resolve("nope")
        assert "driver 'nope'" in error.value.args[0]
        assert "['dut', 'storage']" in error.value.args[0]
        with pytest.raises(KeyError) as error:
            session.resolve("dut.nope.deeper")
        assert "driver 'dut.nope' (from path 'dut.nope.deeper')" in error.value.args[0]
        assert "['power']" in error.value.args[0]

class FakeControllerLease:
    def __init__(self, client, release):
        self.client = client
        self.release = release
        self.name = "lease-123"
        self.exporter_name = "rpi4-01"
        self.exporter_labels = {"board": "rpi4"}
        self.lease_ended = self.lease_transferred = False
        self.events = []

    @contextmanager
    def monitor(self):
        self.events.append("monitor")
        yield
        self.events.append("monitor-exit")

    @contextmanager
    def connect(self):
        self.events.append("connect")
        yield self.client
        self.events.append("disconnect")


class FakeClientConfig:
    def __init__(self, client, release=True, listed=()):
        self.client = client
        self.release = release
        self.calls = []
        self.lease = None
        self.metadata = SimpleNamespace(name="me")
        self.listed = list(listed)
        self.listings = 0

    async def list_leases(self, only_active=True):
        assert only_active
        self.listings += 1
        return LeaseList(leases=self.listed, next_page_token=None)

    @asynccontextmanager
    async def lease_async(self, selector, exporter_name, lease_name, duration, portal, **kwargs):
        self.calls.append((selector, exporter_name, lease_name, duration, kwargs))
        self.lease = FakeControllerLease(self.client, self.release and not lease_name)
        if lease_name:
            self.lease.name = lease_name  # attaching keeps the lease's own name
        yield self.lease
        self.lease.events.append("released")


class TestFromLease:
    def test_acquires_and_releases(self, tree, monkeypatch):
        config = FakeClientConfig(tree)
        loaded = []
        monkeypatch.setattr(sessions, "load_client_config", lambda value, base_dir=None: loaded.append(value) or config)
        session = Session.from_lease(
            "lab", config="lab-eu", selector="board=rpi4", duration=timedelta(minutes=5), release_grace=0
        )
        assert loaded == ["lab-eu"]
        assert config.calls == [
            ("board=rpi4", None, None, timedelta(minutes=5), {"acquisition_timeout": None, "allow_disabled": False})
        ]
        assert (session.kind, session.exporter, session.lease_name) == ("lease", "rpi4-01", "lease-123")
        assert session.labels == {"board": "rpi4"}
        assert session.owns_lease is True
        assert session.resolve("dut.power") is tree.children["dut"].children["power"]
        session.close()
        assert config.lease is not None
        assert config.lease.events == ["monitor", "connect", "disconnect", "monitor-exit", "released"]


class TestFromExporterConfig:
    def test_serves_a_local_config(self, tmp_path):
        (tmp_path / "exporter.yaml").write_text(
            "apiVersion: jumpstarter.dev/v1alpha1\n"
            "kind: ExporterConfig\n"
            "metadata:\n  namespace: default\n  name: local-rig\n"
            "endpoint: ''\ntoken: ''\n"
            "export:\n"
            "  dut:\n"
            "    type: jumpstarter_driver_composite.driver.Composite\n"
            "    children:\n"
            "      power:\n"
            "        type: jumpstarter_driver_power.driver.MockPower\n"
        )
        session = Session.from_exporter_config("rig", config="exporter.yaml", config_dir=tmp_path)
        try:
            assert (session.name, session.kind, session.exporter) == ("rig", "local", "local-rig")
            power = session.resolve("dut.power")
            assert isinstance(power, PowerClient)
            power.call("on")
            assert power.call("status") == "on"
        finally:
            session.close()
        assert not session.alive

STARTED = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


def listed(name, selector="", *, client="me", exporter_name=None, began=STARTED, ended=False, shared=()):
    """A lease as the controller lists it: active unless ``began`` is None or it ``ended``."""
    return Lease(
        namespace="default",
        name=name,
        selector=selector,
        exporter_name=exporter_name,
        duration=timedelta(hours=1),
        client=client,
        exporter="rpi4-01",
        conditions=[],
        effective_begin_time=began,
        effective_end_time=datetime.now(UTC) if ended else None,
        effective_shared_with=list(shared),
    )


class TestMatchingLease:
    def match(self, leases, selector=None, exporter_name=None):
        found = matching_lease(leases, "me", selector, exporter_name)
        return found.name if found is not None else None

    def test_exact_selector_in_any_order(self):
        assert self.match([listed("l1", "board=rpi4,lab=eu")], "lab=eu, board=rpi4") == "l1"

    def test_narrower_or_wider_selectors_do_not_match(self):
        leases = [listed("narrow", "board=rpi4"), listed("wide", "board=rpi4,lab=eu,rack=3")]
        assert self.match(leases, "board=rpi4,lab=eu") is None
        assert self.match([listed("l1", "board=rpi4")], "board=rpi5") is None


class TestLeaseReuse:
    @pytest.fixture
    def config(self, tree, monkeypatch):
        config = FakeClientConfig(tree)
        monkeypatch.setattr(sessions, "load_client_config", lambda value, base_dir=None: config)
        return config

    def test_attaches_to_a_matching_lease_and_never_releases_it(self, config, monkeypatch, caplog):
        caplog.set_level("INFO")
        slept = []
        monkeypatch.setattr(sessions.time, "sleep", slept.append)
        config.listed = [listed("other", "board=rpi4"), listed("mine", "lab=eu,board=rpi4")]
        session = Session.from_lease("lab", selector="board=rpi4, lab=eu", release_grace=1.0)
        assert config.calls[0][:3] == (None, None, "mine")  # the controller would compare the selector as text
        assert session.owns_lease is False
        assert "reusing lease mine for session 'lab'" in caplog.text
        session.close()
        assert slept == []  # no release, so no grace

    def test_acquires_when_nothing_matches(self, config):
        config.listed = [listed("narrow", "board=rpi4")]
        session = Session.from_lease(selector="board=rpi4,lab=eu", release_grace=0)
        assert config.calls[0][:3] == ("board=rpi4,lab=eu", None, None)
        assert session.owns_lease is True
        session.close()


    def test_attach_only_never_acquires(self, config):
        config.listed = [listed("narrow", "board=rpi4,lab=us")]
        expected = r"no active lease of client 'me' matches board=rpi4.*jmp create lease -l board=rpi4"
        with pytest.raises(NoSession, match=expected):
            Session.from_lease("lab", selector="board=rpi4", acquire=False)
        assert config.calls == []
