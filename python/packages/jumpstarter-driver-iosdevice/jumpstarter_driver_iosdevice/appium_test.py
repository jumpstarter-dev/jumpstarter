import http.client
import json
import math
import socket
import sys
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import anyio
import pytest
from click.testing import CliRunner
from jumpstarter_driver_appium.testing import write_fake_appium

from .appium import IosDeviceAppium
from .driver import IosDevice
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

UDID = "00008110-001234567890001E"
PRESENT = {"present": True, "reason": None, "udid": UDID, "os_version": "27.0.1"}


class EchoPort:
    """A device TCP port that answers every chunk with ``wda:`` + the chunk."""

    def __init__(self):
        self._send, self._receive = anyio.create_memory_object_stream[bytes](math.inf)

    async def send(self, data: bytes) -> None:
        await self._send.send(b"wda:" + data)

    async def receive(self, max_bytes: int = 65536) -> bytes:
        return await self._receive.receive()


@pytest.fixture
def appium(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(tmp_path / "record.jsonl"))
    return write_fake_appium(tmp_path)


@pytest.fixture
def device_calls(monkeypatch):
    calls = []

    @asynccontextmanager
    async def connect_device(udid, port):
        calls.append((udid, port))
        yield EchoPort()

    class Daemon:
        pass

    daemon = Daemon()
    daemon.connect_device = connect_device  # ty: ignore[unresolved-attribute]
    monkeypatch.setattr(IosDevice, "_daemon", lambda self: daemon)
    monkeypatch.setattr(IosDevice, "info", AsyncMock(return_value=PRESENT))
    return calls


def new_session(target, always):
    parts = urlsplit(target.url)
    connection = http.client.HTTPSConnection(parts.hostname, parts.port, context=target.ssl_context())
    body = json.dumps({"capabilities": {"alwaysMatch": always}})
    connection.request("POST", "/session", body, {"Content-Type": "application/json"})
    response = connection.getresponse()
    assert response.status == 200
    return json.loads(response.read())["value"]["capabilities"]["capabilities"]["alwaysMatch"]


@pytest.mark.parametrize(
    "config", [{"wda_port": 0}, {"wda_port": "8100"}, {"wda_port": True}, {"relaxed_security": True}, "appium"]
)
def test_appium_configuration_rejected_early(config):
    with pytest.raises(ConfigurationError):
        IosDevice(udid=UDID, appium=config)


def test_appium_child_only_when_configured():
    assert "appium" not in IosDevice(udid=UDID).children
    child = IosDevice(udid=UDID, appium={}).children["appium"]
    assert isinstance(child, IosDeviceAppium) and child.drivers == ["xcuitest"]


def test_client_without_appium_explains(device_calls):
    with serve(IosDevice(udid=UDID)) as client, pytest.raises(AttributeError, match="not configured"):
        _ = client.appium


def test_sessions_attach_to_running_wda_through_usbmux(appium, device_calls, tmp_path):
    config = {"executable": str(appium), "wda_port": 8100, "capabilities": {"appium:wdaLaunchTimeout": 60000}}
    with serve(IosDevice(udid=UDID, appium=config)) as client, client.appium.serve() as target:
        public = target.capabilities()
        assert public["appium:udid"] == UDID and public["appium:platformVersion"] == "27.0.1"
        assert "appium:webDriverAgentUrl" not in public
        always = new_session(target, {"appium:udid": "OTHER", "appium:webDriverAgentUrl": "http://evil"})
        assert always["appium:udid"] == UDID and always["appium:wdaLaunchTimeout"] == 60000
        wda = urlsplit(always["appium:webDriverAgentUrl"])
        assert wda.hostname == "127.0.0.1"
        with socket.create_connection((wda.hostname, wda.port), timeout=5) as connection:
            connection.sendall(b"GET /status")
            assert connection.recv(64) == b"wda:GET /status"
    assert device_calls == [(UDID, 8100)]
    (started,) = [json.loads(line) for line in (tmp_path / "record.jsonl").read_text().splitlines()]
    assert started["env"]["USBMUXD_SOCKET_ADDRESS"] == "UNIX:/var/run/usbmuxd"
    with pytest.raises(OSError):  # The forward closed with the session.
        socket.create_connection((wda.hostname, wda.port), timeout=2)


def test_operator_capabilities_launch_wda_without_a_forward(appium, device_calls):
    config = {
        "executable": str(appium),
        "capabilities": {"appium:usePreinstalledWDA": True, "appium:updatedWDABundleId": "dev.example.WDA"},
    }
    driver = IosDevice(transport="network", udid=UDID, usbmux_host="usbmux.lab", appium=config)
    with serve(driver) as client, client.appium.serve() as target:
        always = new_session(target, {})
        assert always["appium:updatedWDABundleId"] == "dev.example.WDA"
        assert "appium:webDriverAgentUrl" not in always
    child = driver.children["appium"]
    assert isinstance(child, IosDeviceAppium)
    assert child.server_environment()["USBMUXD_SOCKET_ADDRESS"] == "usbmux.lab:27015"
    assert device_calls == []


def test_absent_device_does_not_start_appium(appium, monkeypatch):
    absent = {"present": False, "reason": "device is not present in usbmuxd", "udid": UDID, "os_version": None}
    monkeypatch.setattr(IosDevice, "info", AsyncMock(return_value=absent))
    driver = IosDevice(udid=UDID, appium={"executable": str(appium)})
    with serve(driver) as client, pytest.raises(Exception, match="not present in usbmuxd"):
        client.appium.info()


def test_ios_cli_hands_commands_to_appium(appium, device_calls):
    check = "import os, sys; sys.exit(0 if os.environ['JUMPSTARTER_APPIUM_URL'].startswith('https://') else 3)"
    driver = IosDevice(udid=UDID, appium={"executable": str(appium)})
    with patch.dict("os.environ", {"NODE_EXTRA_CA_CERTS": ""}), serve(driver) as client:
        result = CliRunner().invoke(client.cli(), ["appium", "--", sys.executable, "-c", check])
    assert result.exit_code == 0, result.output
