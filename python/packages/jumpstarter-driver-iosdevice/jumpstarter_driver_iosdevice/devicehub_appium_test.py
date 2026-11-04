import json
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import pytest
from jumpstarter_driver_appium.policy import PolicyError, Reply

from . import devicehub_appium
from .devicehub_appium import Applications, Devicectl, DeviceHubPolicy, bundle_id, gateway

FIXED = {"platformName": "iOS", "appium:udid": "DEVICE", "appium:webDriverAgentUrl": "http://[fd00::1]:8100"}


def make_app(directory: Path, identifier: str = "com.example.App", name: str = "App.app") -> Path:
    app = directory / name
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": identifier}, fmt=plistlib.FMT_BINARY))
    (app / "App").write_bytes(b"binary")
    (app / "App").chmod(0o755)
    return app


def archive(app: Path, path: Path, prefix: str = "") -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for item in app.rglob("*"):
            bundle.write(item, f"{prefix}{app.name}/{item.relative_to(app)}")
    return path


def session(first=None, **capabilities) -> bytes:
    return json.dumps({"capabilities": {"alwaysMatch": capabilities, "firstMatch": first or [{}]}}).encode()


class FakeDevice(Devicectl):
    """Devicectl with the device kept in memory; archives unpack with zipfile."""

    def __init__(self, apps=("com.example.Installed",)):
        super().__init__("FAKE")
        self.apps = set(apps)
        self.events: list[tuple[str, str]] = []

    @asynccontextmanager
    async def unpacked(self, path):
        if path.is_dir():
            yield path
            return
        with tempfile.TemporaryDirectory() as directory:
            with zipfile.ZipFile(path) as bundle:
                bundle.extractall(directory)
            yield devicehub_appium._bundle(Path(directory))

    async def installed(self, identifier):
        return identifier in self.apps

    async def install(self, app):
        identifier = bundle_id(app)
        self.events.append(("install", identifier))
        self.apps.add(identifier)

    async def uninstall(self, identifier):
        self.events.append(("uninstall", identifier))
        self.apps.discard(identifier)


@pytest.fixture
def device():
    return FakeDevice()


@pytest.fixture
def policy(device):
    return DeviceHubPolicy(FIXED, Applications(), device)


def handle(policy, path: str, body: bytes):
    return anyio.run(policy.handle, "POST", path, body)


def always_of(policy, **capabilities):
    return json.loads(handle(policy, "/session", session(**capabilities)))["capabilities"]["alwaysMatch"]


def test_only_ios_applications_are_offered(tmp_path):
    applications = Applications()
    with pytest.raises(ValueError, match="does not exist"):
        applications.add(tmp_path / "missing.ipa")
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"apk")
    with pytest.raises(ValueError, match="not an iOS application"):
        applications.add(apk)
    not_a_bundle = tmp_path / "file.app"
    not_a_bundle.write_bytes(b"")
    with pytest.raises(ValueError, match="not an iOS application"):
        applications.add(not_a_bundle)
    token = applications.add(make_app(tmp_path))
    assert token.startswith("jumpstarter-app:") and token.endswith("/App.app")
    assert applications.path(token) == tmp_path / "App.app"
    with pytest.raises(PolicyError, match="Unknown application upload"):
        applications.path("jumpstarter-app:0/other.ipa")


def test_session_app_is_installed_and_launched_by_bundle(policy, device, tmp_path):
    token = policy.applications.add(make_app(tmp_path))
    always = always_of(policy, **{"appium:app": token, "appium:udid": "OTHER"})
    assert "appium:app" not in always
    assert always["appium:bundleId"] == "com.example.App"
    # The target's fixed capabilities still win, as on the exporter.
    assert always["appium:udid"] == "DEVICE"
    assert device.events == [("install", "com.example.App")]


@pytest.mark.parametrize(
    "capabilities,events",
    [
        # XCUITest's default: an installed copy is removed, so the app starts with fresh data.
        ({}, [("uninstall", "com.example.Installed"), ("install", "com.example.Installed")]),
        ({"appium:noReset": True}, []),
        ({"appium:options": {"enforceAppInstall": False}}, [("install", "com.example.Installed")]),
        # Appium reads the strings "true" and "false" as booleans.
        ({"appium:noReset": "true"}, []),
        ({"appium:noReset": "false", "appium:enforceAppInstall": "false"}, [("install", "com.example.Installed")]),
    ],
)
def test_installed_apps_are_reset_as_xcuitest_does(policy, device, tmp_path, capabilities, events):
    token = policy.applications.add(make_app(tmp_path, "com.example.Installed"))
    always = always_of(policy, **{"appium:app": token, **capabilities})
    assert always["appium:bundleId"] == "com.example.Installed"
    assert device.events == events


def test_apps_in_options_and_other_apps_are_installed(policy, device, tmp_path):
    first = policy.applications.add(make_app(tmp_path / "a", "com.example.A"))
    second = policy.applications.add(archive(make_app(tmp_path / "b", "com.example.B"), tmp_path / "B.ipa", "Payload/"))
    always = always_of(policy, **{"appium:options": {"app": first, "otherApps": json.dumps([second])}})
    assert always["appium:bundleId"] == "com.example.A"
    assert "appium:app" not in always and "appium:otherApps" not in always and "appium:options" not in always
    assert device.events == [("install", "com.example.A"), ("install", "com.example.B")]
    # Other applications are installed afresh whatever the reset options, as XCUITest does.
    always_of(policy, **{"appium:otherApps": second, "appium:noReset": True})
    assert device.events[2:] == [("uninstall", "com.example.B"), ("install", "com.example.B")]


def test_a_single_first_match_is_part_of_the_session(policy, device, tmp_path):
    token = policy.applications.add(make_app(tmp_path))
    body = session(first=[{"platformName": "iOS", "appium:app": token, "appium:noReset": True}])
    forwarded = json.loads(handle(policy, "/session", body))["capabilities"]
    assert forwarded["alwaysMatch"]["appium:bundleId"] == "com.example.App"
    assert forwarded["firstMatch"] == [{}]
    assert device.events == [("install", "com.example.App")]


def test_bundle_ids_given_anywhere_must_match_the_app(policy, device, tmp_path):
    token = policy.applications.add(make_app(tmp_path))
    matching = [
        {"appium:options": {"app": token}, "appium:bundleId": "com.example.App"},
        {"appium:app": token, "appium:options": {"bundleId": "com.example.App"}},
    ]
    for capabilities in matching:
        assert always_of(policy, **capabilities)["appium:bundleId"] == "com.example.App"
    body = session(first=[{"appium:bundleId": "com.example.App"}], **{"appium:app": token})
    forwarded = json.loads(handle(policy, "/session", body))["capabilities"]
    assert forwarded["alwaysMatch"]["appium:bundleId"] == "com.example.App"
    assert forwarded["firstMatch"] == [{}]
    events = list(device.events)
    for body in (
        session(**{"appium:app": token, "appium:bundleId": "com.other"}),
        session(first=[{"appium:bundleId": "com.other"}], **{"appium:app": token}),
        session(first=[{"appium:bundleId": "com.other"}, {}], **{"appium:app": token}),
        session(**{"appium:app": token, "appium:bundleId": ["com.example.App"]}),
    ):
        with pytest.raises(PolicyError, match="is not the bundle of appium:app"):
            handle(policy, "/session", body)
    assert device.events == events  # Rejected before anything was installed.


def test_rejected_sessions_change_nothing_on_the_device(policy, device, tmp_path):
    token = policy.applications.add(make_app(tmp_path))
    for capabilities in (
        {"appium:app": token, "appium:chromedriverExecutable": "/bin/sh"},
        {"appium:app": str(tmp_path / "App.app")},
        {"appium:app": token, "appium:resetLocationService": True},
    ):
        with pytest.raises(PolicyError):
            handle(policy, "/session", session(**capabilities))
    with pytest.raises(PolicyError, match="several firstMatch alternatives"):
        handle(policy, "/session", session(first=[{"appium:app": token}, {}]))
    assert device.events == []


def test_capabilities_needing_usbmux_are_ignored_or_rejected(policy):
    body = {"appium:fullReset": True, "appium:options": {"usePreinstalledWDA": True}, "appium:mjpegServerPort": 9100}
    always = always_of(policy, **body)
    assert not {"appium:fullReset", "appium:usePreinstalledWDA", "appium:mjpegServerPort"} & set(always)
    for name in ("appium:resetLocationService", "appium:customSSLCert"):
        with pytest.raises(PolicyError, match="not available on this Jumpstarter target"):
            handle(policy, "/session", session(**{name: True}))


def test_bundle_ids_pass_through_and_no_reset_is_not_pinned(policy, device):
    always = always_of(policy, **{"appium:bundleId": "com.apple.Preferences"})
    assert always["appium:bundleId"] == "com.apple.Preferences"
    assert "appium:noReset" not in always
    assert device.events == []


@pytest.mark.parametrize(
    "path,body",
    [
        ("/session/s1/appium/device/install_app", {"appPath": "TOKEN"}),
        ("/session/s1/execute/sync", {"script": "mobile: installApp", "args": [{"app": "TOKEN"}, {"ignored": 1}]}),
        ("/session/s1/execute", {"script": "mobile: installApp", "args": {"app": "TOKEN"}}),
    ],
)
def test_install_commands_are_answered_by_the_gateway(policy, device, tmp_path, path, body):
    token = policy.applications.add(archive(make_app(tmp_path), tmp_path / "App.ipa", "Payload/"))
    body = json.loads(json.dumps(body).replace("TOKEN", token))
    assert handle(policy, path, json.dumps(body).encode()) == Reply(200, {"value": None})
    assert device.events == [("install", "com.example.App")]
    with pytest.raises(PolicyError, match="remotePath"):
        handle(policy, path, json.dumps({**body, "remotePath": "http://example.com"}).encode())
    assert len(device.events) == 1


@pytest.mark.parametrize(
    "path,body",
    [
        ("/session/s1/appium/device/remove_app", {"appId": "BUNDLE"}),
        ("/session/s1/execute/sync", {"script": "mobile: removeApp", "args": [{"bundleId": "BUNDLE"}]}),
    ],
)
def test_remove_commands_uninstall_with_devicectl(policy, device, path, body):
    def remove(bundle):
        return handle(policy, path, json.dumps(body).replace("BUNDLE", bundle).encode())

    assert remove("com.example.Installed") == Reply(200, {"value": True})
    assert remove("com.example.Installed") == Reply(200, {"value": False})
    with pytest.raises(PolicyError, match="bundle identifier"):
        handle(policy, path, json.dumps(body).replace('"BUNDLE"', "1").encode())


def test_failed_removal_answers_false_like_xcuitest(policy, device, monkeypatch):
    async def refuse(identifier):
        raise RuntimeError("devicectl uninstall app failed: system app")

    monkeypatch.setattr(device, "uninstall", refuse)
    body = json.dumps({"appId": "com.example.Installed"}).encode()
    assert handle(policy, "/session/s1/appium/device/remove_app", body) == Reply(200, {"value": False})


def test_kill_app_stops_the_application_through_webdriveragent(policy):
    body = json.dumps({"script": "mobile: killApp", "args": [{"bundleId": "com.apple.Preferences"}]}).encode()
    forwarded = json.loads(handle(policy, "/session/s1/execute/sync", body))
    assert forwarded == {"script": "mobile: terminateApp", "args": [{"bundleId": "com.apple.Preferences"}]}


def test_other_commands_reach_appium(policy):
    body = json.dumps({"script": "mobile: activateApp", "args": [{"bundleId": "com.apple.Preferences"}]}).encode()
    assert json.loads(handle(policy, "/session/s1/execute/sync", body))["script"] == "mobile: activateApp"
    assert anyio.run(policy.handle, "GET", "/status", b"") == b""


def test_malformed_bodies_are_rejected_as_on_the_exporter(policy):
    with pytest.raises(PolicyError, match="nested too deeply"):
        handle(policy, "/session/s1/execute/sync", b"[" * 200000 + b"]" * 200000)
    with pytest.raises(PolicyError, match="must be JSON"):
        handle(policy, "/session", b"{not json")


def test_failures_are_webdriver_errors(policy, device, tmp_path, monkeypatch):
    async def broken(app):
        raise OSError("disk full")

    monkeypatch.setattr(device, "install", broken)
    token = policy.applications.add(make_app(tmp_path))
    reply = handle(policy, "/session", session(**{"appium:app": token}))
    assert reply.status == 500 and reply.document["value"]["message"] == "disk full"


@pytest.fixture
def fake_appium_url(tmp_path, monkeypatch):
    from jumpstarter_driver_appium.local import local_appium
    from jumpstarter_driver_appium.testing import write_fake_appium

    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(tmp_path / "record.jsonl"))
    with local_appium(str(write_fake_appium(tmp_path))) as url:
        yield url


def test_one_failed_connection_does_not_stop_the_gateway(policy, monkeypatch, fake_appium_url):
    async def explode(method, path, body):
        if path == "/explode":
            raise KeyError("boom")
        return body

    monkeypatch.setattr(policy, "handle", explode)

    async def get(port, path):
        async with await anyio.connect_tcp("127.0.0.1", port) as stream:
            await stream.send(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            try:
                return await stream.receive()
            except (anyio.EndOfStream, anyio.BrokenResourceError):
                return b""

    async def main():
        async with gateway(fake_appium_url, policy) as url:
            port = int(url.rsplit(":", 1)[1])
            assert await get(port, "/explode") == b""
            assert b'"ready": true' in await get(port, "/status")

    anyio.run(main)


def test_a_client_resetting_before_accept_does_not_stop_the_gateway(policy, fake_appium_url):
    import socket
    import struct

    async def main():
        async with gateway(fake_appium_url, policy) as url:
            port = int(url.rsplit(":", 1)[1])
            for _ in range(3):
                # Connect, then reset at once (SO_LINGER 0) before the gateway accepts.
                abrupt = socket.create_connection(("127.0.0.1", port))
                abrupt.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                abrupt.sendall(b"GET /status HTTP/1.1\r\n\r\n")
                abrupt.close()
            await anyio.sleep(0.1)
            async with await anyio.connect_tcp("127.0.0.1", port) as stream:
                await stream.send(b"GET /status HTTP/1.1\r\nHost: x\r\n\r\n")
                assert b'"ready": true' in await stream.receive()

    anyio.run(main)


def fake_devicectl(monkeypatch, answers):
    """Record commands; answer each with the next ``(status, stdout, stderr)``."""
    calls = []

    async def run_process(command, check):
        calls.append(command)
        status, stdout, stderr = answers.pop(0)
        return subprocess.CompletedProcess(command, status, stdout, stderr)

    monkeypatch.setattr(devicehub_appium.anyio, "run_process", run_process)
    return calls


def test_devicectl_commands(monkeypatch, tmp_path):
    listed = json.dumps({"result": {"apps": [{"bundleIdentifier": "com.example.App"}]}}).encode()
    calls = fake_devicectl(monkeypatch, [(0, listed, b""), (0, b"", b""), (0, b"", b""), (1, b"", b"locked")])
    device = Devicectl("UDID-1")
    assert anyio.run(device.installed, "com.example.App") is True
    anyio.run(device.install, tmp_path)
    anyio.run(device.uninstall, "com.example.App")
    with pytest.raises(RuntimeError, match="devicectl uninstall app failed: locked"):
        anyio.run(device.uninstall, "com.example.App")
    commands = [command[3:5] for command in calls]
    assert commands == [["info", "apps"], ["install", "app"], ["uninstall", "app"], ["uninstall", "app"]]
    assert calls[0][-2:] == ["--json-output", "-"] and calls[0][calls[0].index("--device") + 1] == "UDID-1"
    for answer in ((0, b"{}", b""), (0, b"not json", b"")):
        fake_devicectl(monkeypatch, [answer])
        with pytest.raises(RuntimeError, match="unexpected answer"):
            anyio.run(device.installed, "com.example.App")

    async def missing(command, check):
        raise FileNotFoundError("xcrun")

    monkeypatch.setattr(devicehub_appium.anyio, "run_process", missing)
    with pytest.raises(RuntimeError, match="Could not run devicectl"):
        anyio.run(device.installed, "com.example.App")


def test_unpacking_reports_bad_archives(monkeypatch, tmp_path):
    calls = fake_devicectl(monkeypatch, [(1, b"", b"ditto: Couldn't read PKZip signature")])
    broken = tmp_path / "broken.ipa"
    broken.write_bytes(b"not a zip")

    async def unpack(path):
        async with Devicectl("UDID").unpacked(path):
            pass

    with pytest.raises(RuntimeError, match="Could not unpack broken.ipa: ditto: Couldn't read PKZip signature"):
        anyio.run(unpack, broken)
    assert calls[0][:4] == ["ditto", "-x", "-k", str(broken)]
    assert not Path(calls[0][4]).exists()  # The scratch directory is removed.


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("ditto") is None, reason="ditto is part of macOS")
@pytest.mark.parametrize("prefix", ["Payload/", ""])
def test_unpacked_bundles_keep_their_executables(tmp_path, prefix):
    app = make_app(tmp_path / "build", "com.example.Packed")
    source = archive(app, tmp_path / "App.ipa", prefix)

    async def unpack(path):
        async with Devicectl("UDID").unpacked(path) as bundle:
            return bundle_id(bundle), (bundle / "App").stat().st_mode & 0o111

    assert anyio.run(unpack, source) == ("com.example.Packed", 0o111)
    no_app = tmp_path / "readme.zip"
    with zipfile.ZipFile(no_app, "w") as bundle:
        bundle.writestr("README", "not an app")
    with pytest.raises(RuntimeError, match="Expected one .app bundle in the archive, found 0"):
        anyio.run(unpack, no_app)
