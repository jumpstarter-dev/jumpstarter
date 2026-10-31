import http.client
import json
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from click.testing import CliRunner

from .common import APP_TOKEN_PREFIX
from .driver import AppiumDriver
from jumpstarter.common.utils import serve

FIXED = {
    "platformName": "iOS",
    "appium:automationName": "XCUITest",
    "appium:udid": "DEVICE-1",
    "appium:prebuiltWDAPath": "/opt/wda/Runner.app",
}


def request(target, method, path, body=None, connection=None):
    parts = urlsplit(target.url)
    connection = connection or http.client.HTTPSConnection(parts.hostname, parts.port, context=target.ssl_context())
    payload = None if body is None else json.dumps(body).encode()
    connection.request(method, path, payload, {"Content-Type": "application/json"})
    response = connection.getresponse()
    return response.status, json.loads(response.read())


def session(target, always, first=None):
    capabilities = {"alwaysMatch": always, "firstMatch": first or [{}]}
    return request(target, "POST", "/session", {"capabilities": capabilities})


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def appium(fake_appium, tmp_path):
    executable, _ = fake_appium
    driver = AppiumDriver(
        executable=str(executable),
        home=str(tmp_path),
        drivers=["xcuitest"],
        capabilities=FIXED,
        env={"EXTRA": "1"},
        state_dir=str(tmp_path),
    )
    with serve(driver) as client:
        yield client


def test_info_hides_host_paths(appium):
    capabilities = appium.info()["capabilities"]
    assert capabilities["appium:udid"] == "DEVICE-1"
    assert "appium:prebuiltWDAPath" not in capabilities


def test_server_is_locked_down(appium, fake_appium, tmp_path):
    with appium.serve():
        pass
    (started,) = records(fake_appium[1])
    server = started["config"]["server"]
    assert server["deny-insecure"] == ["*:*"]
    assert server["allow-insecure"] == [] and server["relaxed-security"] is False
    assert server["use-drivers"] == ["xcuitest"] and server["use-plugins"] == []
    assert started["args"][started["args"].index("--address") + 1] == "127.0.0.1"
    env = started["env"]
    assert env["APPIUM_HOME"] == str(tmp_path) and env["EXTRA"] == "1"
    assert Path(env["HOME"]).is_relative_to(tmp_path)


def test_session_pins_fixed_capabilities(appium):
    with appium.serve() as target:
        assert target.url.startswith("https://127.0.0.1:")
        assert target.capabilities()["appium:udid"] == "DEVICE-1"
        status, result = session(
            target,
            {"platformName": "iOS", "appium:udid": "OTHER", "appium:options": {"newCommandTimeout": 30}},
            [{"appium:udid": "OTHER", "appium:language": "fr"}],
        )
    assert status == 200
    always = result["value"]["capabilities"]["capabilities"]["alwaysMatch"]
    first = result["value"]["capabilities"]["capabilities"]["firstMatch"]
    assert always["appium:udid"] == "DEVICE-1"
    assert always["appium:prebuiltWDAPath"] == "/opt/wda/Runner.app"
    assert always["appium:newCommandTimeout"] == 30
    assert first == [{"appium:language": "fr"}]


@pytest.mark.parametrize(
    "capabilities",
    [
        {"appium:app": "https://example.com/app.ipa"},
        {"appium:app": "/etc/passwd"},
        {"appium:otherApps": '["/tmp/a.app"]'},
        {"appium:chromedriverExecutable": "/bin/sh"},
        {"appium:options": {"mjpegScreenshotUrl": "http://169.254.169.254/"}},
        {"proxy": {"proxyType": "manual"}},
    ],
)
def test_session_rejects_host_access(appium, capabilities):
    with appium.serve() as target:
        status, result = session(target, capabilities)
    assert status == 400
    assert result["value"]["error"] == "invalid argument"


def test_commands_reject_remote_uploads(appium):
    with appium.serve() as target:
        status, result = request(
            target,
            "POST",
            "/session/s1/appium/stop_recording_screen",
            {"options": {"remotePath": "https://example.com/upload"}},
        )
        assert status == 400 and "remotePath" in result["value"]["message"]
        status, result = request(target, "POST", "/session/s1/execute/sync", {"script": "mobile: tap", "args": [{}]})
        assert status == 200 and result["value"]["body"]["script"] == "mobile: tap"


def test_uploaded_apps_resolve_on_the_exporter(appium, tmp_path):
    app = tmp_path / "Demo.ipa"
    app.write_bytes(b"ipa-bytes")
    token = appium.upload_app(app)
    assert token.startswith(APP_TOKEN_PREFIX) and token.endswith("/Demo.ipa")
    bundle = tmp_path / "Other.app"
    (bundle / "Contents").mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_text("plist")
    other = appium.upload_app(bundle)
    assert other.endswith("/Other.app.zip")
    with appium.serve() as target:
        status, result = session(target, {"appium:app": token, "appium:otherApps": json.dumps([other])})
        assert status == 200
        always = result["value"]["capabilities"]["capabilities"]["alwaysMatch"]
        assert Path(always["appium:app"]).read_bytes() == b"ipa-bytes"
        with zipfile.ZipFile(always["appium:otherApps"][0]) as archive:
            assert archive.read("Other.app/Contents/Info.plist") == b"plist"
        status, result = request(
            target, "POST", "/session/s1/execute/sync", {"script": "mobile: installApp", "args": [{"app": token}]}
        )
        assert status == 200 and result["value"]["body"]["args"][0]["app"] == always["appium:app"]
        status, result = session(target, {"appium:app": APP_TOKEN_PREFIX + "missing/x.ipa"})
        assert status == 400


def test_upload_rejects_unsupported_names(appium, tmp_path):
    script = tmp_path / "run.sh"
    script.write_text("echo")
    with pytest.raises(ValueError, match="plain names"):
        appium.upload_app(script)


def test_connections_are_reused_and_stream_large_responses(appium):
    with appium.serve() as target:
        parts = urlsplit(target.url)
        connection = http.client.HTTPSConnection(parts.hostname, parts.port, context=target.ssl_context())
        assert request(target, "GET", "/status", connection=connection)[0] == 200
        connection.request("GET", "/big")
        response = connection.getresponse()
        assert len(response.read()) == 3 * 1024 * 1024
        assert request(target, "GET", "/sessions", connection=connection)[1]["value"]["path"] == "/sessions"
        connection.close()


def test_stop_starts_a_fresh_server(appium, fake_appium):
    with appium.serve() as target:
        assert request(target, "GET", "/status")[0] == 200
    appium.stop()
    with appium.serve() as target:
        assert request(target, "GET", "/status")[0] == 200
    assert len(records(fake_appium[1])) == 2


def test_startup_failure_is_reported(fake_appium, monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_APPIUM_FAIL", "1")
    driver = AppiumDriver(executable=str(fake_appium[0]), state_dir=str(tmp_path))
    with serve(driver) as client, pytest.raises(Exception, match="exited before it was ready"):
        client.info()


def test_missing_launcher_is_reported(tmp_path):
    driver = AppiumDriver(executable="definitely-not-appium", state_dir=str(tmp_path))
    with serve(driver) as client, pytest.raises(Exception, match="not installed"):
        client.info()


def test_cli_runs_command_with_target_environment(appium):
    check = (
        "import json, os, sys; "
        "caps = json.loads(os.environ['JUMPSTARTER_APPIUM_CAPABILITIES']); "
        "ok = os.environ['JUMPSTARTER_APPIUM_URL'].startswith('https://127.0.0.1:') "
        "and caps['appium:udid'] == 'DEVICE-1' "
        "and os.environ['NODE_EXTRA_CA_CERTS'] == os.environ['JUMPSTARTER_APPIUM_CA_FILE']; "
        "sys.exit(0 if ok else 3)"
    )
    result = CliRunner().invoke(appium.cli(), ["--", sys.executable, "-c", check], env={"NODE_EXTRA_CA_CERTS": ""})
    assert result.exit_code == 0, result.output


def test_cli_uploads(appium, tmp_path, capsys):
    app = tmp_path / "Demo.apk"
    app.write_bytes(b"apk")
    with pytest.raises(SystemExit) as exited:
        appium.cli()(["upload", str(app)])
    assert exited.value.code == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith(APP_TOKEN_PREFIX)
