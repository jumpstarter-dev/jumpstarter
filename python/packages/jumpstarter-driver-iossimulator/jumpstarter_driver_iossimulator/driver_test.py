import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import anyio
import pytest

from . import driver as module
from .driver import IosSimulator
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

DEVICE_TYPE = "com.apple.CoreSimulator.SimDeviceType.iPhone-18-Pro"
RUNTIME = "com.apple.CoreSimulator.SimRuntime.iOS-27-0"


class FakeSimctl:
    """Simulate simctl device state and companion processes."""

    def __init__(self):
        self.sets = {}
        self.calls = []
        self.processes = []
        self.fail = None
        self.invalid_create_udid = False
        self.ready = True

    def run(self, args, **kwargs):
        assert args[:3] == ["xcrun", "simctl", "--set"]
        assert kwargs["timeout"] > 0
        self.calls.append((args, kwargs))
        path, operation = args[3:5]
        devices = self.sets.setdefault(path, {})
        if operation == self.fail:
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        if operation == "list":
            output = json.dumps({"devices": {RUNTIME: list(deepcopy(devices).values())}})
        elif operation in ("create", "clone"):
            udid = str(uuid4()).upper()
            device = {"udid": udid, "name": "test simulator", "state": "Shutdown", "deviceTypeIdentifier": DEVICE_TYPE}
            if operation == "clone":
                assert devices[args[5]]["state"] == "Shutdown"
                devices = self.sets.setdefault(args[-1], {})
            devices[udid] = device
            output = "booted" if self.invalid_create_udid else udid
        elif args[5] == "all":
            output = self._all(devices, operation)
        else:
            udid = args[5]
            assert udid in devices
            if operation == "boot":
                devices[udid]["state"] = "Booted"
            elif operation == "shutdown":
                devices[udid]["state"] = "Shutdown"
            elif operation == "bootstatus":
                assert devices[udid]["state"] == "Booted"
            elif operation == "delete":
                del devices[udid]
            else:
                raise AssertionError(f"Unexpected operation: {operation}")
            output = ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    @staticmethod
    def _all(devices, operation):
        assert operation in ("shutdown", "delete")
        for udid in list(devices):
            if operation == "delete":
                del devices[udid]
            else:
                devices[udid]["state"] = "Shutdown"
        return ""

    def popen(self, args, **kwargs):
        assert "--grpc-domain-sock" in args
        assert "--grpc-port" not in args
        assert args[args.index("--only") + 1] == "simulator"
        assert args[args.index("--udid") + 1] in self.sets[args[args.index("--device-set-path") + 1]]
        process = MagicMock()
        process.poll.return_value = None
        process.wait.side_effect = lambda **_: setattr(process.poll, "return_value", 0)
        process.args = args
        self.processes.append(process)
        return process

    def commands(self, operation):
        return [args for args, _ in self.calls if args[4] == operation]


@pytest.fixture
def state():
    # Keep UDS paths short on macOS; pytest's own temp root can exceed sun_path.
    with tempfile.TemporaryDirectory(prefix="jsios-test-", dir="/tmp") as value:
        yield Path(value).resolve()


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSimctl()
    monkeypatch.setattr(module.subprocess, "run", fake.run)
    monkeypatch.setattr(module.subprocess, "Popen", fake.popen)
    monkeypatch.setattr(IosSimulator, "_socket_ready", lambda _: fake.ready)
    monkeypatch.setattr(module, "_processes_using", lambda _: [])
    return fake


@pytest.fixture
def simulator(state, fake):
    simulator = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))
    yield simulator
    fake.fail = None
    simulator.close()


def test_lazy_initialization(simulator, fake, state):
    assert not fake.calls
    assert list(state.iterdir()) == []
    assert set(simulator.children) == {"ios", "power"}
    assert simulator.client().endswith("CompositeClient")
    assert simulator.children["power"].client().endswith("VirtualPowerClient")
    info = simulator.children["ios"].info()
    assert info["present"] is False
    assert info["protocols"] == ["idb"]
    assert info["forward_ports"] == []


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"device_type": DEVICE_TYPE},
        {"runtime": RUNTIME},
        {"golden_device_set": "/tmp/golden"},
        {"golden_udid": str(uuid4())},
        {
            "device_type": DEVICE_TYPE,
            "runtime": RUNTIME,
            "golden_device_set": "/tmp/golden",
            "golden_udid": str(uuid4()),
        },
        {"device_type": DEVICE_TYPE, "runtime": "com.apple.CoreSimulator.SimRuntime.tvOS-27-0"},
        {"device_type": DEVICE_TYPE, "runtime": "iOS 17.3"},
        {"golden_device_set": "/tmp/golden", "golden_udid": "booted"},
    ],
)
def test_invalid_target_configuration_rejected(config):
    with pytest.raises(ConfigurationError):
        IosSimulator(**config)


@pytest.mark.parametrize("field", ["command_timeout", "boot_timeout", "companion_timeout", "shutdown_timeout"])
@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf")])
def test_timeouts_must_be_positive_finite(field, value):
    with pytest.raises(ConfigurationError):
        IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, **{field: value})


def test_reserved_children_are_not_overwritten():
    with pytest.raises(ConfigurationError, match="reserved"):
        IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, children={"power": MagicMock()})


def test_default_device_set_rejected_as_golden():
    with pytest.raises(ConfigurationError, match="default"):
        IosSimulator(
            golden_device_set=str(Path.home() / "Library/Developer/CoreSimulator/Devices"),
            golden_udid=str(uuid4()),
        )


def test_on_creates_private_set_and_is_idempotent(simulator, fake):
    power = simulator.children["power"]
    power.on()
    root, udid = simulator._lease_dir, simulator._udid
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "devices").stat().st_mode & 0o777 == 0o700
    assert all(args[3] == str(root / "devices") for args, _ in fake.calls)
    assert fake.commands("boot")[0][5] == udid
    assert fake.commands("bootstatus")[0][5:] == [udid, "-b"]
    power.on()
    assert len(fake.commands("create")) == 1
    assert len(fake.commands("boot")) == 1
    assert len(fake.processes) == 1
    assert simulator.children["ios"].info()["present"] is True


def test_power_cycle_preserves_device(simulator, fake):
    power = simulator.children["power"]
    power.on()
    root, udid = simulator._lease_dir, simulator._udid
    process = fake.processes[0]
    power.off()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=simulator.shutdown_timeout)
    assert root.exists()
    assert simulator._udid == udid
    assert simulator.children["ios"].info()["present"] is False
    power.on()
    assert simulator._udid == udid
    assert len(fake.commands("create")) == 1
    assert len(fake.processes) == 2


@pytest.mark.parametrize("operation", ["destroy", "reset", "close"])
def test_lifecycle_cleanup_destroys_only_owned_set(simulator, fake, state, operation):
    unrelated = state / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep").write_text("user data")
    simulator.children["power"].on()
    root = simulator._lease_dir
    if operation == "destroy":
        simulator.children["power"].off(destroy=True)
    else:
        getattr(simulator, operation)()
    assert not root.exists()
    assert simulator._lease_dir is None
    assert simulator._udid is None
    assert (unrelated / "keep").read_text() == "user data"
    assert len(fake.commands("delete")) == 1
    assert all(args[3] == str(root / "devices") for args, _ in fake.calls)
    assert all("all" not in args for args, _ in fake.calls)


def test_new_lease_gets_new_set_and_udid(simulator):
    simulator.children["power"].on()
    first = (simulator._lease_dir, simulator._udid)
    simulator.close()
    simulator.reset()
    simulator.children["power"].on()
    assert simulator._lease_dir != first[0]
    assert simulator._udid != first[1]


def test_two_instances_do_not_share_sets(state, fake):
    first = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))
    second = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))
    try:
        first.children["power"].on()
        second.children["power"].on()
        assert first._lease_dir != second._lease_dir
        assert first._udid != second._udid
        first.close()
        assert second.children["ios"].info()["present"] is True
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("operation", ["boot", "bootstatus"])
def test_boot_failure_cleans_partial_lease(simulator, fake, state, operation):
    fake.fail = operation
    with pytest.raises(RuntimeError, match="Could not run"):
        simulator.children["power"].on()
    assert simulator._lease_dir is None
    assert list(state.iterdir()) == []
    assert len(fake.commands("delete")) == 1


@pytest.mark.parametrize("operation", ["boot", "bootstatus", "companion"])
def test_restart_failure_preserves_lease_data(simulator, fake, operation):
    power = simulator.children["power"]
    power.on()
    power.off()
    root, udid = simulator._lease_dir, simulator._udid
    if operation == "companion":
        fake.ready = False
        simulator.companion_timeout = 0.01
    else:
        fake.fail = operation
    with pytest.raises(RuntimeError):
        power.on()
    assert simulator._lease_dir == root and root.exists()
    assert simulator._udid == udid
    assert fake.commands("delete") == []
    assert simulator._device()[1]["state"] == "Shutdown"
    fake.fail, fake.ready = None, True
    power.on()
    assert simulator._udid == udid
    assert simulator.children["ios"].info()["present"] is True
    assert len(fake.commands("create")) == 1


def test_invalid_create_output_cleanup(simulator, fake, state):
    fake.invalid_create_udid = True
    with pytest.raises(RuntimeError, match="valid simulator UDID"):
        simulator.children["power"].on()
    assert len(fake.commands("delete")) == 1
    assert list(state.iterdir()) == []


def test_missing_companion_cleans_booted_simulator(simulator, fake, state):
    with (
        patch.object(module.subprocess, "Popen", side_effect=FileNotFoundError("idb_companion")),
        pytest.raises(FileNotFoundError),
    ):
        simulator.children["power"].on()
    assert len(fake.commands("shutdown")) == 1
    assert len(fake.commands("delete")) == 1
    assert list(state.iterdir()) == []


def test_companion_startup_exit_cleanup(simulator, fake, state):
    process = MagicMock()
    process.poll.return_value = 42
    with (
        patch.object(module.subprocess, "Popen", return_value=process),
        pytest.raises(RuntimeError, match="exited before"),
    ):
        simulator.children["power"].on()
    process.wait.assert_called_once()
    assert list(state.iterdir()) == []


def test_companion_readiness_timeout_stops_process(simulator, fake, state):
    fake.ready = False
    simulator.companion_timeout = 0.01
    with pytest.raises(RuntimeError, match="did not become ready"):
        simulator.children["power"].on()
    fake.processes[0].terminate.assert_called_once()
    assert list(state.iterdir()) == []


def test_hung_companion_is_killed_and_reaped(simulator, fake):
    simulator.children["power"].on()
    process = fake.processes[0]
    process.wait.side_effect = [subprocess.TimeoutExpired("idb_companion", 30), 0]
    simulator.children["power"].off()
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2


def test_crashed_companion_is_unavailable_and_can_restart(simulator, fake):
    simulator.children["power"].on()
    fake.processes[0].poll.return_value = 1
    info = simulator.children["ios"].info()
    assert info["present"] is False
    assert "companion" in info["reason"]
    simulator.children["power"].on()
    assert simulator.children["ios"].info()["present"] is True
    assert len(fake.commands("create")) == 1


def test_shutdown_failure_retains_ownership_for_retry(simulator, fake):
    simulator.children["power"].on()
    root = simulator._lease_dir
    fake.fail = "shutdown"
    with pytest.raises(RuntimeError):
        simulator.children["power"].off(destroy=True)
    assert simulator._lease_dir == root
    assert root.exists()
    assert not fake.commands("delete")
    fake.fail = None
    simulator.close()
    assert not root.exists()


def test_off_before_first_on_does_nothing(simulator, fake):
    simulator.children["power"].off()
    simulator.children["power"].off(destroy=True)
    assert not fake.calls


def test_power_read_unsupported(simulator):
    with pytest.raises(NotImplementedError, match="electrical"):
        list(simulator.children["power"].read())


def test_socket_path_limit_cleans_uncreated_directory(state, fake):
    long_root = state / ("x" * 90)
    long_root.mkdir()
    simulator = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(long_root))
    with pytest.raises(RuntimeError, match="too long"):
        simulator.children["power"].on()
    assert not list(long_root.iterdir())
    assert not fake.calls


def test_owned_set_replacement_is_never_followed(simulator, fake, state):
    simulator.children["power"].on()
    root = simulator._lease_dir
    original = root / "devices"
    preserved = root / "original-devices"
    original.rename(preserved)
    target = state / "user-devices"
    target.mkdir()
    (target / "keep").write_text("private")
    original.symlink_to(target, target_is_directory=True)
    count = len(fake.calls)
    try:
        with pytest.raises(RuntimeError, match="owned directory"):
            simulator.close()
        assert len(fake.calls) == count
        assert (target / "keep").read_text() == "private"
    finally:
        original.unlink()
        preserved.rename(original)


def test_clone_preserves_golden_source(state, fake):
    golden = state / "golden"
    golden.mkdir()
    source_udid = str(uuid4()).upper()
    fake.sets[str(golden)] = {
        source_udid: {"udid": source_udid, "state": "Shutdown", "deviceTypeIdentifier": DEVICE_TYPE}
    }
    source = deepcopy(fake.sets[str(golden)])
    simulator = IosSimulator(golden_device_set=str(golden), golden_udid=source_udid, state_dir=str(state))
    try:
        simulator.children["power"].on()
        clone = fake.commands("clone")[0]
        assert clone[3] == str(golden)
        assert clone[5] == source_udid
        assert clone[-1] == str(simulator._lease_dir / "devices")
        assert simulator._udid != source_udid
        simulator.close()
        assert fake.sets[str(golden)] == source
        assert golden.is_dir()
        assert all(args[3] != str(golden) for args, _ in fake.calls if args[4] not in ("list", "clone"))
    finally:
        simulator.close()


def test_running_golden_is_refused_without_stopping_it(state, fake):
    golden = state / "golden"
    golden.mkdir()
    source_udid = str(uuid4()).upper()
    fake.sets[str(golden)] = {source_udid: {"udid": source_udid, "state": "Booted"}}
    simulator = IosSimulator(golden_device_set=str(golden), golden_udid=source_udid, state_dir=str(state))
    with pytest.raises(RuntimeError, match="must be Shutdown"):
        simulator.children["power"].on()
    assert [args[4] for args, _ in fake.calls] == ["list"]
    assert simulator._lease_dir is None
    assert fake.sets[str(golden)][source_udid]["state"] == "Booted"


def test_session_teardown_destroys_simulator(simulator, fake):
    with serve(simulator) as client:
        client.power.on()
        assert client.ios.info()["present"] is True
        root = simulator._lease_dir
    assert not root.exists()
    assert simulator._process is None
    assert len(fake.commands("delete")) == 1


def test_real_socket_readiness_rejects_plain_file(state):
    simulator = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))
    simulator._lease_dir = state
    simulator._lease_identity = simulator._identity(state)
    (state / "devices").mkdir()
    simulator._set_identity = simulator._identity(state / "devices")
    (state / "idb.sock").write_text("not a socket")
    assert simulator._socket_ready() is False
    (state / "idb.sock").unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(state / "idb.sock"))
        server.listen(1)
        assert simulator._socket_ready() is True


def test_idb_stream_refuses_powered_off_simulator(simulator):
    async def connect():
        with pytest.raises(RuntimeError, match="powered off"):
            async with simulator.children["ios"].connect_idb():
                pytest.fail("powered-off simulator must not expose a companion")

    anyio.run(connect)


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"provider": ""},
        {"provider": "not_a_dotted_class"},
        {"provider": "module.Provider", "options": []},
        {"provider": "module.Provider", "options": {"context": "override"}},
        {"provider": "module.Provider", "extra": True},
    ],
)
def test_http_provider_configuration_rejected_early(config):
    with pytest.raises(ConfigurationError):
        IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, http_service=config)


def test_http_provider_lifecycle(simulator, state, monkeypatch, fake):
    simulator.http_service = {"provider": "operator.Service", "options": {"key": "value"}}
    services = []

    def factory(config, context):
        assert config == simulator.http_service
        assert context.udid == simulator._udid
        assert context.device_set == simulator._owned_set()
        service = MagicMock()
        service.close.side_effect = lambda: None if not fake.commands("shutdown") else pytest.fail("late service stop")
        services.append(service)
        return service

    monkeypatch.setattr(module, "create_http_service", factory)
    with pytest.raises(RuntimeError, match="powered off"):
        simulator._prepare_http_service()
    simulator._on()
    with pytest.raises(RuntimeError, match="Prepare"):
        simulator._current_http_service()
    first = simulator._prepare_http_service()
    assert simulator._prepare_http_service() is first and len(services) == 1
    assert simulator._info()["protocols"] == ["idb"]
    assert simulator._info()["https_services"] == ["https"]
    simulator._off()
    first.close.assert_called_once()
    with pytest.raises(RuntimeError, match="Prepare"):
        simulator._current_http_service()
    simulator._on()
    second = simulator._prepare_http_service()
    assert second is not first
    second.close.side_effect = None


def _crash(simulator):
    """Leave a lease behind as an exporter that exited without cleanup would."""
    lease = simulator._lease_dir
    simulator._forget_lease()  # Closing the lock is what the kernel does at process exit.
    return lease


def _simulator(state):
    return IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))


def test_orphaned_lease_is_swept_by_the_next_lease(state, fake, monkeypatch):
    crashed = _simulator(state)
    crashed.children["power"].on()
    orphan = _crash(crashed)
    stopped = []
    monkeypatch.setattr(module, "_processes_using", lambda path: [4242] if path == orphan else [])
    monkeypatch.setattr(module, "_stop_processes", stopped.extend)
    survivor = _simulator(state)
    try:
        survivor.children["power"].on()
        assert not orphan.exists()
        assert stopped == [4242]
        orphan_calls = [args[4:] for args, _ in fake.calls if args[3] == str(orphan / "devices")]
        assert orphan_calls[-2:] == [["shutdown", "all"], ["delete", "all"]]
        assert survivor.children["ios"].info()["present"] is True
    finally:
        survivor.close()


def test_session_reset_sweeps_orphaned_leases(state, fake):
    crashed = _simulator(state)
    crashed.children["power"].on()
    orphan = _crash(crashed)
    _simulator(state).reset()
    assert not orphan.exists()


def test_live_leases_are_never_swept(state, fake):
    first, second = _simulator(state), _simulator(state)
    try:
        first.children["power"].on()
        second.children["power"].on()
        second.reset()
        assert first._lease_dir.exists()
        assert first.children["ios"].info()["present"] is True
        assert not [args for args, _ in fake.calls if args[4:] == ["delete", "all"]]
    finally:
        first.close()
        second.close()


def test_sweep_ignores_entries_it_did_not_create(state, fake, tmp_path):
    unmarked = state / "js-ios-unmarked"
    unmarked.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / module.LEASE_LOCK).write_text("")
    (state / "js-ios-link").symlink_to(elsewhere)
    (state / "js-ios-file").write_text("not a lease")
    simulator = _simulator(state)
    try:
        simulator.children["power"].on()
    finally:
        simulator.close()
    assert unmarked.is_dir() and (elsewhere / module.LEASE_LOCK).exists() and (state / "js-ios-file").exists()


def test_process_lookup_matches_only_this_users_lease_processes(monkeypatch):
    lease = Path("/private/tmp/js-ios-abc123")
    listing = "\n".join(
        [
            f"101 {os.getuid()} idb_companion --grpc-domain-sock {lease}/idb.sock",
            f"102 {os.getuid()} node appium --config {lease}/appium-x/config.json",
            f"103 {os.getuid() + 1} idb_companion --grpc-domain-sock {lease}/idb.sock",
            f"104 {os.getuid()} idb_companion --grpc-domain-sock {lease}0/idb.sock",
            f"{os.getpid()} {os.getuid()} python {lease}/script.py",
        ]
    )
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, listing, "")
    )
    assert module._processes_using(lease) == [101, 102]


def test_stop_processes_terminates_leftovers():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        module._stop_processes([process.pid], timeout=0.5)
        assert process.wait(timeout=5) == -signal.SIGTERM
    finally:
        process.kill()
        process.wait()
