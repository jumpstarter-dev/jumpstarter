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

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        args = argv[1:]

        if args[:1] == ["version"]:
            return MagicMock(stdout="Android Debug Bridge version 1.0.41", stderr="", returncode=0)
        if args[:2] == ["devices", "-l"]:
            return MagicMock(stdout=self._devices_long(), stderr="", returncode=0)
        if args[:1] == ["devices"]:
            return MagicMock(stdout="List of devices attached\n", stderr="", returncode=0)

        if "forward" in args:
            serial = args[args.index("-s") + 1] if "-s" in args else ""
            if "--list" in args:
                lines = "".join(f"{s} tcp:{p} tcp:5555\n" for p, s in self.forwards.items())
                return MagicMock(stdout=lines, stderr="", returncode=0)
            if "--remove-all" in args:
                self.forwards = {p: s for p, s in self.forwards.items() if s != serial}
                return MagicMock(stdout="", stderr="", returncode=0)
            if "--remove" in args:
                self.forwards.pop(int(args[-1].removeprefix("tcp:")), None)
                return MagicMock(stdout="", stderr="", returncode=0)
            local = int(args[-2].removeprefix("tcp:"))
            if local == 0:
                local = self._next_port
                self._next_port += 1
            self.forwards[local] = serial
            # Real adb prints the chosen port, and only that, for tcp:0.
            return MagicMock(stdout=f"{local}\n", stderr="", returncode=0)

        return MagicMock(stdout="ok", stderr="", returncode=0)


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
# and sharing one between drivers is a correctness requirement, not an optimisation.


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
@patch("socket.create_connection")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_a_server_we_started_is_killed_on_close(mock_run, mock_conn, _):
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


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_start_server_survives_a_hung_port(mock_conn, _):
    """`adb start-server` blocks forever on a non-ADB listener; bound it."""

    def run(argv, **kwargs):
        if argv[1:] == ["start-server"]:
            assert kwargs.get("timeout"), "start-server must be bounded"
            raise subprocess.TimeoutExpired("adb start-server", 30.0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        server = AdbServer()  # must not hang or raise
    assert server.port == 15037


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
    """The registry is keyed per (adb_path, port)."""
    fake, patcher = _fake()
    with patcher:
        a = AdbDevice(usb_port="1-4.2", server_port=15037)
        b = AdbDevice(usb_port="1-4.3", server_port=15038)
        a._ensure_server()
        b._ensure_server()
        assert a._server is not b._server
        assert len([c for c in fake.calls if c[1:] == ["start-server"]]) == 2


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
    """A DUT powered off by its relay is normal, not a crash — but say so clearly."""
    fake, patcher = _fake(devices=())
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        with pytest.raises(RuntimeError, match="no device on USB port usb:1-4.2"):
            device._resolve_endpoint()


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
    client tunnelled to a dead port and the device sat `offline` with no error
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
def test_a_macos_hex_devpath_matches(mock_conn, _):
    """The macOS native backend reports an IOKit location ID, not a port path."""
    fake, patcher = _fake(devices=((SERIAL, "device", "usb:1A320000"),))
    with patcher:
        device = AdbDevice(usb_port="1A320000")
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
def test_close_removes_the_forward_and_releases_the_server(mock_conn, _):
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        device._resolve_endpoint()
        assert fake.forwards
        device.close()
        assert not fake.forwards
        assert not adb_driver._SERVERS


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("socket.create_connection", side_effect=OSError("refused"))
def test_teardown_completes_when_forward_removal_hangs(mock_conn, _):
    """An unresponsive ADB server must not be able to wedge close()."""
    fake, patcher = _fake()
    with patcher:
        device = AdbDevice(usb_port="1-4.2")
        device._resolve_endpoint()

    def run(argv, **kwargs):
        if "--remove" in argv:
            raise subprocess.TimeoutExpired("adb forward --remove", 30.0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run):
        device.close()  # must not raise
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

        fake.devices = []
        absent = device.info()
        assert absent["present"] == "no"
        assert "powered off" in absent["reason"]


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
def test_tcp_close_disconnects(mock_conn, _):
    def run(argv, **kwargs):
        if argv[1:2] == ["connect"]:
            return MagicMock(stdout="connected to 10.0.0.5:5555\n", stderr="", returncode=0)
        return _mock_adb_ok()

    with patch("subprocess.run", side_effect=run) as mock_run:
        device = AdbDevice(transport="tcp", address="10.0.0.5:5555")
        device._resolve_endpoint()
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
