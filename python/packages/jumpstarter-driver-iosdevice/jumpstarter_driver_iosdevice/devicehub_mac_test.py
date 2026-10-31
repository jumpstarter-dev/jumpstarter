import plistlib

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
