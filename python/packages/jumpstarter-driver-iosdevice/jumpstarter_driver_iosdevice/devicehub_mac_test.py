import plistlib
import sys
from pathlib import Path

import anyio
import pytest

from . import devicehub_mac


def _xcode(folder, version, *, device_hub):
    app = folder / f"Xcode-{version}.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/version.plist").write_bytes(plistlib.dumps({"CFBundleShortVersionString": version}))
    if device_hub:
        (app / devicehub_mac.DEVICE_HUB_APP).mkdir(parents=True)
    return app


@pytest.fixture
def mac(tmp_path, monkeypatch):
    media = tmp_path / "CoreDeviceMediaStreamSupport.framework"
    media.mkdir()
    monkeypatch.setattr(devicehub_mac, "MEDIA_STREAM_SUPPORT", media)
    apps = []
    monkeypatch.setattr(devicehub_mac, "_xcode_apps", lambda: apps)
    return tmp_path, apps, media


def test_xcode_27_with_device_hub_is_quiet(mac):
    folder, apps, _ = mac
    apps.append(_xcode(folder, "27.1", device_hub=True))
    assert devicehub_mac.xcode_warning() is None


def test_older_xcode_is_named(mac):
    folder, apps, _ = mac
    apps.extend([_xcode(folder, "9.4", device_hub=False), _xcode(folder, "26.4", device_hub=False)])
    assert devicehub_mac.xcode_warning() == (
        "Device Hub needs Xcode 27 or later, but this Mac has Xcode 26.4. "
        "The device will not appear until a newer Xcode is installed."
    )


def test_missing_xcode(mac):
    assert "this Mac has no Xcode" in (devicehub_mac.xcode_warning() or "")


def test_unfinished_install(mac):
    folder, apps, media = mac
    apps.append(_xcode(folder, "27.0", device_hub=True))
    media.rmdir()
    assert "Open Xcode once" in (devicehub_mac.xcode_warning() or "")


def test_xcode_apps_include_selected_and_installed(tmp_path, monkeypatch):
    selected = _xcode(tmp_path / "selected", "27.1", device_hub=True)
    installed = _xcode(tmp_path, "26.4", device_hub=False)

    def run(command, **_kwargs):
        assert command == ["xcode-select", "-p"]
        return type("Result", (), {"stdout": f"{selected}/Contents/Developer\n"})()

    monkeypatch.setattr(devicehub_mac.subprocess, "run", run)
    monkeypatch.setattr(devicehub_mac, "XCODE_FOLDERS", (tmp_path,))
    assert devicehub_mac._xcode_apps() == [selected, installed]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _fake_pair(monkeypatch, *, connect_before_code: bool):
    async def pair(client, code, *, timeout):
        if connect_before_code:
            client.connected.set()
        entered = await code()
        # A newly paired Mac connects before pairing returns.
        client.connected.set()
        await anyio.sleep(0.05)
        client.codes.append(entered)

    monkeypatch.setattr(devicehub_mac, "pair", pair)


class _Client:
    def __init__(self):
        self.connected = anyio.Event()
        self.codes = []


@pytest.mark.anyio
async def test_pairing_finishes_when_the_new_pairing_connects(monkeypatch):
    _fake_pair(monkeypatch, connect_before_code=False)
    client = _Client()

    async def code():
        return "123456"

    assert await devicehub_mac._pair_unless_connected(client, code, client.connected) is True
    assert client.codes == ["123456"]


@pytest.mark.anyio
async def test_pairing_abandoned_when_still_paired_mac_connects(monkeypatch):
    _fake_pair(monkeypatch, connect_before_code=True)
    client = _Client()

    async def code():
        await anyio.sleep_forever()

    assert await devicehub_mac._pair_unless_connected(client, code, client.connected) is False
    assert client.codes == []


FAKE_XCODEBUILD = """
import pathlib, signal, sys, time
marker = pathlib.Path(sys.argv[1])
mode = sys.argv[2]
print("Testing started", flush=True)
if mode == "automation":
    print("Failed to initialize for UI testing: Timed out while enabling automation mode.", flush=True)
    sys.exit(65)
if mode == "silent":
    print("something else went wrong", flush=True)
    sys.exit(65)
def stop(*_):
    marker.write_text(" ".join(sys.argv[3:]))
    sys.exit(0)
signal.signal(signal.SIGINT, stop)
print("ServerURLHere->http://192.168.1.79:8100<-ServerURLHere", flush=True)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def fake_wda(tmp_path, monkeypatch):
    script = tmp_path / "xcodebuild.py"
    script.write_text(FAKE_XCODEBUILD)
    marker = tmp_path / "stopped"
    record = {
        "hardwareProperties": {"udid": "DEVICE"},
        "connectionProperties": {"tunnelIPAddress": "fd00::1"},
        "deviceProperties": {"osVersionNumber": "27.0.1"},
    }
    monkeypatch.setattr(devicehub_mac, "coredevice", lambda udids: record if "DEVICE" in udids else None)
    monkeypatch.setattr(devicehub_mac, "_wda_ready", lambda url: True)
    monkeypatch.setattr(devicehub_mac, "_connect", lambda udid: None)

    def use(mode):
        monkeypatch.setattr(devicehub_mac, "XCODEBUILD", (sys.executable, str(script), str(marker), mode))

    return use, marker


@pytest.mark.anyio
async def test_webdriveragent_runs_over_the_tunnel_and_stops(fake_wda):
    use, marker = fake_wda
    use("ok")
    xctestrun = Path("WebDriverAgentRunner.xctestrun")
    async with devicehub_mac.webdriveragent(xctestrun, udids=["STAND-IN", "DEVICE"], notify=lambda _: None) as agent:
        # The tunnel address, not the Wi-Fi address WebDriverAgent prints.
        assert agent.url == "http://[fd00::1]:8100"
        assert agent.udid == "DEVICE"
        assert agent.platform_version == "27.0.1"
        assert not marker.exists()
    assert marker.read_text() == "-xctestrun WebDriverAgentRunner.xctestrun -destination id=DEVICE"


@pytest.mark.anyio
async def test_webdriveragent_explains_refused_automation(fake_wda):
    use, _ = fake_wda
    use("automation")
    with pytest.raises(RuntimeError, match="Enable UI Automation"):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            pass


@pytest.mark.anyio
async def test_webdriveragent_reports_xcodebuild_output(fake_wda):
    use, _ = fake_wda
    use("silent")
    with pytest.raises(RuntimeError, match="something else went wrong"):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            pass


@pytest.mark.anyio
async def test_webdriveragent_keeps_caller_errors(fake_wda):
    use, marker = fake_wda
    use("ok")
    with pytest.raises(KeyError):
        async with devicehub_mac.webdriveragent(Path("x.xctestrun"), udids=["DEVICE"], notify=lambda _: None):
            raise KeyError("from the test")
    assert marker.exists()


def test_webdriveragent_capabilities_avoid_usbmux():
    agent = devicehub_mac.WebDriverAgent(url="http://[fd00::1]:8100", udid="DEVICE", platform_version="27.0.1")
    capabilities = agent.capabilities()
    assert capabilities["appium:webDriverAgentUrl"] == "http://[fd00::1]:8100"
    assert capabilities["appium:platformVersion"] == "27.0.1"
    assert capabilities["appium:skipLogCapture"] is True
    assert "appium:bundleId" not in capabilities
