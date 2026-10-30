from unittest.mock import MagicMock, call, patch

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
    mock_proc.poll.return_value = None
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
    mock_proc.poll.return_value = None
    mock_proc.wait = MagicMock(side_effect=[TimeoutExpired(cmd="emulator", timeout=15), -9])
    power._process = mock_proc

    with patch("subprocess.run") as mock_off_run:
        mock_off_run.return_value = _mock_adb_ok()
        power.off()

    assert power._process is None
    mock_proc.kill.assert_called_once()
    assert mock_proc.wait.call_args_list == [call(timeout=15), call(timeout=5)]


@patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator")
@patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run", return_value=_mock_adb_ok())
def test_custom_ports(mock_run, mock_adb_which, mock_emu_which):
    emu = AndroidEmulator(avd_name="test_avd", console_port=5556, adb_server_port=15038)
    assert emu.console_port == 5556
    assert emu.adb_server_port == 15038
    adb_child: AdbServer = emu.children["adb"]  # ty: ignore[invalid-assignment]
    assert adb_child.port == 15038


@pytest.fixture
def power():
    parent = MagicMock()
    parent.emulator_path = "/usr/bin/emulator"
    parent.avd_name = "test_avd"
    parent.console_port = 5554
    parent.adb_server_port = 15037
    parent.headless = True
    return AndroidEmulatorPower(parent=parent)


def test_power_on_restarts_exited_process(power):
    previous = MagicMock()
    previous.poll.return_value = 1
    power._process = previous
    threads = [MagicMock(), MagicMock()]
    for thread in threads:
        thread.is_alive.return_value = False
    power._log_thread, power._stderr_thread = threads
    with patch("subprocess.Popen") as popen, patch("threading.Thread"):
        power.on()
    popen.assert_called_once()
    previous.wait.assert_called_once_with(timeout=5)
    previous.stdin.close.assert_called_once()
    for thread in threads:
        thread.join.assert_called_once_with(timeout=2)


def test_power_on_ignores_running_process(power):
    process = MagicMock()
    process.returncode = None
    process.poll.return_value = None
    power._process = process
    with patch("subprocess.Popen") as popen:
        power.on()
    popen.assert_not_called()
    assert power._process is process


def test_power_off_cleans_exited_process_once(power):
    process = MagicMock()
    process.poll.return_value = 1
    power._process = process
    threads = [MagicMock(), MagicMock()]
    for thread in threads:
        thread.is_alive.return_value = False
    power._log_thread, power._stderr_thread = threads
    with patch("subprocess.run") as run:
        power.off()
        power.off()
        power.close()
    run.assert_not_called()
    process.wait.assert_called_once_with(timeout=5)
    process.kill.assert_not_called()
    process.stdin.close.assert_called_once()
    for thread in threads:
        thread.join.assert_called_once_with(timeout=2)
    assert power._process is power._log_thread is power._stderr_thread is None


@pytest.mark.parametrize("error", [TimeoutError, FileNotFoundError, ProcessLookupError])
def test_power_off_reaps_after_adb_failure(power, error):
    from subprocess import TimeoutExpired

    process = MagicMock()
    process.returncode = None
    process.poll.return_value = None
    power._process = process
    failure = TimeoutExpired("adb", 5) if error is TimeoutError else FileNotFoundError()
    if error is ProcessLookupError:
        process.kill.side_effect = ProcessLookupError()
    with patch("subprocess.run", side_effect=failure):
        power.off()
    process.kill.assert_called_once()
    process.wait.assert_called_once_with(timeout=5)
    assert power._process is None


@pytest.mark.parametrize("exited", [False, True])
def test_power_off_reaps_real_process_and_joins_log_threads(power, exited):
    import subprocess
    import sys

    process = subprocess.Popen(
        [sys.executable, "-c", "pass" if exited else "import time; time.sleep(60)"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        with patch("subprocess.Popen", return_value=process):
            power.on()
        threads = [power._log_thread, power._stderr_thread]
        if exited:
            process.wait(timeout=5)
        with patch("subprocess.run", side_effect=FileNotFoundError):
            power.off()
        assert process.returncode is not None
        assert process.stdin is not None and process.stdin.closed
        assert process.stdout is not None and process.stdout.closed
        assert process.stderr is not None and process.stderr.closed
        assert all(thread is not None and not thread.is_alive() for thread in threads)
        assert power._process is power._log_thread is power._stderr_thread is None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        power.close()


@pytest.mark.parametrize("operation", ["kill", "wait"])
def test_power_off_preserves_failure_and_closes_stdin(power, operation):
    process = MagicMock()
    process.poll.return_value = None
    failure = OSError("cannot reap emulator")
    getattr(process, operation).side_effect = failure
    process.stdin.close.side_effect = BrokenPipeError("stdin already broken")
    power._process = process
    with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(OSError) as exc:
        power.off()
    assert exc.value is failure
    process.stdin.close.assert_called_once()
    assert power._process is process
    getattr(process, operation).side_effect = None
    process.poll.return_value = 1
    power.close()
    assert power._process is None


def test_power_off_preserves_process_after_reap_timeout(power):
    from subprocess import TimeoutExpired

    process = MagicMock()
    process.poll.return_value = None
    failure = TimeoutExpired("emulator", 5)
    process.wait.side_effect = failure
    power._process = process
    with patch("subprocess.run", side_effect=FileNotFoundError), pytest.raises(TimeoutExpired) as exc:
        power.off()
    assert exc.value is failure
    process.wait.assert_called_once_with(timeout=5)
    process.stdin.close.assert_called_once()
    assert power._process is process


def test_power_on_waits_for_old_log_threads_before_restart(power):
    process = MagicMock()
    process.poll.return_value = 1
    power._process = process
    thread = MagicMock()
    thread.is_alive.return_value = True
    power._log_thread = thread
    with patch("subprocess.Popen") as popen, pytest.raises(TimeoutError, match="log thread"):
        power.on()
    popen.assert_not_called()
    thread.join.assert_called_once_with(timeout=2)
    assert power._process is process
    assert power._log_thread is thread
    thread.is_alive.return_value = False
    power.close()
    power.close()
    assert power._process is power._log_thread is None


@pytest.mark.parametrize("failure_point", ["kill", "wait", "reap_timeout", "reader"])
def test_close_logs_failure_and_continues_sibling_cleanup(power, caplog, failure_point):
    from subprocess import TimeoutExpired

    from jumpstarter.driver import Driver

    process = MagicMock()
    process.poll.return_value = None
    power._process = process
    reader = MagicMock()
    reader.is_alive.return_value = False
    power._log_thread = reader
    sibling = MagicMock()
    parent = power.parent
    parent.children = {"power": power, "sibling": sibling}
    failure = OSError("cannot stop emulator")
    if failure_point == "kill":
        process.kill.side_effect = failure
    elif failure_point == "wait":
        process.wait.side_effect = failure
    elif failure_point == "reap_timeout":
        failure = TimeoutExpired("emulator", 5)
        process.wait.side_effect = failure
    else:
        process.poll.return_value = 1
        reader.is_alive.return_value = True

    with caplog.at_level("ERROR", logger=power.logger.name), patch("subprocess.run", side_effect=FileNotFoundError):
        Driver.close(parent)
    sibling.close.assert_called_once()
    assert power._process is process
    assert power._log_thread is reader
    errors = [record for record in caplog.records if record.exc_info is not None]
    assert len(errors) == 1
    assert errors[0].message == "Failed to stop emulator during close"
    assert errors[0].exc_info is not None
    if failure_point == "reader":
        assert isinstance(errors[0].exc_info[1], TimeoutError)
        with patch("subprocess.Popen") as popen, pytest.raises(TimeoutError):
            power.on()
    else:
        assert errors[0].exc_info[1] is failure
        with patch("subprocess.Popen") as popen:
            power.on()
    popen.assert_not_called()

    process.poll.return_value = 1
    process.wait.side_effect = None
    process.kill.side_effect = None
    reader.is_alive.return_value = False
    Driver.close(parent)
    assert sibling.close.call_count == 2
    assert power._process is power._log_thread is None


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit()])
def test_close_does_not_swallow_base_exception(power, caplog, failure):
    with patch.object(power, "off", side_effect=failure), pytest.raises(type(failure)) as exc:
        power.close()
    assert exc.value is failure
    assert not caplog.records
