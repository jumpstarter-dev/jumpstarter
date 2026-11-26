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
from jumpstarter.client.exceptions import LeaseError
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

    def test_close_all_closes_every_session_after_a_failure(self, tree):
        first, second, third = (sessions.install(Session.from_client(tree, name)) for name in "abc")
        for session in (first, third):
            session._stack.callback(lambda name=session.name: 1 // 0)
        with pytest.raises(ExceptionGroup, match="failed to close") as errors:
            sessions.close_all()
        assert len(errors.value.exceptions) == 2
        assert not (first.alive or second.alive or third.alive)

    def test_reset_clears_everything_after_a_failed_close(self, tree):
        sessions.install(Session.from_client(tree, "lab"))._stack.callback(lambda: 1 // 0)
        sessions.register_factory("lab", Counter(tree, "lab"))
        sessions.set_default("lab")
        sessions.set_lease_end_policy("fail")
        with pytest.raises(ZeroDivisionError):
            sessions.reset()
        assert sessions.names() == []
        assert sessions.default_name() == "default"
        assert sessions.lease_end_policy() == "fatal"


class TestDeclarations:
    """Which connection a later declaration of the same session name replaces."""

    @pytest.fixture
    def running(self, monkeypatch):
        suite = SimpleNamespace(id="s1-s1")
        monkeypatch.setattr(sessions, "_running_suite", lambda: suite.id)
        return suite

    def declare(self, tree, target):
        sessions.register_factory("rig", Counter(tree, "rig"), target)

    @pytest.mark.parametrize("declared", ["a", None])
    def test_another_suite_gets_its_own_target(self, tree, running, declared):
        if declared:
            self.declare(tree, declared)
        opened = sessions.install(Session.from_client(tree, "rig"), target="a")
        running.id = "s1-s2"
        self.declare(tree, "a")
        assert opened.alive  # the same target
        self.declare(tree, "b")
        assert not opened.alive

    def test_another_suite_does_not_inherit_keyword_arguments(self, tree, running):
        self.declare(tree, "a")
        opened = sessions.install(Session.from_client(tree, "rig"), target="override")
        self.declare(tree, "a")
        assert opened.alive  # the suite that opened it chose the override
        running.id = "s1-s1-s4"
        self.declare(tree, "a")
        assert opened.alive  # and its children use it
        running.id = "s1-s10"
        self.declare(tree, "a")
        assert not opened.alive

    def test_a_session_already_connected_to_the_target_stays(self, tree, running):
        running.id = "s1"
        self.declare(tree, "a")
        opened = sessions.install(Session.from_client(tree, "rig"), target="b")
        running.id = "s1-s1"
        self.declare(tree, "b")
        assert opened.alive

    def test_child_suites_keep_what_a_parent_acquired(self, tree, running):
        running.id = "s1"
        opened = sessions.install(Session.from_client(tree, "rig"), target="leased")
        running.id = "s1-s2"
        self.declare(tree, "local")
        assert opened.alive


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
        self.endpoint = "grpc.example.com:443"
        self.metadata = SimpleNamespace(name="me", namespace="default")
        self.ended = set()
        self.listed = list(listed)
        self.listings = 0

    async def list_leases(self, only_active=True):
        assert only_active
        self.listings += 1
        return LeaseList(leases=self.listed, next_page_token=None)

    @asynccontextmanager
    async def lease_async(self, selector, exporter_name, lease_name, duration, portal, **kwargs):
        self.calls.append((selector, exporter_name, lease_name, duration, kwargs))
        if lease_name in self.ended:
            raise LeaseError(f"lease {lease_name} has already ended")
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

    @pytest.mark.parametrize("empty", ["lab=", "lab==", "lab!="])
    def test_empty_values_are_requirements(self, empty):
        assert self.match([listed("l1", "board=rpi4")], f"board=rpi4,{empty}") is None
        assert self.match([listed("l1", f"{empty},board=rpi4")], f"board=rpi4,{empty}") == "l1"

    def test_selectors_that_cannot_be_read_match_nothing(self):
        for selector in ("board=rpi4,lab in", "board=rpi4,lab~eu", "lab in (eu", "board=rpi4,", ",board=rpi4", "a,,b"):
            assert self.match([listed("l1", selector), listed("l2", "board=rpi4")], selector) is None
        assert self.match([listed("l1", "board=rpi4,lab~eu")], "board=rpi4") is None

    def test_the_controllers_empty_selector(self):
        lease = listed("l1", "<none>", exporter_name="rpi4-01")
        assert self.match([lease], None, "rpi4-01") == "l1"
        assert self.match([lease], "board=rpi4", "rpi4-01") is None

    @pytest.mark.parametrize("selector", [",,,", ",", " , "])
    def test_empty_requirements_are_not_the_empty_selector(self, selector):
        # the controller's parser rejects them
        assert self.match([listed("l1", "<none>"), listed("l2", "")], selector) is None
        assert self.match([listed("l1", selector)], selector) is None

    def test_contradictory_values_are_kept(self):
        assert self.match([listed("l1", "board=rpi4")], "board=rpi5,board=rpi4") is None


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

    def test_recovery_command_quotes_the_selector(self, config):
        with pytest.raises(NoSession, match=r"jmp create lease -l 'board in \(rpi4,rpi5\)' --duration"):
            Session.from_lease("lab", selector="board in (rpi4,rpi5)", acquire=False)

    def test_acquires_when_the_reused_lease_has_ended(self, config):
        config.listed, config.ended = [listed("gone", "board=rpi4")], {"gone"}
        session = Session.from_lease("lab", selector="board=rpi4", duration=timedelta(minutes=5), release_grace=0)
        assert [call[:4] for call in config.calls] == [
            (None, None, "gone", timedelta(minutes=5)),
            ("board=rpi4", None, None, timedelta(minutes=5)),
        ]
        assert session.owns_lease is True
        session.close()

    def test_attach_only_fails_when_the_reused_lease_has_ended(self, config):
        config.listed, config.ended = [listed("gone", "board=rpi4")], {"gone"}
        with pytest.raises(LeaseError, match="has already ended"):
            Session.from_lease("lab", selector="board=rpi4", acquire=False)
        assert len(config.calls) == 1

    @pytest.mark.parametrize(
        ("endpoint", "namespace", "free"),
        [("grpc.example.com:443", "default", False), ("other:443", "default", True), (None, "west", True)],
    )
    def test_a_lease_is_held_by_its_controller_and_namespace(self, config, tree, endpoint, namespace, free):
        holder = Session.from_client(tree, "holder")
        holder.lease_name, holder.lease_scope = "lab-1", (endpoint, namespace)
        sessions.install(holder)
        config.listed = [listed("lab-1", "board=rpi4")]
        session = Session.from_lease("lab", selector="board=rpi4", release_grace=0)
        assert session.owns_lease is not free
        assert session.lease_scope == ("grpc.example.com:443", "default")
        session.close()
