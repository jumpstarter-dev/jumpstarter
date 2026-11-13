from unittest.mock import MagicMock, patch

import pytest

from . import driver as driver_module
from .appium import AndroidEmulatorAppium
from .driver import AndroidEmulator
from jumpstarter.common.exceptions import ConfigurationError


@pytest.fixture(autouse=True)
def tools():
    with (
        patch("jumpstarter_driver_androidemulator.driver.shutil.which", return_value="/usr/bin/emulator"),
        patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb"),
        patch("subprocess.run", return_value=MagicMock(stdout="ok", stderr="", returncode=0)),
    ):
        yield


def appium_child(emulator) -> AndroidEmulatorAppium:
    child = emulator.children["appium"]
    assert isinstance(child, AndroidEmulatorAppium)
    return child


def test_appium_child_only_when_configured():
    assert "appium" not in AndroidEmulator(avd_name="test_avd").children
    # Rejected before the shared ADB server is referenced, which nothing would release.
    with patch.object(driver_module, "AdbServer") as server, pytest.raises(ConfigurationError):
        AndroidEmulator(avd_name="test_avd", appium={"wda": "/opt/WDA.app"})
    server.assert_not_called()


def test_appium_is_pinned_to_the_running_emulator():
    emulator = AndroidEmulator(avd_name="test_avd", console_port=5556, adb_server_port=15040, appium={})
    child = appium_child(emulator)
    with pytest.raises(RuntimeError, match="power it on"):
        child.prepare()
    process = MagicMock()
    process.poll.return_value = None
    emulator.children["power"]._process = process  # ty: ignore[unresolved-attribute]
    child.prepare()
    fixed = child.fixed_capabilities()
    assert fixed["appium:udid"] == "emulator-5556"
    assert fixed["appium:adbPort"] == 15040 and fixed["appium:suppressKillServer"] is True
    assert child.server_environment()["ANDROID_ADB_SERVER_PORT"] == "15040"
    process.poll.return_value = 0
    with pytest.raises(RuntimeError, match="power it on"):
        child.prepare()


def test_power_off_stops_appium():
    emulator = AndroidEmulator(avd_name="test_avd", appium={})
    child = appium_child(emulator)
    process = MagicMock()
    process.returncode = None
    emulator.children["power"]._process = process  # ty: ignore[unresolved-attribute]
    with patch.object(child, "stop_server") as stop:
        emulator.children["power"].off()  # ty: ignore[unresolved-attribute]
    stop.assert_called_once_with()
