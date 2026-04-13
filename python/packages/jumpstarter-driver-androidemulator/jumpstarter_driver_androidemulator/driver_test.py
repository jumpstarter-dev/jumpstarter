from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_adb.driver import AdbServer

from .driver import AndroidEmulator, AndroidEmulatorPower
from jumpstarter.common.exceptions import ConfigurationError


def _mock_adb_ok():
    return MagicMock(stdout="ok", stderr="", returncode=0)


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_registers_children(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd")
    assert "adb" in emu.children
    assert "power" in emu.children
    assert emu.emulator_path == "/usr/bin/emulator"
    assert emu.avd_name == "test_avd"
    assert emu.console_port == 5554
    assert emu.adb_server_port == 15037


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value=None)
def test_init_missing_emulator(_):
    with pytest.raises(ConfigurationError, match="not found"):
        AndroidEmulator(avd_name="test_avd")


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_init_invalid_port(mock_run, mock_adb_which, mock_emu_which):
    with pytest.raises(ConfigurationError, match="Invalid console_port"):
        AndroidEmulator(avd_name="test_avd", console_port=-1)


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_power_on_builds_cmdline(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd", console_port=5556)
    power: AndroidEmulatorPower = emu.children["power"]  # ty: ignore[invalid-assignment]

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stdout.readline = MagicMock(return_value=b"")
        mock_proc.stderr.readline = MagicMock(return_value=b"")
        mock_popen.return_value = mock_proc

        power.on()

        cmdline = mock_popen.call_args[0][0]
        assert cmdline[0] == "/usr/bin/emulator"
        assert "-avd" in cmdline
        assert "test_avd" in cmdline
        assert "-port" in cmdline
        assert "5556" in cmdline
        assert "-no-window" in cmdline
        assert "-no-audio" in cmdline
        assert "-skip-adb-auth" in cmdline

        env = mock_popen.call_args[1]["env"]
        assert env["ANDROID_ADB_SERVER_PORT"] == "15037"


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_power_on_not_headless(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd", headless=False)
    power: AndroidEmulatorPower = emu.children["power"]  # ty: ignore[invalid-assignment]

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stdout.readline = MagicMock(return_value=b"")
        mock_proc.stderr.readline = MagicMock(return_value=b"")
        mock_popen.return_value = mock_proc

        power.on()
        cmdline = mock_popen.call_args[0][0]
        assert "-no-window" not in cmdline


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_power_off_graceful(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd")
    power: AndroidEmulatorPower = emu.children["power"]  # ty: ignore[invalid-assignment]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.wait = MagicMock(return_value=0)
    power._process = mock_proc

    with patch("subprocess.run") as mock_off_run:
        mock_off_run.return_value = _mock_adb_ok()
        power.off()

    assert power._process is None
    mock_proc.wait.assert_called_once_with(timeout=15)


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_power_off_force_kill(mock_run, mock_adb_which, mock_emu_which):
    from subprocess import TimeoutExpired

    emu = AndroidEmulator(avd_name="test_avd")
    power: AndroidEmulatorPower = emu.children["power"]  # ty: ignore[invalid-assignment]

    mock_proc = MagicMock()
    mock_proc.returncode = None
    mock_proc.wait = MagicMock(side_effect=TimeoutExpired(cmd="emulator", timeout=15))
    power._process = mock_proc

    with patch("subprocess.run") as mock_off_run:
        mock_off_run.return_value = _mock_adb_ok()
        power.off()

    assert power._process is None
    mock_proc.kill.assert_called_once()


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_custom_ports(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd", console_port=5556, adb_server_port=15038)
    assert emu.console_port == 5556
    assert emu.adb_server_port == 15038
    adb_child: AdbServer = emu.children["adb"]  # ty: ignore[invalid-assignment]
    assert adb_child.port == 15038
