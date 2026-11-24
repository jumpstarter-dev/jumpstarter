import http.client
import json
import socket
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
from jumpstarter_driver_appium.testing import write_fake_appium

from . import driver as module
from .appium import IosSimulatorAppium
from .driver import IosSimulator
from .driver_test import DEVICE_TYPE, RUNTIME, FakeSimctl
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

REAL_POPEN = subprocess.Popen


@pytest.fixture
def state():
    # Keep UDS paths short on macOS; pytest's own temp root can exceed sun_path.
    with tempfile.TemporaryDirectory(prefix="jsios-test-", dir="/tmp") as value:
        yield Path(value).resolve()


@pytest.fixture
def wda(tmp_path):
    path = tmp_path / "WebDriverAgentRunner-Runner.app"
    path.mkdir()
    return path


@pytest.fixture
def appium(tmp_path):
    return write_fake_appium(tmp_path)


@pytest.fixture
def fake(monkeypatch, tmp_path, appium):
    fake = FakeSimctl()
    monkeypatch.setenv("FAKE_APPIUM_RECORD", str(tmp_path / "record.jsonl"))

    def popen(args, **kwargs):
        # The real stand-in Appium runs; idb_companion stays simulated.
        return REAL_POPEN(args, **kwargs) if args[0] == str(appium) else fake.popen(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake.run)
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(IosSimulator, "_socket_ready", lambda _: fake.ready)
    monkeypatch.setattr(module, "_processes_using", lambda _: [])
    return fake


@pytest.fixture
def simulator(state, fake, wda, appium):
    simulator = IosSimulator(
        device_type=DEVICE_TYPE,
        runtime=RUNTIME,
        state_dir=str(state),
        appium={"executable": str(appium), "wda": str(wda), "capabilities": {"appium:language": "en"}},
    )
    yield simulator
    simulator.close()


def appium_child(simulator) -> IosSimulatorAppium:
    child = simulator.children["appium"]
    assert isinstance(child, IosSimulatorAppium)
    return child


def test_appium_child_only_when_configured(state, fake, wda):
    plain = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state))
    assert "appium" not in plain.children
    configured = IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state), appium={"wda": str(wda)})
    assert configured.children["appium"].client() == "jumpstarter_driver_appium.client.AppiumClient"
    assert appium_child(configured).drivers == ["xcuitest"]


@pytest.mark.parametrize(
    "config",
    [{"executable": "appium"}, {"wda": "/opt/wda"}, {"wda": "/opt/WDA.app", "relaxed": True}, "appium"],
)
def test_appium_configuration_rejected_early(state, config):
    with pytest.raises(ConfigurationError):
        IosSimulator(device_type=DEVICE_TYPE, runtime=RUNTIME, state_dir=str(state), appium=config)


def test_appium_needs_a_running_simulator(simulator, wda):
    child = appium_child(simulator)
    with pytest.raises(RuntimeError, match="powered off"):
        child.prepare()
    with pytest.raises(RuntimeError, match="power it on"):
        child.state_root()
    simulator._on()
    child.prepare()
    wda.rmdir()
    with pytest.raises(RuntimeError, match="WebDriverAgent"):
        child.prepare()


def test_sessions_are_pinned_to_the_lease(simulator, wda, state):
    simulator._on()
    child = appium_child(simulator)
    fixed = child.fixed_capabilities()
    assert fixed["appium:udid"] == simulator._udid
    assert fixed["appium:platformVersion"] == "27.0"
    assert fixed["appium:simulatorDevicesSetPath"] == str(simulator._owned_set())
    assert fixed["appium:prebuiltWDAPath"] == str(wda.resolve())
    assert fixed["appium:language"] == "en" and fixed["appium:fullReset"] is False
    public = child.public_capabilities()
    assert public["appium:udid"] == simulator._udid
    assert "appium:simulatorDevicesSetPath" not in public and "appium:prebuiltWDAPath" not in public
    assert Path(str(child.state_root())).is_relative_to(state)


def test_mjpeg_port_cannot_listen_on_the_network(simulator):
    simulator._on()
    child = appium_child(simulator)
    first = child.session_capabilities()
    port = first["appium:mjpegServerPort"]
    assert first["appium:wdaLocalPort"] != port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe, pytest.raises(OSError):
        probe.bind(("0.0.0.0", port))
    second = child.session_capabilities()
    assert second["appium:mjpegServerPort"] != port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", port))  # The earlier session's guard is gone.
    child.stop_server()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("0.0.0.0", second["appium:mjpegServerPort"]))


def test_power_off_stops_appium_and_destroy_releases_it(simulator, monkeypatch):
    child = appium_child(simulator)
    simulator._on()
    stop, release = Mock(wraps=child.stop_server), Mock(wraps=child.release)
    monkeypatch.setattr(child, "stop_server", stop)
    monkeypatch.setattr(child, "release", release)
    simulator._off()
    stop.assert_called_once_with()
    release.assert_not_called()
    simulator._on()
    simulator._off(destroy=True)
    release.assert_called_once_with()


def test_client_sessions_reach_the_lease_simulator(simulator, tmp_path):
    with serve(simulator) as client:
        client.power.on()
        app = tmp_path / "Demo.app"
        app.mkdir()
        (app / "Info.plist").write_text("plist")
        token = client.appium.upload_app(app)
        with client.appium.serve() as target:
            assert target.capabilities()["appium:udid"] == simulator._udid
            parts = urlsplit(target.url)
            connection = http.client.HTTPSConnection(parts.hostname, parts.port, context=target.ssl_context())
            body = {"capabilities": {"alwaysMatch": {"appium:udid": "OTHER", "appium:app": token}}}
            connection.request("POST", "/session", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 200
            always = json.loads(response.read())["value"]["capabilities"]["capabilities"]["alwaysMatch"]
        assert always["appium:udid"] == simulator._udid
        assert always["appium:wdaBindingIP"] == "127.0.0.1"
        assert isinstance(always["appium:mjpegServerPort"], int)
        assert Path(always["appium:app"]).name == "Demo.app.zip"
        assert Path(always["appium:app"]).is_relative_to(simulator._lease_dir)
        client.power.off()
        with pytest.raises(Exception, match="powered off|Shutdown"):
            client.appium.info()
