import subprocess
from unittest.mock import MagicMock, patch

import pytest

from .driver import AdbServer
from jumpstarter.common.exceptions import ConfigurationError


class _FakeAdb:
    """Minimal stand-in for the adb binary that remembers `forward` state.

    A single canned return value cannot model reconciliation: `forward --list`
    has to report what earlier `forward` calls created, or every attach looks
    stale. This tracks just enough for that.
    """

    def __init__(self):
        self.forwards = {}  # local port -> serial
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if "forward" in argv:
            serial = argv[argv.index("-s") + 1] if "-s" in argv else ""
            if "--list" in argv:
                lines = "".join(f"{s} tcp:{p} tcp:5555\n" for p, s in self.forwards.items())
                return MagicMock(stdout=lines, stderr="", returncode=0)
            if "--remove-all" in argv:
                self.forwards = {p: s for p, s in self.forwards.items() if s != serial}
            elif "--remove" in argv:
                port = int(argv[-1].removeprefix("tcp:"))
                self.forwards.pop(port, None)
            else:
                local = int(argv[-2].removeprefix("tcp:"))
                self.forwards[local] = serial
        return MagicMock(stdout="ok", stderr="", returncode=0)


def _mock_adb_ok():
    """Returns a mock that handles version check + auto-start during __post_init__."""
    return MagicMock(stdout="ok", stderr="", returncode=0)


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_validates_adb(mock_run, mock_which):
    server = AdbServer()
    assert server.adb_path == "/usr/bin/adb"
    assert server.port == 15037
    # Should have called: version check + start-server (auto-start)
    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0][0][0] == ["/usr/bin/adb", "version"]
    assert mock_run.call_args_list[1][0][0] == ["/usr/bin/adb", "start-server"]


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
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_start_server(mock_run, _):
    server = AdbServer()
    mock_run.reset_mock()
    port = server.start_server()
    assert port == 15037
    call_args = mock_run.call_args_list[0]
    assert call_args[0][0] == ["/usr/bin/adb", "start-server"]
    assert call_args[1]["env"]["ANDROID_ADB_SERVER_PORT"] == "15037"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_kill_server(mock_run, _):
    server = AdbServer()
    mock_run.reset_mock()
    port = server.kill_server()
    assert port == 15037
    call_args = mock_run.call_args_list[0]
    assert call_args[0][0] == ["/usr/bin/adb", "kill-server"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_list_devices(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server (auto-start)
        MagicMock(stdout="List of devices attached\nHVA1234567\tdevice\n", stderr="", returncode=0),
    ]
    server = AdbServer()
    output = server.list_devices()
    assert "HVA1234567" in output


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_custom_port(mock_run, _):
    server = AdbServer(port=5038)
    assert server.port == 5038


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_no_auto_connect(mock_run, _):
    AdbServer()
    assert mock_run.call_count == 2  # version + start-server only


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_connect_device(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
        MagicMock(stdout="connected to 10.0.0.1:6520\n", stderr="", returncode=0),
    ]
    server = AdbServer()
    mock_run.reset_mock()
    # reset_mock() does not clear side_effect; clear it so return_value is used.
    mock_run.side_effect = None
    mock_run.return_value = MagicMock(stdout="connected to 10.0.0.2:6520\n", stderr="", returncode=0)
    result = server.connect_device("10.0.0.2:6520")
    assert result == "connected to 10.0.0.2:6520"
    assert mock_run.call_args[0][0] == ["/usr/bin/adb", "connect", "10.0.0.2:6520"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_connect_device_error(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
    ]
    server = AdbServer()
    mock_run.side_effect = subprocess.CalledProcessError(1, "adb connect")
    with pytest.raises(subprocess.CalledProcessError):
        server.connect_device("bad:99")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_connect_device_timeout(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
    ]
    server = AdbServer()
    mock_run.side_effect = subprocess.TimeoutExpired("adb connect", 30.0)
    with pytest.raises(TimeoutError):
        server.connect_device("bad:99")
    assert mock_run.call_args[0][0] == ["/usr/bin/adb", "connect", "bad:99"]
    assert mock_run.call_args[1]["timeout"] == server.connect_timeout


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_disconnect_device(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
    ]
    server = AdbServer()
    mock_run.side_effect = None
    mock_run.return_value = MagicMock(stdout="disconnected 10.0.0.1:6520\n", stderr="", returncode=0)
    result = server.disconnect_device("10.0.0.1:6520")
    assert "disconnected" in result
    assert mock_run.call_args[0][0] == ["/usr/bin/adb", "disconnect", "10.0.0.1:6520"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_disconnect_device_error(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
    ]
    server = AdbServer()
    mock_run.side_effect = subprocess.CalledProcessError(1, "adb disconnect")
    with pytest.raises(subprocess.CalledProcessError):
        server.disconnect_device("bad:99")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_disconnect_device_timeout(mock_run, _):
    mock_run.side_effect = [
        _mock_adb_ok(),  # version check
        _mock_adb_ok(),  # start-server
    ]
    server = AdbServer()
    mock_run.side_effect = subprocess.TimeoutExpired("adb disconnect", 30.0)
    with pytest.raises(TimeoutError):
        server.disconnect_device("bad:99")
    assert mock_run.call_args[0][0] == ["/usr/bin/adb", "disconnect", "bad:99"]
    assert mock_run.call_args[1]["timeout"] == server.connect_timeout


# ------------------------------------------------------------------ attaching
#
# Attaching adds a device to an ADB server the CLIENT owns, via `adb connect`.
# That is additive, unlike addressing our server, so it works when the client
# does not own its server -- the Android Studio case.


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", new_callable=lambda: MagicMock(side_effect=_FakeAdb()))
def test_slots_exist_but_are_empty_at_startup(mock_run, _):
    """Slots must pre-exist (children are fixed at lease start) but forward nothing."""
    server = AdbServer(attach_slots=3)
    assert sorted(k for k in server.children if k.startswith("slot")) == ["slot0", "slot1", "slot2"]
    assert server.list_attached() == {}
    # `list_attached` legitimately runs `forward --list`; what matters is that no
    # forward was CREATED at startup.
    assert not any(
        "forward" in c.args[0] and "--list" not in c.args[0] for c in mock_run.call_args_list
    )


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_slot_children_bind_sequential_ports(mock_run, _):
    server = AdbServer(attach_slots=2, attach_base_port=16000)
    assert (server.children["slot0"].host, server.children["slot0"].port) == ("127.0.0.1", 16000)
    assert server.children["slot1"].port == 16001


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_attach_forwards_adbd_and_returns_a_slot_name(mock_run, _):
    server = AdbServer()
    assert server.attach_device("HVA1234567") == "slot0"
    argv = mock_run.call_args_list[-1].args[0]
    assert argv == ["/usr/bin/adb", "-s", "HVA1234567", "forward", "tcp:16000", "tcp:5555"]


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", new_callable=lambda: MagicMock(side_effect=_FakeAdb()))
def test_attach_is_idempotent(mock_run, _):
    server = AdbServer()
    assert server.attach_device("HVA1234567") == server.attach_device("HVA1234567") == "slot0"
    assert server.list_attached() == {"16000": "HVA1234567"}


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", new_callable=lambda: MagicMock(side_effect=_FakeAdb()))
def test_attaching_any_serial_works_without_declaration(mock_run, _):
    """Hotplug: an emulator or phone that appeared after startup needs no config."""
    server = AdbServer()
    assert server.attach_device("emulator-5554") == "slot0"
    assert server.attach_device("10.0.0.5:5555") == "slot1"
    assert server.list_attached() == {"16000": "emulator-5554", "16001": "10.0.0.5:5555"}


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_custom_adbd_port(mock_run, _):
    server = AdbServer()
    server.attach_device("HVA1234567", adbd_port=5556)
    assert mock_run.call_args_list[-1].args[0][-1] == "tcp:5556"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", new_callable=lambda: MagicMock(side_effect=_FakeAdb()))
def test_slot_exhaustion_is_actionable(mock_run, _):
    server = AdbServer(attach_slots=1)
    server.attach_device("a")
    with pytest.raises(RuntimeError, match="attach_slots"):
        server.attach_device("b")


@patch("shutil.which", return_value="/usr/bin/adb")
def test_attach_failure_mentions_adb_tcpip(mock_which):
    """A device whose adbd is not on TCP is the common failure; say what to do."""
    with patch("subprocess.run", return_value=_mock_adb_ok()):
        server = AdbServer()
    with (
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "adb", stderr="cannot bind")),
        pytest.raises(RuntimeError, match="adb tcpip"),
    ):
        server.attach_device("HVA1234567")


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", new_callable=lambda: MagicMock(side_effect=_FakeAdb()))
def test_detach_frees_the_slot_for_reuse(mock_run, _):
    server = AdbServer(attach_slots=1)
    server.attach_device("a")
    server.detach_device("a")
    assert server.list_attached() == {}
    assert any(
        c.args[0][3:] == ["forward", "--remove", "tcp:16000"] for c in mock_run.call_args_list
    )
    # The freed slot must be reusable, or long sessions leak slots.
    assert server.attach_device("b") == "slot0"


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_detach_unknown_device_is_a_noop(mock_run, _):
    AdbServer().detach_device("never-attached")  # must not raise


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_adbd_port_arriving_as_a_float_is_coerced(mock_run, _):
    """gRPC carries numbers as doubles: 5555 arrives as 5555.0, and adb rejects
    `tcp:5555.0`. Found on hardware before this was fixed."""
    server = AdbServer()
    server.attach_device("HVA1234567", adbd_port=5555.0)
    assert mock_run.call_args_list[-1].args[0][-1] == "tcp:5555"


# ------------------------------------------- reconciliation with the ADB server
#
# `adb forward` state lives in the ADB server, not in this driver. Anything that
# restarts the server, or runs `forward --remove-all` / `adb usb`, invalidates our
# bookkeeping. Trusting memory made attach report success while creating no
# forward, so the client tunnelled to a dead port and the device sat `offline`
# with no error reported anywhere. Found on hardware.


def _forward_list(*lines):
    return MagicMock(stdout="".join(f"{line}\n" for line in lines), stderr="", returncode=0)


@patch("shutil.which", return_value="/usr/bin/adb")
def test_stale_slot_is_reclaimed_and_the_forward_recreated(mock_which):
    with patch("subprocess.run", return_value=_mock_adb_ok()):
        server = AdbServer()
        server.attach_device("HVA1234567")  # slot0 recorded in memory
    # The ADB server no longer has that forward (e.g. `forward --remove-all`).
    with patch("subprocess.run", side_effect=[_forward_list(), _mock_adb_ok()]) as mock_run:
        assert server.attach_device("HVA1234567") == "slot0"
        # The critical assertion: a forward was actually (re)created, not skipped.
        assert mock_run.call_args_list[-1].args[0][3:] == ["forward", "tcp:16000", "tcp:5555"]


@patch("shutil.which", return_value="/usr/bin/adb")
def test_live_forward_is_not_recreated(mock_which):
    """Genuine idempotency still holds when ADB agrees the forward exists."""
    with patch("subprocess.run", return_value=_mock_adb_ok()):
        server = AdbServer()
        server.attach_device("HVA1234567")
    with patch("subprocess.run", return_value=_forward_list("HVA1234567 tcp:16000 tcp:5555")) as mock_run:
        assert server.attach_device("HVA1234567") == "slot0"
        # Only the --list call; no new forward.
        assert all("forward" not in c.args[0] or "--list" in c.args[0] for c in mock_run.call_args_list)


@patch("shutil.which", return_value="/usr/bin/adb")
def test_list_attached_hides_dead_forwards(mock_which):
    with patch("subprocess.run", return_value=_mock_adb_ok()):
        server = AdbServer()
        server.attach_device("HVA1234567")
    with patch("subprocess.run", return_value=_forward_list()):
        assert server.list_attached() == {}
    with patch("subprocess.run", return_value=_forward_list("HVA1234567 tcp:16000 tcp:5555")):
        # Keys are strings: gRPC maps cannot have integer keys.
        assert server.list_attached() == {"16000": "HVA1234567"}


@patch("shutil.which", return_value="/usr/bin/adb")
def test_a_reclaimed_slot_can_serve_a_different_device(mock_which):
    """Otherwise a single stale entry permanently burns a slot."""
    with patch("subprocess.run", return_value=_mock_adb_ok()):
        server = AdbServer(attach_slots=1)
        server.attach_device("old-device")
    with patch("subprocess.run", side_effect=[_forward_list(), _mock_adb_ok()]):
        assert server.attach_device("new-device") == "slot0"
