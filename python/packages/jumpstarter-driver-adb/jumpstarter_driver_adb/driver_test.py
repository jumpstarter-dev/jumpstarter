import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from . import driver as adb_driver
from .driver import AdbDevice, AdbServer
from jumpstarter.common.exceptions import ConfigurationError

SERIAL = "HVA1234567"
USB_PORT = "usb:1-4.2"


@pytest.fixture(autouse=True)
def _reset_server_registry():
    """Clear the process-wide ADB server registry between tests.

    `_SERVERS` is module-level on purpose (one server per port per process), so a test
    that leaves an entry behind would make later tests adopt it and pass or fail
    depending on ordering.
    """
    adb_driver._SERVERS.clear()
    yield
    adb_driver._SERVERS.clear()


class _FakeAdb:
    """Stand-in for the adb binary that remembers device and forward state.

    A single canned return value cannot model this driver: it resolves a USB port to a
    serial through `devices -l`, then reconciles its forward against `forward --list`.
    Both have to reflect what earlier calls did.

    `forward tcp:0` allocates a port and echoes it on stdout, as real adb does — the
    driver reads that number to learn where the device landed.
    """

    #: Where the fake starts handing out ports for `tcp:0`.
    FIRST_PORT = 41000

    def __init__(self, devices=((SERIAL, "device", USB_PORT),)):
        #: (serial, state, devpath|None); devpath None models an emulator.
        self.devices = list(devices)
        self.forwards = {}  # local port -> serial
        self.calls = []
        self._next_port = self.FIRST_PORT

    def _devices_long(self):
        lines = ["List of devices attached"]
        for serial, state, devpath in self.devices:
            extra = f" {devpath}" if devpath else ""
            lines.append(f"{serial}\t{state}{extra} product:x model:y device:z")
        return "\n".join(lines) + "\n"

    def _acquire(self, serial, argv, check):
        """Model `acquire_one_transport`, which every `-s` command goes through first.

        Not scoping — `-s` does not narrow what a command acts on. It selects a transport,
        and the server refuses the whole request when that transport is missing or not in
        the `device` state (`transport.cpp` rejects offline/unauthorized/connecting unless
        the caller passes `accept_any_state`, and no forward request does).
        """
        state = next((st for s, st, _ in self.devices if s == serial), None)
        stderr = f"error: device '{serial}' not found\n" if state is None else f"error: device {state}\n"
        if state == "device":
            return None
        if check:
            raise subprocess.CalledProcessError(1, argv, output="", stderr=stderr)
        return MagicMock(stdout="", stderr=stderr, returncode=1)

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        args = argv[1:]

        if "-s" in args:
            refusal = self._acquire(args[args.index("-s") + 1], argv, kwargs.get("check", False))
            if refusal is not None:
                return refusal

        if args[:1] == ["version"]:
            return MagicMock(stdout="Android Debug Bridge version 1.0.41", stderr="", returncode=0)
        if args[:2] == ["devices", "-l"]:
            return MagicMock(stdout=self._devices_long(), stderr="", returncode=0)
        if args[:1] == ["devices"]:
            return MagicMock(stdout="List of devices attached\n", stderr="", returncode=0)

        if "forward" in args:
            return self._forward(args)

        return MagicMock(stdout="ok", stderr="", returncode=0)

    def _forward(self, args):
        """Handle the `forward` family against the remembered forward state."""
        serial = args[args.index("-s") + 1] if "-s" in args else ""
        if "--list" in args:
            lines = "".join(f"{s} tcp:{p} tcp:5555\n" for p, s in self.forwards.items())
            return MagicMock(stdout=lines, stderr="", returncode=0)
        if "--remove-all" in args:
            self.forwards = {p: s for p, s in self.forwards.items() if s != serial}
            return MagicMock(stdout="", stderr="", returncode=0)
        if "--remove" in args:
            # Keyed by the local spec alone: `remove_listener` ignores the transport, so
            # a `-s` here would not narrow which forward goes.
            self.forwards.pop(int(args[-1].removeprefix("tcp:")), None)
            return MagicMock(stdout="", stderr="", returncode=0)

        local = int(args[-2].removeprefix("tcp:"))
        if local == 0:
            local = self._next_port
            self._next_port += 1
        self.forwards[local] = serial
        # Real adb prints the chosen port, and only that, for tcp:0.
        return MagicMock(stdout=f"{local}\n", stderr="", returncode=0)


def _mock_adb_ok():
    """A mock that satisfies the version check and start-server."""
    return MagicMock(stdout="ok", stderr="", returncode=0)


def _fake(**kwargs):
    """Patch subprocess.run with a fresh `_FakeAdb`, returning it for assertions."""
    fake = _FakeAdb(**kwargs)
    return fake, patch("subprocess.run", new=MagicMock(side_effect=fake))


# ================================================================== AdbServer
#
# The server driver is now only about server lifecycle. The cuttlefish and
# androidemulator drivers embed it and call exactly these methods, so this surface is
# a compatibility contract.


@patch("shutil.which", return_value="/usr/bin/adb")
# Without this the probe opens a real socket to 15037, so the test would depend
# on whether the machine running it happens to have an ADB server there.
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_validates_adb(mock_run, mock_conn, mock_which):
    server = AdbServer()
    assert server.adb_path == "/usr/bin/adb"
    assert server.port == 15037
    # version check + start-server (the server driver starts eagerly)
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert ["/usr/bin/adb", "version"] in argvs
    assert ["/usr/bin/adb", "start-server"] in argvs


@patch("shutil.which", return_value=None)
def test_init_missing_adb(_):
    with pytest.raises(ConfigurationError, match="not found"):
        AdbServer()


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("adb version", 30.0),
        OSError("Input/output error"),
    ],
    ids=["hangs", "unreadable"],
)
@patch("shutil.which", return_value="/usr/bin/adb")
def test_an_adb_that_cannot_answer_version_is_a_config_error(_, failure):
    """This probe runs from `__post_init__`, so an unbounded one hangs exporter startup.

    Every other adb call in the driver is bounded by `connect_timeout`; this was the
    exception. There is nothing to recover from at that point, so expiry has to surface
    as a configuration failure rather than a wedged exporter.
    """
    with patch("subprocess.run", side_effect=failure) as run:
        with pytest.raises(ConfigurationError, match="not functional"):
            AdbServer()
    assert run.call_args.kwargs["timeout"] == 30.0, "the version probe must be bounded"


def test_invalid_port_negative():
    with pytest.raises(ConfigurationError):
        AdbServer(port=-1)


def test_invalid_port_too_high():
    with pytest.raises(ConfigurationError):
        AdbServer(port=70000)


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, "30", None])
@patch("shutil.which", return_value="/usr/bin/adb")
def test_invalid_connect_timeout(_, bad):
    with pytest.raises(ConfigurationError, match="connect_timeout"):
        AdbServer(connect_timeout=bad)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_start_server(mock_run, mock_conn, _):
    server = AdbServer()
    mock_run.reset_mock()
    assert server.start_server() == 15037
    call = mock_run.call_args_list[0]
    assert call.args[0] == ["/usr/bin/adb", "start-server"]
    assert call.kwargs["env"]["ANDROID_ADB_SERVER_PORT"] == "15037"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_kill_server(mock_run, mock_conn, _):
    server = AdbServer()
    mock_run.reset_mock()
    assert server.kill_server() == 15037
    assert mock_run.call_args_list[0].args[0] == ["/usr/bin/adb", "kill-server"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_list_devices(mock_conn, _):
    fake, patcher = _fake()
    with patcher:
        server = AdbServer()
        assert SERIAL in server.list_devices()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_custom_port(mock_run, mock_conn, _):
    assert AdbServer(port=5038).port == 5038


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_does_not_connect_to_any_device(mock_run, mock_conn, _):
    """Startup must not reach for hardware; devices are declared, not discovered."""
    AdbServer()
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert not any("connect" in argv for argv in argvs)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_connect_device(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.return_value = MagicMock(stdout="connected to 10.0.0.2:6520\n", stderr="", returncode=0)
    assert server.connect_device("10.0.0.2:6520") == "connected to 10.0.0.2:6520"
    assert mock_run.call_args.args[0] == ["/usr/bin/adb", "connect", "10.0.0.2:6520"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_connect_device_error(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.side_effect = subprocess.CalledProcessError(1, "adb connect")
    with pytest.raises(subprocess.CalledProcessError):
        server.connect_device("bad:99")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_connect_device_timeout(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.side_effect = subprocess.TimeoutExpired("adb connect", 30.0)
    with pytest.raises(TimeoutError):
        server.connect_device("bad:99")
    assert mock_run.call_args.kwargs["timeout"] == server.connect_timeout


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_disconnect_device(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.return_value = MagicMock(stdout="disconnected 10.0.0.2:6520\n", stderr="", returncode=0)
    assert server.disconnect_device("10.0.0.2:6520") == "disconnected 10.0.0.2:6520"
    assert mock_run.call_args.args[0] == ["/usr/bin/adb", "disconnect", "10.0.0.2:6520"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_disconnect_device_error(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.side_effect = subprocess.CalledProcessError(1, "adb disconnect")
    with pytest.raises(subprocess.CalledProcessError):
        server.disconnect_device("bad:99")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run")
def test_disconnect_device_timeout(mock_run, mock_conn, _):
    mock_run.return_value = _mock_adb_ok()
    server = AdbServer()
    mock_run.side_effect = subprocess.TimeoutExpired("adb disconnect", 30.0)
    with pytest.raises(TimeoutError):
        server.disconnect_device("bad:99")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_adbserver_keeps_the_surface_its_consumers_use(mock_run, mock_conn, _):
    """The cuttlefish and androidemulator drivers embed AdbServer and call these.

    A signature-level guard: this refactor removed a lot from AdbServer, and breaking
    one of these would surface as a 300s boot timeout on real hardware rather than a
    test failure in this package.
    """
    server = AdbServer()
    for name in (
        "start_server",
        "kill_server",
        "connect_device",
        "disconnect_device",
        "list_devices",
        "adb_env",
    ):
        assert callable(getattr(server, name)), name
    assert isinstance(server.adb_path, str)
    assert server.adb_env()["ANDROID_ADB_SERVER_PORT"] == "15037"


# ============================================== the shared, implicit ADB server
#
# An ADB server *claims* the USB devices it finds, and only one server can hold a
# given device. So on a host that already runs one, starting a second does not give
# us "another view" of the devices -- it gives us an empty one, while `start-server`
# reports success. Adopting the running server is the only way to see the hardware,
# and sharing one between drivers is a correctness requirement, not an optimization.


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_adopts_a_server_already_on_our_port(mock_run, mock_conn, _):
    """The running server owns the devices; ours would see none."""
    server = AdbServer()
    assert server._owns_server is False
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert ["/usr/bin/adb", "start-server"] not in argvs


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_an_adopted_server_is_left_running_on_close(mock_run, mock_conn, _):
    """Killing it would drop the device claims of everything else on the host."""
    server = AdbServer()
    server.close()
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert ["/usr/bin/adb", "kill-server"] not in argvs


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_starts_a_server_when_the_port_is_free(mock_run, mock_conn, _):
    server = AdbServer()
    assert server._owns_server is True
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert ["/usr/bin/adb", "start-server"] in argvs


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_a_server_we_started_is_killed_on_close(mock_run, mock_conn, _):
    """The port has to be genuinely free for the server to be ours.

    This previously passed with the port occupied, which only worked because the driver
    claimed ownership of a server it had not started — see
    `test_adopt_false_does_not_claim_a_server_it_could_not_start`.
    """
    server = AdbServer(adopt_existing_server=False)
    assert server._owns_server is True
    server.close()
    argvs = [c.args[0] for c in mock_run.call_args_list]
    assert ["/usr/bin/adb", "kill-server"] in argvs


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_a_non_adb_listener_is_not_adopted(mock_conn, _):
    """A plain TCP listener hangs the probe rather than failing it.

    Verified against adb 1.0.41: `start-server` and `devices` both block forever
    against a non-ADB listener. Adopting it would wedge every later call, so we
    decline and fall through to starting our own.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if argv[1:] == ["devices"] and kwargs.get("check") is False:
            # The probe: the socket accepted, but nothing answers as ADB.
            raise subprocess.TimeoutExpired("adb devices", 10)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        server = AdbServer()

    # Declined the adoption, so it started its own and owns it.
    assert server._owns_server is True
    assert ["/usr/bin/adb", "start-server"] in calls


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_the_adoption_probe_asks_the_server_not_the_client(mock_conn, _):
    """The probe has to be a command the server answers.

    `adb version` reports the local client's own version without contacting the
    server at all — verified against adb 1.0.41, where it exits 0 with zero
    connections to the port. Probing with it would adopt any listener.
    """
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs.get("check")))
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        server = AdbServer()

    probes = [argv for argv, check in calls if check is False]
    assert probes == [["/usr/bin/adb", "devices"]]
    # It answered, so the running server was adopted and left alone.
    assert server._owns_server is False
    assert ["/usr/bin/adb", "start-server"] not in [argv for argv, _ in calls]


@pytest.mark.parametrize(
    "failure, message",
    [
        (subprocess.TimeoutExpired("adb start-server", 30.0), "timed out"),
        (subprocess.CalledProcessError(1, "adb start-server", stderr="cannot bind"), "cannot bind"),
        (OSError("Permission denied"), "Permission denied"),
    ],
    ids=["hung-port", "exit-nonzero", "cannot-exec"],
)
@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_server_that_fails_to_start_is_an_error_not_a_running_server(mock_conn, _, failure, message):
    """A swallowed start failure left the driver registered as owning a running server.

    Every later adb call against that port then failed, far from the cause. The start is
    still bounded — a non-ADB listener makes `adb start-server` block forever — but its
    failure now surfaces where it happened, and nothing half-started is left behind.
    """

    def run(argv, **kwargs):
        if argv[1:] == ["start-server"]:
            assert kwargs.get("timeout"), "start-server must be bounded"
            raise failure
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match=message):
            AdbServer()

    assert not adb_driver._SERVERS, "registered a server that never started"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_failed_start_can_be_retried(mock_conn, _):
    """Nothing half-registered may block the next attempt once the port is free again."""
    attempts = []

    def run(argv, **kwargs):
        if argv[1:] == ["start-server"]:
            attempts.append(argv)
            if len(attempts) == 1:
                raise subprocess.TimeoutExpired("adb start-server", 30.0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError):
            AdbServer()
        server = AdbServer()

    assert len(attempts) == 2, "the retry never reached adb start-server"
    assert server._owns_server is True


@patch("shutil.which", return_value="/usr/bin/adb")
def test_start_server_reports_a_restart_failure(_):
    """The exported `start_server` restarts a dead server, and has to say when it can't."""
    fake, patcher = _fake()
    with patcher, patch("socket.create_connection", side_effect=OSError("refused")):
        server = AdbServer()

    def run(argv, **kwargs):
        if argv[1:] == ["start-server"]:
            raise subprocess.CalledProcessError(1, "adb start-server", stderr="cannot bind")
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run), patch("socket.create_connection", side_effect=OSError("refused")):
        with pytest.raises(RuntimeError, match="cannot bind"):
            server.start_server()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_list_devices_is_bounded(mock_conn, _):
    """`adb devices` hangs forever on a non-ADB listener."""

    def run(argv, **kwargs):
        if "devices" in argv:
            assert kwargs.get("timeout"), "devices must be bounded"
            raise subprocess.TimeoutExpired("adb devices", 30.0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        server = AdbServer()
        assert "Error" in server.list_devices()  # reported, not raised


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_two_devices_share_one_server(mock_conn, _):
    """Two servers on one port would split the USB device claims.

    The second server would see an empty device list while `adb start-server`
    reported success, so the driver would come up blind.
    """
    fake, patcher = _fake()
    with patcher:
        a = AdbDevice(usb_port="1-4.2")
        b = AdbDevice(usb_port="1-4.3")
        a._ensure_server()
        b._ensure_server()
        starts = [c for c in fake.calls if c[1:] == ["start-server"]]
        assert len(starts) == 1, f"expected one start-server, got {starts}"
        assert a._server is b._server


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_no_server_is_started_at_construction(mock_conn, _):
    """A bench whose DUTs are all powered off should not start a server it never uses.

    Startup must not depend on the ADB server either, so it is acquired lazily on the
    first stream instead.
    """
    fake, patcher = _fake()
    with patcher:
        AdbDevice(usb_port="1-4.2")
        assert not [c for c in fake.calls if c[1:] == ["start-server"]]
        assert not adb_driver._SERVERS


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_the_last_device_to_close_kills_the_server(mock_conn, _):
    """Refcounted: closing one device must not pull the server out from the other."""
    fake, patcher = _fake()
    with patcher:
        a = AdbDevice(usb_port="1-4.2")
        b = AdbDevice(usb_port="1-4.3")
        a._ensure_server()
        b._ensure_server()

        a.close()
        assert not [c for c in fake.calls if c[1:] == ["kill-server"]], "killed while still in use"

        b.close()
        assert [c for c in fake.calls if c[1:] == ["kill-server"]], "not killed after last release"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_an_adopted_server_is_not_killed_by_a_device(mock_conn, _):
    """It owns other processes' device claims."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        device._ensure_server()
        assert device._server is not None and device._server.owns is False
        device.close()
        assert not [c for c in fake.calls if c[1:] == ["kill-server"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_declared_server_and_a_device_share_one_server(mock_conn, _):
    """The androidemulator/cuttlefish coexistence case.

    Both declare an AdbServer on an explicit port; a co-located AdbDevice must adopt
    that one rather than starting a second.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(port=15037)
        device = AdbDevice(usb_port="1-4.2", server_port=15037)
        device._ensure_server()
        assert device._server is server._server
        assert len([c for c in fake.calls if c[1:] == ["start-server"]]) == 1


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_different_server_ports_get_independent_servers(mock_conn, _):
    """The registry is keyed per port, so distinct ports are distinct servers."""
    fake, patcher = _fake()
    with patcher:
        a = AdbDevice(usb_port="1-4.2", server_port=15037)
        b = AdbDevice(usb_port="1-4.3", server_port=15038)
        a._ensure_server()
        b._ensure_server()
        assert a._server is not b._server
        assert len([c for c in fake.calls if c[1:] == ["start-server"]]) == 2


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_two_adb_paths_on_one_port_share_one_server(mock_conn, _):
    """Keying the registry on (adb_path, port) split one real server across two entries.

    Only one process can listen on the port, and adb reaches it through
    ANDROID_ADB_SERVER_PORT without regard for which binary started it, so the two
    entries described the same server with two independent refcounts.
    """
    fake, patcher = _fake()
    with patcher:
        a = AdbDevice(usb_port="1-4.2")
        b = AdbDevice(usb_port="1-4.3", adb_path="/opt/platform-tools/adb")
        a._ensure_server()
        b._ensure_server()

        assert a._server is b._server
        assert list(adb_driver._SERVERS) == [15037]
        assert len([c for c in fake.calls if c[1:] == ["start-server"]]) == 1


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_differently_named_adb_does_not_get_its_own_refcount(mock_conn, _):
    """The consequence of the split key: either refcount reaching zero killed the server.

    The AdbServer entry hit zero on close and ran `adb kill-server` while the device
    still held its own entry, pointing at a process that no longer existed.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(adopt_existing_server=False)
        device = AdbDevice(usb_port="1-4.2", adb_path="/opt/platform-tools/adb")
        device._ensure_server()

        server.close()
        assert not [c for c in fake.calls if c[1:] == ["kill-server"]], "killed a server still in use"

        device.close()
        assert [c for c in fake.calls if c[1:] == ["kill-server"]], "last holder left the server running"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_start_server_does_not_claim_an_adopted_server(mock_conn, _):
    """`adb start-server` is silent and exits 0 whether it started a server or found one.

    So ownership cannot be inferred from it. Probing first keeps an adopted server
    unowned, and `close()` therefore leaves it running.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer()
        assert server._owns_server is False

        assert server.start_server() == 15037
        assert server._owns_server is False

        server.close()
    assert not [c for c in fake.calls if c[1:] == ["kill-server"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_restarting_a_dead_adopted_server_makes_it_ours(mock_conn, _):
    """A server this process started is ours even if the one we adopted was not.

    Ownership used to be decided once, at acquisition, by a throwaway registry entry —
    so a restart left it false and `close()` walked away from a server we had started.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer()
        assert server._owns_server is False

        mock_conn.side_effect = OSError("refused")  # the adopted server is gone
        assert server.start_server() == 15037
        assert server._owns_server is True

        server.close()
    assert [c for c in fake.calls if c[1:] == ["kill-server"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection")
def test_adopt_false_does_not_claim_a_server_it_could_not_start(mock_conn, _):
    """`adopt_existing_server: false` cannot conjure a second server on a taken port.

    `adb start-server` is silent and exits 0 whether it started one or found one, so
    starting here would "succeed" and leave us believing we own a server somebody else
    runs — and `close()` would kill it. Against Android Studio that drops every device
    claim, and Studio respawns its server about a second later (measured on hardware).
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(adopt_existing_server=False)
        assert server._owns_server is False, "claimed a server it did not start"
        server.close()

    assert not [c for c in fake.calls if c[1:] == ["kill-server"]], "killed someone else's server"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_kill_server_gives_up_ownership(mock_conn, _):
    """Nothing of ours runs on that port afterwards.

    Killing and then killing again on close would target whatever process took the
    freed port in between.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(adopt_existing_server=False)
        server.kill_server()
        server.close()
    assert len([c for c in fake.calls if c[1:] == ["kill-server"]]) == 1


@pytest.mark.parametrize(
    "failure, message",
    [
        (subprocess.TimeoutExpired("adb kill-server", 30.0), "timed out"),
        (subprocess.CalledProcessError(1, "adb kill-server", stderr="permission denied"), "permission denied"),
        (OSError("Input/output error"), "Input/output error"),
    ],
    ids=["hangs", "exit-nonzero", "cannot-exec"],
)
@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_kill_server_reports_a_failed_kill(mock_conn, _, failure, message):
    """The exported `kill_server` said nothing when the kill failed.

    It then cleared ownership as though the server were gone, so a caller asking to kill
    the server was told it had succeeded while the server kept running.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(adopt_existing_server=False)

    def run(argv, **kwargs):
        if argv[1:] == ["kill-server"]:
            raise failure
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run):
        with pytest.raises(RuntimeError, match=message):
            server.kill_server()

    assert server._owns_server is True, "gave up ownership of a server that is still running"


@patch("shutil.which", return_value="/usr/bin/adb")
def test_kill_server_notices_a_server_that_survived(_):
    """`adb kill-server` exits 0 even with no server to kill, so exit status proves nothing.

    Measured against adb 1.0.41: it prints `cannot connect to daemon` and exits 0 when the
    port is empty. What counts is whether the port is still being served afterwards.
    """
    fake, patcher = _fake()
    with patcher, patch("socket.create_connection", side_effect=OSError("refused")):
        server = AdbServer(adopt_existing_server=False)

    with (
        patcher,
        patch("socket.create_connection", return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock())),
    ):
        with pytest.raises(RuntimeError, match="still being served"):
            server.kill_server()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_teardown_finishes_even_if_the_server_will_not_die(mock_conn, _):
    """`close()` runs at lease end; a stuck server must not stop the lease being released.

    The failure is logged instead, and the registry entry is dropped either way.
    """
    fake, patcher = _fake()
    with patcher:
        server = AdbServer(adopt_existing_server=False)

    def run(argv, **kwargs):
        if argv[1:] == ["kill-server"]:
            raise subprocess.TimeoutExpired("adb kill-server", 30.0)
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run):
        server.close()  # must not raise

    assert not adb_driver._SERVERS


# ================================================================== AdbDevice
#
# One declared device per driver instance. Identity for USB is the BENCH PORT, not the
# serial, so hardware can be swapped between benches without a config change.


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_usb_port_is_normalized(mock_conn, _):
    """adb matches the devpath by exact string equality, including the `usb:` prefix."""
    fake, patcher = _fake()
    with patcher:
        assert AdbDevice(usb_port="1-4.2").usb_port == "usb:1-4.2"
        assert AdbDevice(usb_port="usb:1-4.2").usb_port == "usb:1-4.2"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_usb_port_resolves_to_a_serial_and_forwards(mock_conn, _):
    """The whole USB path: bench port -> current serial -> forward -> endpoint."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        assert device._resolve_endpoint() == ("127.0.0.1", _FakeAdb.FIRST_PORT)

    created = [c for c in fake.calls if "forward" in c and "--list" not in c]
    # The documented `-s SERIAL` selector, resolved from the port. Deliberately NOT
    # `-s usb:1-4.2`: that works (adb's MatchesTarget falls through to the devpath)
    # but it is undocumented, and we do not need it.
    assert created[0][1:] == ["-s", SERIAL, "forward", "tcp:0", "tcp:5555"]
    assert not any("usb:" in arg for call in fake.calls for arg in call)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_an_absent_device_is_an_actionable_error(mock_conn, _):
    """A DUT powered off by its relay is normal, not a crash — but say so clearly.

    The server can see other devices, so the port really is empty.
    """
    fake, patcher = _fake(devices=(("OTHERDEVICE99", "device", "usb:1-1.1"),))
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="no device on USB port usb:1-4.2") as e:
            device._resolve_endpoint()
    assert "powered off" in str(e.value)
    assert "OTHERDEVICE99 (usb:1-1.1)" in str(e.value), "should say what it can see"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_an_empty_server_still_blames_the_relay_first(mock_conn, _):
    """The bench case: one DUT, relay off, so its own server legitimately sees nothing.

    This is the ordinary state on a bench, not a misconfiguration, and the operator needs
    the relay — not a hunt for a rogue ADB server they do not have.
    """
    fake, patcher = _fake(devices=())
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="no device on USB port usb:1-4.2") as e:
            device._resolve_endpoint()

    assert "power relay" in str(e.value)
    assert "5037" not in str(e.value), "invented a rogue server with no evidence for one"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_failed_listing_does_not_orphan_our_forward(mock_conn, _):
    """bennyz, on the old slot pool: a failed listing must not look like "nothing exists".

    Reading an unanswerable `forward --list` as an empty one made a forward we own appear
    gone. Ownership was dropped and a second forward created, so the first was orphaned —
    invisible to teardown, and leaked for the life of the ADB server.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()

    def run(argv, **kwargs):
        if "--list" in argv:
            raise subprocess.TimeoutExpired("adb forward --list", 30.0)
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run):
        assert device._resolve_endpoint() == ("127.0.0.1", port), "abandoned a forward it owns"
        assert device._owns_forward is True, "disowned a forward it created"

    assert len(fake.forwards) == 1, f"created a second forward: {fake.forwards}"

    # And with the listing working again, teardown still removes exactly that forward.
    with patcher:
        device.close()
    assert not fake.forwards


@patch("shutil.which", return_value="/usr/bin/adb")
def test_devices_held_by_another_server_are_named_as_such(mock_conn, _=None):
    """With evidence, the split claim is stated — it is decisive, not a guess.

    Observed on real hardware: a desktop `adb` on 5037 held the tablet, so the driver's
    server on 15037 saw an empty list. A device is claimed by whichever server finds it
    first, so devices visible there and not here can only mean a split.
    """
    fake = _FakeAdb(devices=())

    def run(argv, **kwargs):
        if kwargs.get("env", {}).get("ANDROID_ADB_SERVER_PORT") == "5037":
            return MagicMock(stdout="List of devices attached\nR52X200B4NW\tdevice\n", stderr="", returncode=0)
        return fake(argv, **kwargs)

    # Something answers on 5037; our own port is refused so the driver starts its own.
    def connect(address, *a, **kw):
        if address[1] == 5037:
            return MagicMock(__enter__=MagicMock(), __exit__=MagicMock())
        raise OSError("refused")

    with patch("subprocess.run", side_effect=run), patch("socket.create_connection", side_effect=connect):
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="no device on USB port usb:1-4.2") as e:
            device._resolve_endpoint()

    reason = str(e.value)
    assert "power relay" in reason, "the relay is still the first thing to check"
    assert "R52X200B4NW" in reason, "should name what the other server holds"
    assert "set server_port to 5037" in reason


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_diagnosing_a_blind_server_does_not_start_one(mock_conn, _):
    """An `adb devices` against a free port starts a server there.

    A diagnostic that creates the thing it is diagnosing would leave a stray server on
    5037 on every failed resolve, so nothing is asked unless something already answers.
    """
    fake, patcher = _fake(devices=())
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError):
            device._resolve_endpoint()

    asked = [c for c in fake.calls if c[1:] == ["devices"]]
    assert not asked, f"probed adb on a port with nothing listening: {asked}"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_device_that_appears_later_needs_no_restart(mock_conn, _):
    """Powering the DUT on mid-lease must just work; endpoints resolve per call."""
    fake = _FakeAdb(devices=())
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError):
            device._resolve_endpoint()

        fake.devices = [(SERIAL, "device", USB_PORT)]  # relay powers it on
        assert device._resolve_endpoint() == ("127.0.0.1", _FakeAdb.FIRST_PORT)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_reenumeration_changes_the_serial_but_not_the_port(mock_conn, _):
    """The core regression this design exists to prevent.

    A relay power-cycle re-enumerates USB and can hand the device a different ADB
    serial. Because config names the bench PORT, the driver re-resolves and forwards
    against the new serial with no config edit and no restart.
    """
    fake = _FakeAdb()
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        assert device._resolve_endpoint() == ("127.0.0.1", _FakeAdb.FIRST_PORT)

        # Power cycle: same bench port, new serial, and the old forward is gone.
        fake.devices = [("NEWSERIAL999", "device", USB_PORT)]
        fake.forwards.clear()

        host, port = device._resolve_endpoint()
        assert (host, port) == ("127.0.0.1", _FakeAdb.FIRST_PORT + 1)
        assert fake.forwards[port] == "NEWSERIAL999"

    created = [c for c in fake.calls if "forward" in c and "--list" not in c and "--remove" not in c]
    assert created[-1][1:3] == ["-s", "NEWSERIAL999"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_live_forward_is_reused(mock_conn, _):
    """Streams are per-connection; re-forwarding on each would churn the ADB server."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        first = device._resolve_endpoint()
        assert device._resolve_endpoint() == first
    created = [c for c in fake.calls if "forward" in c and "--list" not in c and "--remove" not in c]
    assert len(created) == 1, created


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_stale_memoized_forward_is_recreated(mock_conn, _):
    """Forwards live in the ADB server and vanish with the device.

    Trusting memory made attach report success while creating no forward, so the
    client tunneled to a dead port and the device sat `offline` with no error
    anywhere. Observed on hardware.
    """
    fake = _FakeAdb()
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        device._resolve_endpoint()
        fake.forwards.clear()  # e.g. `adb forward --remove-all`, or a server restart
        _, port = device._resolve_endpoint()
        assert port in fake.forwards


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_an_unauthorized_device_is_reported_not_forwarded(mock_conn, _):
    """`offline`/`unauthorized` cannot be forwarded; say which it is."""
    fake, patcher = _fake(devices=((SERIAL, "unauthorized", USB_PORT),))
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="unauthorized"):
            device._resolve_endpoint()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_an_explicit_serial_skips_the_port_lookup(mock_conn, _):
    """`serial` is the escape hatch for hardware with no usable devpath."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(serial=SERIAL)
        device._resolve_endpoint()
    assert not [c for c in fake.calls if c[1:3] == ["devices", "-l"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_macos_location_id_devpath_matches(mock_conn, _):
    """The macOS native backend reports an IOKit location ID, not a port path.

    Decimal with a literal `X` suffix — `usb_osx.cpp` formats it `"usb:%" PRIu32 "X"`, so
    the `X` is not a hex marker. Taken from a real SM-P613 on adb 1.0.41.
    """
    fake, patcher = _fake(devices=((SERIAL, "device", "usb:538116096X"),))
    with patcher:
        device = AdbDevice(usb_port="538116096X")
        assert device._resolve_serial() == SERIAL


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_devpath_that_equals_the_serial_still_matches(mock_conn, _):
    """A documented macOS native-backend quirk.

    When the location ID cannot be read, adb sets devpath to the *serial*
    (`if (devpath.empty()) { devpath = serial; }`). Matching must still work, and must
    not select some other device.
    """
    fake, patcher = _fake(
        devices=(
            ("OTHER", "device", "usb:1-1"),
            (SERIAL, "device", f"usb:{SERIAL}"),
        )
    )
    with patcher:
        device = AdbDevice(usb_port=SERIAL)
        assert device._resolve_serial() == SERIAL


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_an_emulator_without_a_devpath_is_not_matched(mock_conn, _):
    """Emulator lines carry no `usb:` field, so they must never match a bench port."""
    fake, patcher = _fake(devices=(("emulator-5554", "device", None),))
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="no device on USB port"):
            device._resolve_serial()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_silent_forward_falls_back_to_the_forward_list(mock_conn, _):
    """Reporting the chosen port is optional in adb's protocol.

    AOSP's client prints it only when the server sends one ("Server or device may
    optionally return a resolved TCP port number"), so a server that stays silent
    still created the forward and still exits 0.
    """
    fake = _FakeAdb()
    real = fake.__call__

    def silent(argv, **kwargs):
        result = real(argv, **kwargs)
        args = argv[1:]
        if "forward" in args and "--list" not in args and "--remove" not in args:
            return MagicMock(stdout="", stderr="", returncode=0)
        return result

    with patch("subprocess.run", side_effect=silent):
        device = AdbDevice(usb_port="1-4.2")
        assert device._resolve_endpoint() == ("127.0.0.1", _FakeAdb.FIRST_PORT)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_forward_with_no_discoverable_port_is_an_error(mock_conn, _):
    """Better to fail than to hand the client a port that cannot exist."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")

    def blank(argv, **kwargs):
        args = argv[1:]
        if args[:2] == ["devices", "-l"]:
            return MagicMock(stdout=fake._devices_long(), stderr="", returncode=0)
        return MagicMock(stdout="", stderr="", returncode=0)

    with patch("subprocess.run", side_effect=blank):
        with pytest.raises(RuntimeError, match="reported no forwarded port"):
            device._resolve_endpoint()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_forward_failure_mentions_adb_tcpip(mock_conn, _):
    """A device whose adbd is not on TCP is the common failure; say what to do."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")

    def failing(argv, **kwargs):
        args = argv[1:]
        if args[:2] == ["devices", "-l"]:
            return MagicMock(stdout=fake._devices_long(), stderr="", returncode=0)
        if "forward" in args and "--list" in args:
            return MagicMock(stdout="", stderr="", returncode=0)
        if "forward" in args:
            raise subprocess.CalledProcessError(1, "adb", stderr="cannot bind")
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=failing):
        with pytest.raises(RuntimeError, match="tcpip"):
            device._resolve_endpoint()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_concurrent_streams_create_one_forward(mock_conn, _):
    """Streams are opened per client connection, and adb calls run in worker threads.

    Two concurrent resolutions must not each create a forward: the second would
    silently strand the first client's port.
    """
    fake = _FakeAdb()
    real = fake.__call__
    started = threading.Event()

    def slow(argv, **kwargs):
        # Hold the first forward-creation open long enough that the other threads are
        # definitely inside _resolve_endpoint waiting on the lock. A Barrier cannot be
        # used here: the lock means only one thread ever reaches this point, so a
        # barrier of 4 would deadlock rather than test anything.
        if "forward" in argv and "--list" not in argv and "--remove" not in argv:
            started.set()
            time.sleep(0.2)
        return real(argv, **kwargs)

    with patch("subprocess.run", new=MagicMock(side_effect=slow)):
        device = AdbDevice(usb_port="1-4.2")
        results = []
        results_lock = threading.Lock()

        def resolve():
            endpoint = device._resolve_endpoint()
            with results_lock:
                results.append(endpoint)

        threads = [threading.Thread(target=resolve) for _ in range(4)]
        for t in threads:
            t.start()
        assert started.wait(timeout=10), "no forward was ever created"
        for t in threads:
            t.join(timeout=30)

    assert len(results) == 4, f"a thread did not finish: {results}"
    assert len(set(results)) == 1, f"streams disagree on the endpoint: {results}"
    assert len(fake.forwards) == 1, f"more than one forward: {fake.forwards}"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_close_removes_the_forward_we_created(mock_conn, _):
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()
        assert device._owns_forward is True
        device.close()

    assert not fake.forwards
    assert not adb_driver._SERVERS


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_close_leaves_a_forward_adb_rebound_to_another_device(mock_conn, _):
    """Our local port can stop being ours without anything being freed.

    `install_listener` matches on the local spec, and on a hit it repurposes that listener
    in place — new transport, exit 0, silently — unless `--no-rebind` was passed. So one
    `adb -s OTHER forward tcp:<our port> tcp:5555` moves our port to another device. (The
    same end state arrives less often via the kernel reusing a freed ephemeral port for a
    later `tcp:0`.) Removal matches on the local spec too — `remove_listener` ignores the
    transport it is handed — so teardown must confirm the forward is still ours rather
    than trusting the port it remembers.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()

        fake.forwards[port] = "OTHERDEVICE99"  # `adb -s OTHERDEVICE99 forward tcp:<port> ...`
        device.close()

    assert fake.forwards == {port: "OTHERDEVICE99"}, "removed another device's forward"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_rebound_port_is_replaced_on_the_next_stream(mock_conn, _):
    """A stolen port must not keep being handed to clients.

    Nothing can stop the theft — `--no-rebind` on our own `tcp:0` is a no-op, since the
    listener is renamed to its resolved port and never matches the literal `tcp:0` again —
    so the defense is that endpoints are resolved per stream against what the server
    actually forwards for *our* serial.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, stolen = device._resolve_endpoint()

        fake.forwards[stolen] = "OTHERDEVICE99"
        _, port = device._resolve_endpoint()

        assert port != stolen, "kept handing out a port that now points at another device"
        assert fake.forwards[port] == SERIAL
        assert fake.forwards[stolen] == "OTHERDEVICE99", "clobbered the other device"
        assert device._owns_forward is True


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_close_removes_only_our_forward_when_the_device_has_several(mock_conn, _):
    """One device can hold several forwards to the same adbd port.

    Listeners are keyed by their local spec, so ours and a colleague's `adb forward
    tcp:9000 tcp:5555` coexist and adb lists both. Teardown must remove exactly the one
    it created — not the device's forwards wholesale, and not whichever adb lists first.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()

        fake.forwards[9000] = SERIAL  # someone forwards the same device by hand
        assert device._resolve_endpoint() == ("127.0.0.1", port), "lost track of our own forward"

        device.close()

    assert fake.forwards == {9000: SERIAL}, "removed a forward this driver did not create"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_close_removes_the_forward_of_an_offline_device(mock_conn, _):
    """The bench case: a relay power-cycles the DUT and teardown runs against it offline.

    An offline device keeps its transport, so its forwards survive — but every `-s`
    command is refused, because the server acquires the transport first and rejects a
    non-`device` state. Removal must not go through `-s`; `remove_listener` matches the
    local spec and never looks at the transport, so the unscoped form is both sufficient
    and the only form that works here.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()

        fake.devices = [(SERIAL, "offline", USB_PORT)]  # the relay cut power mid-lease
        device.close()

    assert not fake.forwards, "left a forward behind on an offline device"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_silent_forward_racing_another_process_is_not_guessed(mock_conn, _):
    """Two exporters can share one adopted ADB server and declare the same device.

    If a foreign forward appears between our listing and our own `forward tcp:0`, and the
    server does not name the port it chose, "which port is forwarded for this serial" has
    two answers. Picking one risks handing the client another process's forward, so the
    ambiguity is reported instead.
    """
    fake = _FakeAdb()

    def run(argv, **kwargs):
        args = argv[1:]
        if "forward" in args and "--list" not in args and "--remove" not in args:
            fake(argv, **kwargs)  # our forward lands
            fake.forwards[9000] = SERIAL  # so does the other process's
            return MagicMock(stdout="", stderr="", returncode=0)  # but the server is silent
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run):
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="does not identify one"):
            device._resolve_endpoint()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_forward_we_did_not_create_is_reused_but_never_removed(mock_conn, _):
    """`forward --list` cannot say who created a forward.

    Forwards live in the shared ADB server, so one already there may belong to another
    driver, another exporter process, or a person at the bench. Reusing it is right —
    re-forwarding per stream would churn the server — but deleting it on close broke
    its owner silently, since adb reports nothing.
    """
    fake = _FakeAdb()
    fake.forwards[9000] = SERIAL  # someone's `adb -s HVA1234567 forward tcp:9000 tcp:5555`
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        assert device._resolve_endpoint() == ("127.0.0.1", 9000)
        assert device._owns_forward is False
        device.close()

    assert fake.forwards == {9000: SERIAL}
    assert not [c for c in fake.calls if "--remove" in c]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_recreated_forward_is_ours_again(mock_conn, _):
    """Reuse must not make ownership sticky.

    A forward adopted before a power cycle is gone afterwards; the one we then create
    is ours, and has to be cleaned up.
    """
    fake = _FakeAdb()
    fake.forwards[9000] = SERIAL
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        device._resolve_endpoint()
        fake.forwards.clear()  # the device re-enumerated, taking the forward with it
        _, port = device._resolve_endpoint()

        assert device._owns_forward is True
        device.close()
    assert not fake.forwards
    assert [c for c in fake.calls if "--remove" in c] == [["/usr/bin/adb", "forward", "--remove", f"tcp:{port}"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_teardown_completes_when_forward_removal_hangs(mock_conn, _):
    """An unresponsive ADB server must not be able to wedge close()."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        _, port = device._resolve_endpoint()

    removals = []

    def run(argv, **kwargs):
        if "--remove" in argv:
            removals.append(argv)
            raise subprocess.TimeoutExpired("adb forward --remove", 30.0)
        # The forward is still live, so close() gets as far as trying to remove it.
        return fake(argv, **kwargs)

    with patch("subprocess.run", side_effect=run):
        device.close()  # must not raise

    assert removals == [["/usr/bin/adb", "forward", "--remove", f"tcp:{port}"]]
    assert device._forward_port is None


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_info_reports_presence(mock_conn, _):
    fake = _FakeAdb()
    with patch("subprocess.run", new=MagicMock(side_effect=fake)):
        device = AdbDevice(usb_port="1-4.2")
        info = device.info()
        assert info["transport"] == "usb"
        assert info["selector"] == USB_PORT
        assert info["serial"] == SERIAL
        assert info["present"] == "yes"

        fake.devices = [("OTHERDEVICE99", "device", "usb:1-1.1")]
        absent = device.info()
        assert absent["present"] == "no"
        assert "powered off" in absent["reason"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_info_takes_a_reference_to_the_server_it_uses(mock_conn, _):
    """Otherwise the adb client silently starts one that nothing accounts for.

    Observed on real hardware: `info()` ran `devices -l` before any acquire, the adb
    client auto-started a server on the port, the next acquire *adopted* it — and
    adopted servers are never killed, so `close()` left it running for good.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        device.info()

        assert device._server is not None, "info() ran adb without taking a reference"
        assert device._server.owns is True, "adopted a server this driver itself caused"

        device.close()
    assert not adb_driver._SERVERS
    assert [c for c in fake.calls if c[1:] == ["kill-server"]], "left the server running"


# ------------------------------------------------------------- transport: tcp


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_tcp_transport_connects_and_creates_no_forward(mock_conn, _):
    """adbd already listens on the DUT, so there is nothing to forward."""

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        assert device._resolve_endpoint() == ("10.0.0.5", 5555)
        argvs = [c.args[0] for c in mock_run.call_args_list]
        assert ["/usr/bin/adb", "connect", "10.0.0.5:5555"] in argvs
        assert not any("forward" in argv for argv in argvs)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_tcp_address_without_a_port_uses_adbd_port(mock_conn, _):
    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5")
        assert device._resolve_endpoint() == ("10.0.0.5", 5555)
        assert ["/usr/bin/adb", "connect", "10.0.0.5:5555"] in [c.args[0] for c in mock_run.call_args_list]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_tcp_connect_failure_is_detected_despite_exit_zero(mock_conn, _):
    """`adb connect` returns 0 on failure and reports it on stdout."""

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="failed to connect to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        with pytest.raises(RuntimeError, match="could not connect"):
            device._resolve_endpoint()


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_tcp_close_disconnects_a_device_we_connected(mock_conn, _):
    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        device._resolve_endpoint()
        device.close()
        assert ["/usr/bin/adb", "disconnect", "10.0.0.5:5555"] in [c.args[0] for c in mock_run.call_args_list]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_a_tcp_device_someone_else_connected_is_left_connected(mock_conn, _):
    """`already connected to` means the shared server had the device before us.

    Which of adb's two success strings comes back is the only evidence of who
    connected it; disconnecting on that basis dropped it out from under its owner.
    """

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="already connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        assert device._resolve_endpoint() == ("10.0.0.5", 5555)
        assert device._owns_connection is False
        device.close()

    assert not [c for c in mock_run.call_args_list if c.args[0][1:2] == ["disconnect"]]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_reconnecting_per_stream_does_not_forfeit_ownership(mock_conn, _):
    """Every stream after the first sees `already connected to`, ours included.

    `adb connect` runs per stream because it is idempotent, so ownership is decided
    once, on the reply that actually established the connection.
    """
    replies = iter(["connected to 10.0.0.5:5555\n", "already connected to 10.0.0.5:5555\n"])

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout=next(replies), stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        device._resolve_endpoint()
        device._resolve_endpoint()

        assert device._owns_connection is True
        device.close()
    assert ["/usr/bin/adb", "disconnect", "10.0.0.5:5555"] in [c.args[0] for c in mock_run.call_args_list]


# ---------------------------------------------------------- config validation


@patch("shutil.which", return_value="/usr/bin/adb")
def test_usb_needs_exactly_one_selector(_):
    with pytest.raises(ConfigurationError, match="exactly one"):
        AdbDevice()
    with pytest.raises(ConfigurationError, match="exactly one"):
        AdbDevice(usb_port="1-4.2", serial=SERIAL)


@patch("shutil.which", return_value="/usr/bin/adb")
def test_transport_field_mismatches_are_rejected(_):
    """Silently ignoring a field the transport cannot use hides a config mistake."""
    with pytest.raises(ConfigurationError, match="only applies to transport: tcp"):
        AdbDevice(usb_port="1-4.2", address="10.0.0.5")
    with pytest.raises(ConfigurationError, match="only apply to transport: usb"):
        AdbDevice(transport="tcp", address="10.0.0.5", usb_port="1-4.2")
    with pytest.raises(ConfigurationError, match="needs 'address'"):
        AdbDevice(transport="tcp")


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        ("serial", "no serial/UART transport"),
        ("uart", "no serial/UART transport"),
        ("vsock", "not implemented"),
        ("emulator", "androidemulator"),
    ],
)
@patch("shutil.which", return_value="/usr/bin/adb")
def test_unsupported_transports_say_what_to_do_instead(_, transport, expected):
    """A bare "unknown transport" sends people looking for a typo.

    Serial is the one people will reach for: adb genuinely has no UART transport, so
    the message has to point at the actual route rather than imply a spelling error.
    """
    with pytest.raises(ConfigurationError, match=expected):
        AdbDevice(transport=transport, usb_port="1-4.2")


@patch("shutil.which", return_value="/usr/bin/adb")
def test_unknown_transport_lists_the_supported_ones(_):
    with pytest.raises(ConfigurationError, match="usb/tcp"):
        AdbDevice(transport="carrier-pigeon", usb_port="1-4.2")


@pytest.mark.parametrize("bad", [0, -1, 70000, True, "5555", None])
@patch("shutil.which", return_value="/usr/bin/adb")
def test_invalid_adbd_port(_, bad):
    with pytest.raises(ConfigurationError, match="adbd_port"):
        AdbDevice(usb_port="1-4.2", adbd_port=bad)


@patch("shutil.which", return_value="/usr/bin/adb")
def test_empty_usb_port_is_rejected(_):
    with pytest.raises(ConfigurationError, match="usb_port"):
        AdbDevice(usb_port="   ")


# ------------------------------------------------- driver-side API for parent drivers
#
# The Cuttlefish driver runs its own `adb shell` to poll for `sys.boot_completed`, so it
# needs an adb environment and a device that is already in the shared server.


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_adb_env_points_at_our_server_and_holds_it(mock_conn, _):
    """Handing out the environment without taking a reference would leak a server.

    The caller's first adb call would find nothing on the port, the adb client would
    start one silently, and the next acquire would adopt it — and adopted servers are
    never killed, so it would outlive the lease.
    """
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        env = device.adb_env()

        assert env["ANDROID_ADB_SERVER_PORT"] == "15037"
        assert device._server is not None, "handed out an env without acquiring the server"
        assert device._server.owns is True

        device.close()
    assert not adb_driver._SERVERS
    assert [c for c in fake.calls if c[1:] == ["kill-server"]], "left the server running"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_ensure_reachable_connects_a_tcp_device_before_any_stream(mock_conn, _):
    """A parent driver polling for boot cannot wait for a client to open a stream."""

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        assert device.ensure_reachable() == "10.0.0.5:5555"
        assert ["/usr/bin/adb", "connect", "10.0.0.5:5555"] in [c.args[0] for c in mock_run.call_args_list]

        # Repeatable: adb connect is idempotent, and ownership is not re-decided.
        assert device.ensure_reachable() == "10.0.0.5:5555"
        assert device._owns_connection is True


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_ensure_reachable_resolves_a_usb_device_to_its_forward(mock_conn, _):
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        target = device.ensure_reachable()

    host, _, port = target.rpartition(":")
    assert host == "127.0.0.1"
    assert int(port) in fake.forwards


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_disconnect_drops_our_connection_mid_lease(mock_conn, _):
    """For a parent driver that power-cycles the device without ending the lease."""

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        device.ensure_reachable()

        device.disconnect()
        assert ["/usr/bin/adb", "disconnect", "10.0.0.5:5555"] in [c.args[0] for c in mock_run.call_args_list]

        # Idempotent, and a second call must not disconnect anything again.
        before = len(mock_run.call_args_list)
        device.disconnect()
        assert len(mock_run.call_args_list) == before


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_disconnect_leaves_a_connection_we_did_not_make(mock_conn, _):
    """`already connected to` means the shared server had it before us."""

    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="already connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        device.ensure_reachable()
        device.disconnect()

    assert not [c for c in mock_run.call_args_list if c.args[0][1:2] == ["disconnect"]]
