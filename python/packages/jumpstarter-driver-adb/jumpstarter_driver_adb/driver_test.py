import subprocess
from unittest.mock import MagicMock, patch

import pytest

from .driver import AdbServer
from jumpstarter.common.exceptions import ConfigurationError


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
