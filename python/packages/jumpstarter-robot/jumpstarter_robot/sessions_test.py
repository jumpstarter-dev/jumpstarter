import time
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import anyio
import pytest
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_power.client import PowerClient
from jumpstarter_driver_power.driver import MockPower

from . import sessions
from .conftest import write
from .errors import JumpstarterRobotError
from .sessions import LeaseEnded, NoSession, Session, matching_lease
from .testing import use_client, use_session_factory
from jumpstarter.client import DriverClient
from jumpstarter.client.grpc import Lease, LeaseList
from jumpstarter.common import ExporterStatus
from jumpstarter.common.utils import serve
from jumpstarter.config.env import JMP_DRIVERS_ALLOW, JMP_EXPORTER, JMP_EXPORTER_LABELS, JUMPSTARTER_HOST
from jumpstarter.exporter import Session as ExporterSession


@pytest.fixture(scope="module")
def tree():
    with serve(Composite(children={"dut": Composite(children={"power": MockPower()}), "storage": MockPower()})) as c:
        yield c


@dataclass
class FakeLease:
    lease_ended: bool = False
    lease_transferred: bool = False
    lease_revoked: bool = False


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

    def test_closed_session_reopens_from_factory(self, tree):
        factory = Counter(tree, "lab")
        sessions.register_factory("lab", factory)
        first = sessions.get("lab")
        assert sessions.close("lab") is True
        second = sessions.get("lab")
        assert second is not first
        assert second.exporter == "exp-2"

    def test_failing_factory_installs_nothing(self):
        def broken():
            raise RuntimeError("no exporter")

        sessions.register_factory("lab", broken)
        with pytest.raises(RuntimeError):
            sessions.get("lab")
        assert sessions.opened("lab") is None

    def test_no_session(self):
        with pytest.raises(NoSession, match="no Jumpstarter session 'default'"):
            sessions.get()

    def test_unregister_only_matching_factory(self, tree):
        factory = Counter(tree)
        sessions.register_factory("lab", factory)
        sessions.unregister_factory("lab", Counter(tree, "lab"))
        assert "lab" in sessions.names()
        sessions.unregister_factory("lab", factory)
        assert "lab" not in sessions.names()

    def test_install_conflict(self, tree):
        first = sessions.install(Session.from_client(tree, "x"))
        assert sessions.install(first) is first  # installing the same session again is fine
        with pytest.raises(JumpstarterRobotError, match="already open"):
            sessions.install(Session.from_client(tree, "x"))
        first.close()
        replacement = sessions.install(Session.from_client(tree, "x"))
        assert sessions.get("x") is replacement

    def test_close_is_idempotent(self, tree):
        session = sessions.install(Session.from_client(tree, "x"))
        assert sessions.close("x") is True
        assert sessions.close("x") is False
        assert sessions.close("never") is False
        session.close()  # closing a closed session does nothing

    def test_close_all(self, tree):
        a = sessions.install(Session.from_client(tree, "a"))
        b = sessions.install(Session.from_client(tree, "b"))
        sessions.close_all()
        assert not a.alive and not b.alive
        assert sessions.opened("a") is None

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

    def test_switch_lasts_for_its_suite(self):
        assert sessions.set_default("a", suite="s1") == "default"
        assert sessions.set_default("b", suite="s1-s1") == "a"
        sessions.end_suite("s1-s1")
        assert (sessions.default_name(), sessions.default_switched()) == ("a", True)
        sessions.end_suite("s1-s1")  # every listener calls it; the second call does nothing
        assert sessions.default_name() == "a"
        sessions.end_suite("s2")
        assert sessions.default_name() == "a"
        sessions.end_suite("s1")
        assert (sessions.default_name(), sessions.default_switched()) == ("default", False)

    def test_switch_without_suite_lasts_until_reset(self):
        sessions.set_default("a")
        sessions.end_suite("s1")
        assert sessions.default_name() == "a"

    def test_config_registry(self):
        config, other = object(), object()
        sessions.register_config("lab", config)
        assert sessions.registered_config("lab") is config
        sessions.unregister_config("lab", other)  # only the config that registered removes it
        assert sessions.registered_config("lab") is config
        sessions.unregister_config("lab", config)
        assert sessions.registered_config("lab") is None
        sessions.register_config("lab", config)
        sessions.reset()
        assert sessions.registered_config("lab") is None

    def test_lease_end_policy(self):
        assert sessions.lease_end_policy() == "fatal"
        sessions.set_lease_end_policy("FAIL")
        assert sessions.lease_end_policy() == "fail"
        with pytest.raises(ValueError, match="on_lease_end"):
            sessions.set_lease_end_policy("ignore")

    def test_session_lease_end_policy_wins(self):
        sessions.set_lease_end_policy("fail", session="lab")
        assert sessions.lease_end_policy("lab") == "fail"
        assert sessions.lease_end_policy("other") == "fatal"
        assert sessions.lease_end_policy() == "fatal"
        sessions.reset()
        assert sessions.lease_end_policy("lab") == "fatal"

    def test_conflicting_lease_end_policy_warns(self, caplog):
        sessions.set_lease_end_policy("fatal")  # the default, but now explicit
        sessions.set_lease_end_policy("fatal")
        assert "on_lease_end" not in caplog.text
        sessions.set_lease_end_policy("fail")
        assert "on_lease_end for the run changed from 'fatal' to 'fail'" in caplog.text
        sessions.set_lease_end_policy("fail", session="lab")
        sessions.set_lease_end_policy("fatal", session="lab")
        assert "on_lease_end for session 'lab' changed from 'fail' to 'fatal'" in caplog.text


class TestShellFallback:
    def test_shell_used_when_nothing_registered(self, tree, monkeypatch):
        monkeypatch.setenv(JUMPSTARTER_HOST, "/tmp/nowhere.sock")
        shell = Counter(tree)
        with use_session_factory(shell=shell):
            assert sessions.get("lab").exporter == "exp-1"
        assert shell.calls == [("lab", {})]

    def test_factory_wins_over_shell(self, tree, monkeypatch):
        monkeypatch.setenv(JUMPSTARTER_HOST, "/tmp/nowhere.sock")
        shell, factory = Counter(tree), Counter(tree)
        sessions.register_factory("default", factory)
        with use_session_factory(shell=shell):
            sessions.get()
        assert shell.calls == []
        assert len(factory.calls) == 1

    def test_use_session_factory_restores_and_closes(self, tree):
        saved = sessions.open_lease, sessions.open_local, sessions.open_shell
        lease = Counter(tree)
        with use_session_factory(lease=lease, local=lease, shell=lease):
            assert sessions.open_lease is lease
            sessions.install(sessions.open_lease("x"))
        assert (sessions.open_lease, sessions.open_local, sessions.open_shell) == saved
        assert sessions.opened("x") is None

    def test_real_shell(self, monkeypatch):
        with ExporterSession(root_device=MockPower()) as exporter, exporter.serve_unix() as path:
            exporter.update_status(ExporterStatus.LEASE_READY)
            monkeypatch.setenv(JUMPSTARTER_HOST, str(path))
            monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
            monkeypatch.setenv(JMP_EXPORTER, "shell-exporter")
            monkeypatch.setenv(JMP_EXPORTER_LABELS, "board=rpi4,lab=eu")
            session = sessions.get()
            assert session.kind == "shell"
            assert session.exporter == "shell-exporter"
            assert session.labels == {"board": "rpi4", "lab": "eu"}
            power = session.resolve("power")
            assert isinstance(power, PowerClient)
            power.call("on")
            assert power.call("status") == "on"
            sessions.close()


class TestSession:
    def test_check_alive(self, tree):
        session = Session.from_client(tree)
        assert session.alive
        session.check_alive()
        session.close()
        assert not session.alive
        with pytest.raises(LeaseEnded, match="closed"):
            session.check_alive()

    @pytest.mark.parametrize(
        "flag, message",
        [("lease_ended", "has ended"), ("lease_transferred", "transferred"), ("lease_revoked", "revoked")],
    )
    def test_check_alive_lease_flags(self, tree, flag, message):
        lease = FakeLease()
        session = Session(name="x", kind="lease", client=tree, lease=lease, lease_name="lease-1")
        session.check_alive()
        setattr(lease, flag, True)
        assert not session.alive
        with pytest.raises(LeaseEnded, match=message) as error:
            session.check_alive()
        assert error.value.session == "x"  # so the session's own lease end policy applies

    def test_lease_without_revoked_flag(self, tree):
        class OldLease:
            lease_ended = False
            lease_transferred = False

        session = Session(name="x", kind="lease", client=tree, lease=OldLease())
        assert session.alive
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

    def test_resolve_single_driver_root(self):
        with serve(MockPower()) as power:
            session = Session.from_client(power)
            assert session.resolve("power") is power
            with pytest.raises(KeyError, match="available: \\[\\]"):
                session.resolve("power.child")

    def test_closers_run_lifo_once_and_errors_are_logged(self, tree, caplog):
        session = Session.from_client(tree)
        order = []
        session.on_close(lambda: order.append(1))
        session.on_close(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        session.on_close(lambda: order.append(3))
        session.close()
        session.close()
        assert order == [3, 1]
        assert "error while closing" in caplog.text

    def test_release_grace_only_for_owned_leases(self, tree, monkeypatch):
        slept = []
        monkeypatch.setattr(sessions.time, "sleep", slept.append)
        Session(name="a", kind="lease", client=tree, owns_lease=False, release_grace=1.0).close()
        assert slept == []
        Session(name="b", kind="lease", client=tree, owns_lease=True, release_grace=0.5).close()
        assert slept == [0.5]

    def test_use_client(self, tree):
        with use_client(tree, "lab", exporter="e", labels={"a": "b"}) as session:
            assert sessions.get("lab") is session
            assert session.labels == {"a": "b"}
        assert sessions.opened("lab") is None


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

    def test_attached_lease_is_not_owned(self, tree, monkeypatch):
        config = FakeClientConfig(tree)
        monkeypatch.setattr(sessions, "load_client_config", lambda value, base_dir=None: config)
        session = Session.from_lease(lease_name="existing", selector="", release_grace=0)
        assert config.calls[0][:3] == (None, None, "existing")
        assert session.owns_lease is False
        session.close()

    def test_lease_ending_is_seen(self, tree, monkeypatch):
        config = FakeClientConfig(tree)
        monkeypatch.setattr(sessions, "load_client_config", lambda value, base_dir=None: config)
        session = Session.from_lease(selector="a=b", release_grace=0)
        session.check_alive()
        assert config.lease is not None
        config.lease.lease_ended = True
        with pytest.raises(LeaseEnded, match="lease-123 has ended"):
            session.check_alive()
        session.close()


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

    def test_missing_config(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            Session.from_exporter_config(config=str(tmp_path / "missing.yaml"))


def listed(name, selector="", *, client="me", exporter_name=None, began=None, ended=False, shared=()):
    """An active lease as the controller lists it."""
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


def at(minute):
    return datetime(2026, 11, 3, 12, minute, tzinfo=UTC)


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

    def test_expressions_in_any_order(self):
        # as the controller reports it: != becomes notin, requirements sorted by key
        lease = listed("l1", "board in (rpi4,rpi5),!broken,lab notin (us,cn)")
        assert self.match([lease], "lab!=cn, !broken, board in (rpi5, rpi4), lab!=us") == "l1"
        assert self.match([lease], "board in (rpi4,rpi5),!broken") is None
        assert self.match([lease], "board in (rpi4),!broken,lab notin (us,cn)") is None

    def test_only_leases_this_client_may_use(self):
        assert self.match([listed("theirs", "a=b", client="other")], "a=b") is None
        assert self.match([listed("shared", "a=b", client="other", shared=["me"])], "a=b") == "shared"

    def test_ended_leases_are_skipped(self):
        assert self.match([listed("old", "a=b", ended=True)], "a=b") is None

    def test_exporter_name(self):
        leases = [listed("any", "a=b"), listed("pinned", "a=b", exporter_name="rpi4-01")]
        assert self.match(leases, "a=b", "rpi4-01") == "pinned"
        assert self.match(leases, "a=b", "rpi4-02") is None
        assert self.match([listed("by-name", exporter_name="rpi4-01")], exporter_name="rpi4-01") == "by-name"

    def test_several_matches_pick_the_latest_then_by_name(self, caplog):
        caplog.set_level("INFO")
        leases = [listed("b", "a=b", began=at(1)), listed("c", "a=b", began=at(5)), listed("a", "a=b")]
        assert self.match(leases, "a=b") == "c"
        assert "using c, not a, b" in caplog.text
        assert self.match([listed("y", "a=b", began=at(1)), listed("x", "a=b", began=at(1))], "a=b") == "x"


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

    def test_a_lease_another_session_of_the_run_holds_is_not_shared(self, config):
        config.listed = [listed("mine", "board=rpi4")]
        first = sessions.install(Session.from_lease("first", selector="board=rpi4", release_grace=0))
        try:
            assert first.lease_name == "mine"
            config.calls.clear()
            second = Session.from_lease("second", selector="board=rpi4", release_grace=0)
            # the only matching lease is held by "first", so "second" acquires its own
            assert config.calls[0][:3] == ("board=rpi4", None, None)
            assert second.owns_lease is True
            second.close()
        finally:
            sessions.close("first")

    def test_reuse_disabled(self, config):
        config.listed = [listed("mine", "board=rpi4")]
        Session.from_lease(selector="board=rpi4", reuse_lease=False, release_grace=0).close()
        assert config.listings == 0
        assert config.calls[0][:3] == ("board=rpi4", None, None)

    def test_named_lease_or_jmp_lease_is_not_looked_up(self, config, monkeypatch):
        config.listed = [listed("mine", "board=rpi4")]
        Session.from_lease(selector="board=rpi4", lease_name="given", release_grace=0).close()
        monkeypatch.setenv("JMP_LEASE", "from-env")
        Session.from_lease(selector="board=rpi4", release_grace=0).close()
        assert config.listings == 0

    def test_attach_only_uses_a_matching_lease(self, config):
        config.listed = [listed("mine", "board=rpi4")]
        session = Session.from_lease(selector="board=rpi4", acquire=False, release_grace=0)
        assert config.calls[0][:3] == (None, None, "mine")
        assert session.owns_lease is False
        session.close()

    def test_attach_only_never_acquires(self, config):
        config.listed = [listed("narrow", "board=rpi4,lab=us")]
        expected = r"no active lease of client 'me' matches board=rpi4.*jmp create lease -l board=rpi4"
        with pytest.raises(NoSession, match=expected):
            Session.from_lease("lab", selector="board=rpi4", acquire=False)
        assert config.calls == []

    def test_attach_only_without_reuse_looks_for_nothing(self, config):
        # a pool testbed turns reuse off: its processes must never share a lease made outside the run
        config.listed = [listed("someone-elses", "board=rpi4")]
        with pytest.raises(NoSession, match="lease reuse is off"):
            Session.from_lease(selector="board=rpi4", reuse_lease=False, acquire=False)
        assert config.listings == 0 and config.calls == []

    def test_attach_only_to_a_named_or_jmp_lease(self, config, monkeypatch):
        Session.from_lease(lease_name="given", acquire=False, release_grace=0).close()
        monkeypatch.setenv("JMP_LEASE", "from-env")
        Session.from_lease(acquire=False, release_grace=0).close()
        assert config.listings == 0
        assert [call[2] for call in config.calls] == ["given", None]  # the client reads JMP_LEASE itself

    def test_listing_failure_acquires_a_new_lease(self, config, caplog):
        async def broken(only_active=True):
            raise ConnectionError("controller unreachable")

        config.list_leases = broken
        Session.from_lease(selector="board=rpi4", release_grace=0).close()
        assert config.calls[0][:3] == ("board=rpi4", None, None)
        assert "cannot list active leases" in caplog.text


class SlowClientConfig:
    """A controller that never grants the lease; records whether acquiring was cancelled."""

    def __init__(self):
        self.events = []

    @asynccontextmanager
    async def lease_async(self, *args, **kwargs):
        self.events.append("acquiring")
        try:
            await anyio.sleep_forever()
        finally:
            self.events.append("cancelled")
        yield  # pragma: no cover


class SlowExporterConfig:
    metadata = SimpleNamespace(name="slow")

    def __init__(self):
        self.events = []

    @asynccontextmanager
    async def serve_unix_async(self):
        self.events.append("starting")
        try:
            await anyio.sleep_forever()
        finally:
            self.events.append("cancelled")
        yield  # pragma: no cover


class TestInterruptedOpen:
    """An RF timeout (or Ctrl-C) while a session opens fails the test instead of hanging the run."""

    def test_acquire_lease_timeout(self, tmp_path, monkeypatch, run_robot):
        config = SlowClientConfig()
        monkeypatch.setattr(sessions, "load_client_config", lambda value, base_dir=None: config)
        suite = write(
            tmp_path,
            "timeout.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Lease Takes Too Long
                [Timeout]    0.5s
                Acquire Lease    selector=board=x    reuse=False

            Next Test Runs
                Log    ran
            """,
        )
        started = time.monotonic()
        run = run_robot(suite)
        assert time.monotonic() - started < 10
        assert run.status("Lease Takes Too Long") == "FAIL"
        assert "timeout" in run.message("Lease Takes Too Long").lower()
        assert run.status("Next Test Runs") == "PASS"
        assert config.events == ["acquiring", "cancelled"]

    def test_start_local_exporter_timeout(self, tmp_path, monkeypatch, run_robot):
        exporter = SlowExporterConfig()
        monkeypatch.setattr(sessions, "load_exporter_config", lambda value, base_dir=None: exporter)
        suite = write(
            tmp_path,
            "timeout.robot",
            """
            *** Settings ***
            Library    jumpstarter_robot.Jumpstarter

            *** Test Cases ***
            Exporter Takes Too Long
                [Timeout]    0.5s
                Start Local Exporter    exporter.yaml
            """,
        )
        started = time.monotonic()
        run = run_robot(suite)
        assert time.monotonic() - started < 10
        assert run.status("Exporter Takes Too Long") == "FAIL"
        assert exporter.events == ["starting", "cancelled"]
