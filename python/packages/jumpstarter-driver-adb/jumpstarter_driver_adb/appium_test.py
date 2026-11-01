import http.client
import json
import sys
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner
from jumpstarter_driver_appium.testing import write_fake_appium

from .appium import AdbDeviceAppium
from .driver import AdbDevice
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

SERIAL = "R58M123ABC"


@pytest.fixture
def appium(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(tmp_path / "record.jsonl"))
    return write_fake_appium(tmp_path)


@pytest.fixture(autouse=True)
def adb(monkeypatch):
    """No adb on the test host: the device is present, and the shared server is a stand-in."""
    monkeypatch.setattr("jumpstarter_driver_adb.driver._resolve_adb_path", lambda path, timeout: "/usr/bin/adb")
    monkeypatch.setattr(AdbDevice, "adb_env", MagicMock(return_value={}))
    present = {"transport": "usb", "present": "yes", "serial": SERIAL}
    monkeypatch.setattr(AdbDevice, "info", MagicMock(return_value=present))


def new_session(target, always):
    parts = urlsplit(target.url)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, context=target.ssl_context())
    connection.request("POST", "/session", json.dumps({"capabilities": {"alwaysMatch": always}}))
    response = connection.getresponse()
    return response.status, json.loads(response.read())


@pytest.mark.parametrize("config", [{"wda_port": 8100}, {"relaxed_security": True}, "appium"])
def test_appium_configuration_rejected_early(config):
    with pytest.raises(ConfigurationError):
        AdbDevice(serial=SERIAL, appium=config)


def test_appium_child_only_when_configured():
    assert "appium" not in AdbDevice(serial=SERIAL).children
    child = AdbDevice(serial=SERIAL, appium={}).children["appium"]
    assert isinstance(child, AdbDeviceAppium) and child.drivers == ["uiautomator2"]


def test_client_without_appium_explains():
    with serve(AdbDevice(serial=SERIAL)) as client, pytest.raises(AttributeError, match="not configured"):
        _ = client.appium


def test_sessions_are_pinned_to_the_device_and_shared_server(appium, tmp_path):
    driver = AdbDevice(usb_port="1-4.2", server_port=15999, appium={"executable": str(appium)})
    with serve(driver) as client, client.appium.serve() as target:
        assert target.capabilities()["appium:udid"] == SERIAL
        status, result = new_session(target, {"appium:udid": "other", "appium:suppressKillServer": False})
        assert status == 200
        always = result["value"]["capabilities"]["capabilities"]["alwaysMatch"]
        assert always["platformName"] == "Android" and always["appium:automationName"] == "UiAutomator2"
        assert always["appium:udid"] == SERIAL
        assert always["appium:adbPort"] == 15999 and always["appium:suppressKillServer"] is True
        for denied in ({"appium:avd": "Pixel"}, {"appium:avdArgs": "-writable-system"}, {"appium:remoteAdbHost": "x"}):
            status, result = new_session(target, denied)
            assert status == 400 and result["value"]["error"] == "invalid argument"
    (started,) = [json.loads(line) for line in (tmp_path / "record.jsonl").read_text().splitlines()]
    assert started["env"]["ANDROID_ADB_SERVER_PORT"] == "15999"
    assert started["config"]["server"]["use-drivers"] == ["uiautomator2"]


def test_tcp_devices_use_their_adb_address(appium):
    driver = AdbDevice(transport="tcp", address="10.0.0.5", appium={"executable": str(appium)})
    with patch.object(AdbDevice, "_resolve_endpoint", return_value=("10.0.0.5", 5555)), serve(driver) as client:
        assert client.appium.info()["capabilities"]["appium:udid"] == "10.0.0.5:5555"


def test_absent_device_does_not_start_appium(appium, monkeypatch):
    absent = {"transport": "usb", "present": "no", "reason": "no device on usb:1-4.2"}
    monkeypatch.setattr(AdbDevice, "info", MagicMock(return_value=absent))
    driver = AdbDevice(usb_port="1-4.2", appium={"executable": str(appium)})
    with serve(driver) as client, pytest.raises(Exception, match="no device on usb:1-4.2"):
        client.appium.info()


def test_appium_stops_before_the_adb_server_is_released(appium):
    driver = AdbDevice(serial=SERIAL, appium={"executable": str(appium)})
    child = driver.children["appium"]
    order = []
    with (
        patch.object(child, "close", side_effect=lambda: order.append("appium")),
        patch("jumpstarter_driver_adb.driver._release_server", side_effect=lambda *a, **k: order.append("server")),
    ):
        driver._server = MagicMock()
        driver.close()
    assert order[:2] == ["appium", "server"]


def test_adb_cli_hands_commands_to_appium(appium):
    check = "import os, sys; sys.exit(0 if os.environ['JUMPSTARTER_APPIUM_URL'].startswith('https://') else 3)"
    driver = AdbDevice(serial=SERIAL, appium={"executable": str(appium)})
    with patch.dict("os.environ", {"NODE_EXTRA_CA_CERTS": ""}), serve(driver) as client:
        result = CliRunner().invoke(client.cli(), ["appium", "--", sys.executable, "-c", check])
    assert result.exit_code == 0, result.output
