"""
Tests for the mitmproxy Jumpstarter driver and client.

These tests verify the driver/client contract using Jumpstarter's
local testing harness (no real hardware or network needed).
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter_driver_mitmproxy.driver import (
    DirectoriesConfig,
    ListenConfig,
    MitmproxyDriver,
    WebConfig,
    _SizeLimitedCaptureBuffer,
    _verify_mitmproxy_binary,
)


@pytest.fixture(autouse=True)
def _no_port_wait(monkeypatch):
    """Never wait for a port a mocked process cannot open.

    ``start()`` probes the listen port with a ten-second timeout. Every test that
    starts the driver mocks ``Popen``, so nothing ever listens and each of them
    spent the full timeout — about ninety seconds of a hundred-second file. It
    also made the tests answer to whatever else happened to hold port 18080.
    """
    monkeypatch.setattr(
        MitmproxyDriver, "_wait_for_port", staticmethod(lambda *a, **k: True),
    )


@pytest.fixture
def driver(tmp_path):
    """Create a MitmproxyDriver with temp directories."""
    d = MitmproxyDriver(
        listen={"host": "127.0.0.1", "port": 18080},
        web={"host": "127.0.0.1", "port": 18081},
        directories={
            "data": str(tmp_path / "data"),
            "conf": str(tmp_path / "confdir"),
            "flows": str(tmp_path / "flows"),
            "addons": str(tmp_path / "addons"),
            "mocks": str(tmp_path / "mocks"),
            "files": str(tmp_path / "files"),
        },
        ssl_insecure=True,
    )
    yield d
    # Ensure capture server is cleaned up after each test
    d._stop_capture_server()


@pytest.fixture
def a_running_proxy(driver):
    """The driver in the state a successful ``start`` leaves it in.

    ``shape`` refuses without a running session, deliberately: the addon reads
    the config when it runs, and ``start`` clears whatever it finds, so a request
    made before there is a proxy was answered with success and then carried out
    by nobody. Tests about what ``shape`` does with its ARGUMENTS should not have
    to drive the lifecycle to say so, and tests that are about the lifecycle
    reset ``_process`` and start one themselves.
    """
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242
    driver._process = proc
    driver._current_mode = "mock"
    # What ``start`` records when the bundled addon is present, which it is here.
    driver._shaping_available = True
    return driver


class TestMockManagement:
    """Test mock endpoint CRUD operations (no subprocess needed)."""

    def test_set_mock_creates_config_file(self, driver, tmp_path):
        result = driver.set_mock(
            "GET", "/api/v1/status", 200,
            '{"id": "test-001"}', "application/json", "{}",
        )

        assert "Mock set" in result
        config = tmp_path / "mocks" / "endpoints.json"
        assert config.exists()

        data = json.loads(config.read_text())
        endpoints = data.get("endpoints", data)
        assert "GET /api/v1/status" in endpoints
        assert endpoints["GET /api/v1/status"]["status"] == 200

    def test_remove_mock(self, driver):
        driver.set_mock("GET", "/api/test", 200, '{}', "application/json", "{}")
        result = driver.remove_mock("GET", "/api/test")
        assert "Removed" in result

    def test_remove_nonexistent_mock(self, driver):
        result = driver.remove_mock("GET", "/api/nonexistent")
        assert "not found" in result

    def test_clear_mocks(self, driver):
        driver.set_mock("GET", "/a", 200, '{}', "application/json", "{}")
        driver.set_mock("POST", "/b", 201, '{}', "application/json", "{}")
        result = driver.clear_mocks()
        assert "Cleared 2" in result

    def test_list_mocks(self, driver):
        driver.set_mock("GET", "/api/v1/health", 200, '{"ok": true}',
                         "application/json", "{}")
        mocks = json.loads(driver.list_mocks())
        assert "GET /api/v1/health" in mocks

    def test_load_scenario(self, driver, tmp_path):
        scenario = {
            "GET /api/v1/status": {
                "status": 200,
                "body": {"id": "test-001"},
            },
            "POST /api/v1/telemetry": {
                "status": 202,
                "body": {"accepted": True},
            },
        }
        scenario_file = tmp_path / "mocks" / "test-scenario.json"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(json.dumps(scenario))

        result = driver.load_mock_scenario("test-scenario.json")
        assert "2 endpoint(s)" in result

    def test_load_missing_scenario(self, driver):
        result = driver.load_mock_scenario("nonexistent.json")
        assert "not found" in result

    def test_load_yaml_scenario(self, driver, tmp_path):
        yaml_content = (
            "endpoints:\n"
            "  GET /api/v1/status:\n"
            "    status: 200\n"
            "    body:\n"
            "      id: device-001\n"
            "      firmware_version: \"2.5.1\"\n"
        )
        scenario_file = tmp_path / "mocks" / "test.yaml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(yaml_content)

        result = driver.load_mock_scenario("test.yaml")
        assert "1 endpoint(s)" in result

        config = tmp_path / "mocks" / "endpoints.json"
        data = json.loads(config.read_text())
        ep = data["endpoints"]["GET /api/v1/status"]
        assert ep["status"] == 200
        assert ep["body"]["id"] == "device-001"
        assert ep["body"]["firmware_version"] == "2.5.1"

    def test_load_yml_extension(self, driver, tmp_path):
        yaml_content = (
            "endpoints:\n"
            "  POST /api/v1/data:\n"
            "    status: 201\n"
            "    body: {accepted: true}\n"
        )
        scenario_file = tmp_path / "mocks" / "test.yml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(yaml_content)

        result = driver.load_mock_scenario("test.yml")
        assert "1 endpoint(s)" in result

    def test_load_yaml_with_comments(self, driver, tmp_path):
        yaml_content = (
            "# This is a comment\n"
            "endpoints:\n"
            "  # Auth endpoint\n"
            "  GET /api/v1/auth:\n"
            "    status: 200\n"
            "    body: {token: abc}\n"
        )
        scenario_file = tmp_path / "mocks" / "commented.yaml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(yaml_content)

        result = driver.load_mock_scenario("commented.yaml")
        assert "1 endpoint(s)" in result

    def test_load_invalid_yaml(self, driver, tmp_path):
        scenario_file = tmp_path / "mocks" / "bad.yaml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text("endpoints:\n  - :\n    bad:: [yaml\n")

        result = driver.load_mock_scenario("bad.yaml")
        assert "Failed to load scenario" in result

    def test_load_json_still_works(self, driver, tmp_path):
        scenario = {
            "endpoints": {
                "GET /api/v1/health": {
                    "status": 200,
                    "body": {"ok": True},
                }
            }
        }
        scenario_file = tmp_path / "mocks" / "compat.json"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(json.dumps(scenario))

        result = driver.load_mock_scenario("compat.json")
        assert "1 endpoint(s)" in result


class TestStatus:
    """Test status reporting."""

    def test_status_when_stopped(self, driver):
        info = json.loads(driver.status())
        assert info["running"] is False
        assert info["mode"] == "stopped"
        assert info["pid"] is None

    def test_is_running_when_stopped(self, driver):
        assert driver.is_running() is False


class TestConnectWeb:
    """Test the connect_web exportstream method."""

    def test_connect_web_is_exported(self, driver):
        """Verify connect_web is registered as an exported stream method."""
        assert hasattr(driver, "connect_web")
        assert callable(driver.connect_web)


class TestBinaryVerification:
    """Test the mitmproxy binary presence check."""

    def test_missing_binary_returns_install_hint(self):
        with patch("jumpstarter_driver_mitmproxy.driver.shutil.which",
                   return_value=None):
            result = _verify_mitmproxy_binary("mitmdump")
        assert result is not None
        assert "not found on PATH" in result
        assert "install" in result.lower()

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_aborts_when_binary_missing(self, mock_popen, driver):
        with patch("jumpstarter_driver_mitmproxy.driver.shutil.which",
                   return_value=None):
            result = driver.start("mock", False, "")
        assert "not found on PATH" in result
        mock_popen.assert_not_called()


class TestLifecycle:
    """Test start/stop with mocked subprocess."""

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_mock_mode(self, mock_popen, driver):
        proc = MagicMock()
        proc.poll.return_value = None  # process is running
        proc.pid = 12345
        mock_popen.return_value = proc

        result = driver.start("mock", False, "")

        assert "mock" in result
        assert "8080" in result or "18080" in result
        assert driver.is_running()

        # Verify mitmdump was called (not mitmweb)
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "mitmdump"

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_with_web_ui(self, mock_popen, driver):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        result = driver.start("mock", True, "")

        assert "Web UI" in result
        cmd = mock_popen.call_args[0][0]
        assert cmd[0] == "mitmweb"
        assert "--web-port" in cmd

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_record_mode(self, mock_popen, driver):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        result = driver.start("record", False, "")

        assert "record" in result
        assert "Recording to" in result
        cmd = mock_popen.call_args[0][0]
        assert "-w" in cmd

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_replay_requires_file(self, mock_popen, driver):
        result = driver.start("replay", False, "")
        assert "Error" in result
        mock_popen.assert_not_called()

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_replay_checks_file_exists(self, mock_popen, driver,
                                              tmp_path):
        result = driver.start("replay", False, "nonexistent.bin")
        assert "not found" in result

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_start_unknown_mode(self, mock_popen, driver):
        result = driver.start("bogus", False, "")
        assert "Unknown mode" in result

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_double_start_rejected(self, mock_popen, driver):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")
        result = driver.start("mock", False, "")
        assert "Already running" in result

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_stop(self, mock_popen, driver):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        driver.start("mock", False, "")
        result = driver.stop()

        assert "Stopped" in result
        assert "mock" in result
        proc.send_signal.assert_called_once()

    def test_stop_when_not_running(self, driver):
        result = driver.stop()
        assert "Not running" in result


class TestAddonGeneration:
    """Test that the default addon script is generated correctly."""

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_generates_addon_if_missing(self, mock_popen, driver, tmp_path):
        """The installed addon has the driver's directories filled in."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")

        addon_file = tmp_path / "addons" / "mock_addon.py"
        assert addon_file.exists()

        content = addon_file.read_text()
        # The driver fills its own directories into the deployed copy's
        # placeholders; an unfilled one leaves the addon on its standalone
        # defaults while the driver writes elsewhere.
        spool = str(Path(driver.directories.data) / "capture-spool")
        assert f"_DRIVER_MOCK_DIR: str | None = {driver.directories.mocks!r}" in content
        assert f"_DRIVER_CAPTURE_SOCKET: str | None = {driver._capture_socket_path!r}" in content
        assert f"_DRIVER_CAPTURE_SPOOL_DIR: str | None = {spool!r}" in content
        assert "MitmproxyMockAddon" in content
        # Both addons have to be registered, and the shaper's hooks must be
        # async: a blocking sleep in a hook stalls every other flow through the
        # proxy, which is why it is a separate object rather than extra code in
        # the mocking one.
        assert "addons = [MitmproxyMockAddon(), TrafficShaper()]" in content
        assert "async def request" in content


class TestCACert:
    """Test CA certificate path and content retrieval."""

    def test_ca_cert_not_found(self, driver):
        result = driver.get_ca_cert_path()
        assert "not found" in result

    def test_ca_cert_found(self, driver, tmp_path):
        cert_path = tmp_path / "confdir" / "mitmproxy-ca-cert.pem"
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text("FAKE CERT")
        result = driver.get_ca_cert_path()
        assert result == str(cert_path)

    def test_get_ca_cert_not_found(self, driver):
        result = driver.get_ca_cert()
        assert result.startswith("Error:")
        assert "not found" in result

    def test_get_ca_cert_returns_contents(self, driver, tmp_path):
        pem_content = "-----BEGIN CERTIFICATE-----\nFAKEDATA\n-----END CERTIFICATE-----\n"
        cert_path = tmp_path / "confdir" / "mitmproxy-ca-cert.pem"
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text(pem_content)
        result = driver.get_ca_cert()
        assert result == pem_content


class TestCaptureManagement:
    """Test capture request buffer operations (no subprocess needed)."""

    def test_get_captured_requests_empty(self, driver):
        result = json.loads(driver.get_captured_requests())
        assert result == []

    def test_clear_captured_requests_empty(self, driver):
        result = driver.clear_captured_requests()
        assert "Cleared 0" in result

    def test_captured_requests_buffer(self, driver):
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/api/v1/status",
                "timestamp": 1700000000.0,
            })
        result = json.loads(driver.get_captured_requests())
        assert len(result) == 1
        assert result[0]["method"] == "GET"
        assert result[0]["path"] == "/api/v1/status"

    def test_clear_with_items(self, driver):
        with driver._capture_lock:
            driver._captured_requests.extend([
                {"method": "GET", "path": "/a"},
                {"method": "POST", "path": "/b"},
            ])
        result = driver.clear_captured_requests()
        assert "Cleared 2" in result
        assert json.loads(driver.get_captured_requests()) == []

    def test_wait_for_request_immediate_match(self, driver):
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET", "path": "/api/v1/status",
            })
        result = json.loads(
            driver.wait_for_request("GET", "/api/v1/status", 1.0)
        )
        assert result["method"] == "GET"
        assert result["path"] == "/api/v1/status"

    def test_wait_for_request_timeout(self, driver):
        result = json.loads(
            driver.wait_for_request("GET", "/api/nonexistent", 0.5)
        )
        assert "error" in result
        assert "Timed out" in result["error"]

    def test_wait_for_request_wildcard(self, driver):
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET", "path": "/api/v1/users/123",
            })
        result = json.loads(
            driver.wait_for_request("GET", "/api/v1/users/*", 1.0)
        )
        assert result["path"] == "/api/v1/users/123"

    def test_request_matches_exact(self):
        req = {"method": "GET", "path": "/api/v1/status"}
        assert MitmproxyDriver._request_matches(req, "GET", "/api/v1/status")
        assert not MitmproxyDriver._request_matches(req, "POST", "/api/v1/status")
        assert not MitmproxyDriver._request_matches(req, "GET", "/api/v1/other")

    def test_request_matches_wildcard(self):
        req = {"method": "GET", "path": "/api/v1/users/456"}
        assert MitmproxyDriver._request_matches(req, "GET", "/api/v1/users/*")
        assert MitmproxyDriver._request_matches(req, "GET", "/api/*")
        assert not MitmproxyDriver._request_matches(req, "GET", "/other/*")


class TestCaptureSocket:
    """Test the capture Unix socket lifecycle."""

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_socket_created_on_start(self, mock_popen, driver, tmp_path):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")

        sock_path = Path(driver._capture_socket_path)
        assert sock_path.exists()

        driver.stop()

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_socket_cleaned_up_on_stop(self, mock_popen, driver, tmp_path):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        proc.wait.return_value = 0
        mock_popen.return_value = proc

        driver.start("mock", False, "")
        sock_path = driver._capture_socket_path

        driver.stop()

        assert not Path(sock_path).exists()
        assert driver._capture_socket_path is None

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_socket_receives_events(self, mock_popen, driver, tmp_path):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")

        # Connect to the capture socket and send an event
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(driver._capture_socket_path)
            event = {
                "method": "GET",
                "path": "/test",
                "timestamp": time.time(),
                "response_status": 200,
            }
            sock.sendall((json.dumps(event) + "\n").encode())
            # Give the reader thread time to process
            time.sleep(0.5)

            captured = json.loads(driver.get_captured_requests())
            assert len(captured) == 1
            assert captured[0]["method"] == "GET"
            assert captured[0]["path"] == "/test"
        finally:
            sock.close()
            driver.stop()


class TestConditionalMocks:
    """Test conditional mock endpoint operations (no subprocess needed)."""

    def test_set_conditional_creates_config(self, driver, tmp_path):
        rules = [
            {
                "match": {"body_json": {"username": "admin"}},
                "status": 200,
                "body": {"token": "abc"},
            },
            {"status": 401, "body": {"error": "unauthorized"}},
        ]
        result = driver.set_mock_conditional(
            "POST", "/api/auth", json.dumps(rules),
        )
        assert "Conditional mock set" in result
        assert "2 rule(s)" in result

        config = tmp_path / "mocks" / "endpoints.json"
        assert config.exists()

        data = json.loads(config.read_text())
        endpoints = data.get("endpoints", data)
        assert "POST /api/auth" in endpoints
        assert "rules" in endpoints["POST /api/auth"]
        assert len(endpoints["POST /api/auth"]["rules"]) == 2

    def test_set_conditional_invalid_json(self, driver):
        result = driver.set_mock_conditional(
            "POST", "/api/auth", "not-valid-json",
        )
        assert "Invalid JSON" in result

    def test_set_conditional_empty_rules(self, driver):
        result = driver.set_mock_conditional(
            "POST", "/api/auth", "[]",
        )
        assert "non-empty" in result

    def test_conditional_and_remove(self, driver):
        rules = [{"status": 200, "body": {"ok": True}}]
        driver.set_mock_conditional(
            "GET", "/api/test", json.dumps(rules),
        )
        result = driver.remove_mock("GET", "/api/test")
        assert "Removed" in result

        mocks = json.loads(driver.list_mocks())
        assert "GET /api/test" not in mocks

    def test_conditional_listed_in_mocks(self, driver):
        rules = [
            {"match": {"headers": {"X-Key": "abc"}},
             "status": 200, "body": {"ok": True}},
            {"status": 403, "body": {"error": "forbidden"}},
        ]
        driver.set_mock_conditional(
            "GET", "/api/data", json.dumps(rules),
        )

        mocks = json.loads(driver.list_mocks())
        assert "GET /api/data" in mocks
        assert "rules" in mocks["GET /api/data"]


class TestStateStore:
    """Test shared state store operations (no subprocess needed)."""

    def test_set_and_get_state(self, driver):
        driver.set_state("token", json.dumps("abc-123"))
        result = json.loads(driver.get_state("token"))
        assert result == "abc-123"

    def test_set_state_complex_value(self, driver):
        driver.set_state("config", json.dumps({"retries": 3, "debug": True}))
        result = json.loads(driver.get_state("config"))
        assert result == {"retries": 3, "debug": True}

    def test_get_nonexistent_state(self, driver):
        result = json.loads(driver.get_state("nonexistent"))
        assert result is None

    def test_clear_state(self, driver):
        driver.set_state("a", json.dumps(1))
        driver.set_state("b", json.dumps(2))
        result = driver.clear_state()
        assert "Cleared 2" in result

        assert json.loads(driver.get_state("a")) is None
        assert json.loads(driver.get_state("b")) is None

    def test_get_all_state(self, driver):
        driver.set_state("x", json.dumps(10))
        driver.set_state("y", json.dumps("hello"))
        all_state = json.loads(driver.get_all_state())
        assert all_state == {"x": 10, "y": "hello"}

    def test_state_file_written(self, driver, tmp_path):
        driver.set_state("key", json.dumps("value"))

        state_file = tmp_path / "mocks" / "state.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text())
        assert data["key"] == "value"


class TestConfigValidation:
    """Test Pydantic config validation and defaults."""

    def test_defaults_from_data_dir(self):
        d = MitmproxyDriver(
            directories={"data": "/tmp/myproxy"},
        )
        try:
            assert isinstance(d.directories, DirectoriesConfig)
            assert isinstance(d.listen, ListenConfig)
            assert isinstance(d.web, WebConfig)
            assert d.directories.data == "/tmp/myproxy"
            assert d.directories.conf == "/tmp/myproxy/conf"
            assert d.directories.flows == "/tmp/myproxy/flows"
            assert d.directories.addons == "/tmp/myproxy/addons"
            assert d.directories.mocks == "/tmp/myproxy/mock-responses"
            assert d.directories.files == "/tmp/myproxy/mock-files"
            assert d.listen.host == "127.0.0.1"
            assert d.listen.port == 8080
            assert d.web.host == "127.0.0.1"
            assert d.web.port == 8081
        finally:
            d._stop_capture_server()

    def test_partial_directory_override(self):
        d = MitmproxyDriver(
            directories={
                "data": "/tmp/myproxy",
                "conf": "/etc/mitmproxy",
            },
        )
        try:
            assert isinstance(d.directories, DirectoriesConfig)
            assert d.directories.conf == "/etc/mitmproxy"
            assert d.directories.flows == "/tmp/myproxy/flows"
        finally:
            d._stop_capture_server()

    def test_inline_mocks_preloaded(self, tmp_path):
        inline = {
            "GET /api/health": {"status": 200, "body": {"ok": True}},
        }
        d = MitmproxyDriver(
            directories={
                "data": str(tmp_path / "data"),
                "mocks": str(tmp_path / "mocks"),
                "addons": str(tmp_path / "addons"),
            },
            mocks=inline,
        )
        try:
            assert d.mocks == inline
        finally:
            d._stop_capture_server()


class TestBundledAddonImport:
    """Importing the addon must not touch the filesystem (#1194)."""

    def test_import_does_not_create_directories(self, monkeypatch):
        """Importing the addon must not need a writable /opt/jumpstarter (#1194)."""
        import importlib
        import sys

        def refuse(self, *args, **kwargs):
            """Stand-in for Path.mkdir that always fails."""
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "mkdir", refuse)
        monkeypatch.delitem(
            sys.modules, "jumpstarter_driver_mitmproxy.bundled_addon",
            raising=False,
        )
        mod = importlib.import_module("jumpstarter_driver_mitmproxy.bundled_addon")
        assert mod.addons  # ty: ignore[unresolved-attribute]

    def test_spool_dir_is_created_on_first_spool(self, tmp_path):
        """The spool directory appears when the first body is spooled, not before."""
        import importlib

        from mitmproxy.test import tflow

        mod = importlib.import_module("jumpstarter_driver_mitmproxy.bundled_addon")
        addon = mod.MitmproxyMockAddon()  # ty: ignore[unresolved-attribute]
        addon._spool_dir = tmp_path / "missing" / "capture-spool"
        flow = tflow.tflow(resp=True)
        response = flow.response
        assert response is not None
        response.headers["content-type"] = "application/octet-stream"
        response.content = b"\x00" * 16

        result = addon._classify_response_body(flow)

        assert Path(result["response_body_file"]).read_bytes() == b"\x00" * 16


def _addon_module(monkeypatch):
    """The real addon module, with ``ctx.log`` stood in.

    ``ctx.log`` exists only inside a running mitmproxy, and this module logs
    through it, so the error paths below cannot run without a stand-in.
    """
    import importlib

    mod = importlib.import_module("jumpstarter_driver_mitmproxy.bundled_addon")
    log = MagicMock()
    monkeypatch.setattr(mod.ctx, "log", log, raising=False)  # ty: ignore[unresolved-attribute]
    return mod, log


class TestSpoolDirectory:
    """How the addon writes spooled response bodies."""

    @staticmethod
    def _binary_flow(url_path="/bin"):
        """A flow whose response is binary, so it is spooled to disk."""
        from mitmproxy.test import tflow

        flow = tflow.tflow(resp=True)
        response = flow.response
        assert response is not None
        flow.request.path = url_path
        response.headers["content-type"] = "application/octet-stream"
        response.content = b"\x00" * 16
        return flow

    def _addon(self, monkeypatch, tmp_path):
        """An addon instance that spools into a directory under tmp_path."""
        mod, log = _addon_module(monkeypatch)
        addon = mod.MitmproxyMockAddon()  # ty: ignore[unresolved-attribute]
        addon._spool_dir = tmp_path / "spool"
        return addon, log

    def test_an_uncreatable_spool_dir_costs_only_that_body(self, monkeypatch, tmp_path):
        """If the spool directory cannot be created, that body is dropped and the error logged."""
        addon, log = self._addon(monkeypatch, tmp_path)

        def refuse(self, *args, **kwargs):
            """Stand-in for Path.mkdir that always fails."""
            raise PermissionError(13, "Permission denied", str(self))

        monkeypatch.setattr(Path, "mkdir", refuse)

        result = addon._classify_response_body(self._binary_flow())

        assert result["response_body_file"] is None
        assert result["response_body"] is None
        assert result["response_is_binary"] is True
        assert "Failed to spool" in log.error.call_args.args[0]

    def test_spooled_bodies_are_private_whatever_the_umask(self, monkeypatch, tmp_path):
        """Files are 0600 and the directory 0700 even with the most permissive umask."""
        import os
        import stat

        addon, _ = self._addon(monkeypatch, tmp_path)
        previous = os.umask(0)  # the most permissive case
        try:
            result = addon._classify_response_body(self._binary_flow())
        finally:
            os.umask(previous)

        spooled = Path(result["response_body_file"])
        assert stat.S_IMODE(spooled.stat().st_mode) == 0o600
        assert stat.S_IMODE(spooled.parent.stat().st_mode) == 0o700

    @pytest.mark.skipif(
        not hasattr(__import__("os"), "O_NOFOLLOW"), reason="needs O_NOFOLLOW",
    )
    def test_a_symlink_in_the_spool_dir_is_not_followed(self, monkeypatch, tmp_path):
        """A symlink planted at a spool file's name is refused, not written through."""
        import hashlib

        addon, _ = self._addon(monkeypatch, tmp_path)
        flow = self._binary_flow()
        url_hash = hashlib.sha256(flow.request.pretty_url.encode()).hexdigest()[:12]
        victim = tmp_path / "victim.txt"
        victim.write_text("untouched")
        addon._spool_dir.mkdir()
        # The name the first spooled body would get.
        (addon._spool_dir / f"000001_{url_hash}.bin").symlink_to(victim)

        result = addon._classify_response_body(flow)

        assert victim.read_text() == "untouched"
        assert result["response_body_file"] is None

    @staticmethod
    def _first_spool_name(flow):
        """The name the first spooled body of ``flow`` gets."""
        import hashlib

        url_hash = hashlib.sha256(flow.request.pretty_url.encode()).hexdigest()[:12]
        return f"000001_{url_hash}.bin"

    def test_a_leftover_spool_file_and_dir_are_tightened_before_writing(
        self, monkeypatch, tmp_path,
    ):
        """A crashed session or an older version leaves loose modes behind."""
        import os
        import stat

        addon, _ = self._addon(monkeypatch, tmp_path)
        flow = self._binary_flow()
        addon._spool_dir.mkdir()
        os.chmod(addon._spool_dir, 0o755)
        leftover = addon._spool_dir / self._first_spool_name(flow)
        leftover.write_bytes(b"body of an earlier session")
        os.chmod(leftover, 0o644)

        result = addon._classify_response_body(flow)

        assert Path(result["response_body_file"]) == leftover
        assert leftover.read_bytes() == b"\x00" * 16
        assert stat.S_IMODE(leftover.stat().st_mode) == 0o600
        assert stat.S_IMODE(addon._spool_dir.stat().st_mode) == 0o700

    def test_a_spool_file_that_cannot_be_tightened_is_never_written(
        self, monkeypatch, tmp_path,
    ):
        """E.g. a file another user created in a shared directory."""
        import os

        addon, log = self._addon(monkeypatch, tmp_path)
        flow = self._binary_flow()
        addon._spool_dir.mkdir()
        target = addon._spool_dir / self._first_spool_name(flow)
        target.write_bytes(b"planted")

        def not_ours(fd, mode):
            """Stand-in for fchmod on a file this user does not own."""
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(os, "fchmod", not_ours)

        result = addon._classify_response_body(flow)

        assert result["response_body_file"] is None
        assert b"\x00" * 16 not in target.read_bytes(), "the body reached a file that was not ours"
        assert "Failed to spool" in log.error.call_args.args[0]

    @pytest.mark.skipif(
        not hasattr(__import__("os"), "O_NOFOLLOW"), reason="needs O_NOFOLLOW",
    )
    def test_a_symlinked_spool_dir_is_refused_and_its_target_left_alone(
        self, monkeypatch, tmp_path,
    ):
        """chmod would follow the link and change someone else's directory."""
        import os
        import stat

        addon, _ = self._addon(monkeypatch, tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        os.chmod(elsewhere, 0o755)
        addon._spool_dir.symlink_to(elsewhere, target_is_directory=True)

        result = addon._classify_response_body(self._binary_flow())

        assert result["response_body_file"] is None
        assert stat.S_IMODE(elsewhere.stat().st_mode) == 0o755
        assert list(elsewhere.iterdir()) == []

    def test_the_driver_creates_its_spool_dir_private_too(self, driver):
        """The directory the driver creates when it starts is mode 0700 as well."""
        import os
        import stat

        previous = os.umask(0)
        try:
            driver._start_capture_server()
        finally:
            os.umask(previous)

        spool = Path(driver.directories.data) / "capture-spool"
        assert stat.S_IMODE(spool.stat().st_mode) == 0o700


class TestAddonRegistryPaths:
    """An addon name comes from the mock config and must stay in the addons dir."""

    @pytest.fixture
    def registry(self, monkeypatch, tmp_path):
        """An addon registry over an addons directory holding one valid addon."""
        mod, _ = _addon_module(monkeypatch)
        addons = tmp_path / "addons"
        addons.mkdir()
        (addons / "good.py").write_text(self._handler("good"))
        return mod.AddonRegistry(str(addons))  # ty: ignore[unresolved-attribute]

    @staticmethod
    def _handler(label, marker=None):
        """Source of an addon module; given ``marker``, it records that it was executed."""
        touch = ""
        if marker:
            touch = f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
        return (
            f"{touch}class Handler:\n    label = {label!r}\n"
            "    def handle(self, flow, config):\n        return True\n"
        )

    def test_a_plain_name_loads(self, registry):
        """An ordinary addon name still loads."""
        assert registry.get_handler("good").label == "good"

    def test_a_name_in_a_subdirectory_loads(self, registry, tmp_path):
        """Names with a subdirectory, including ``..`` that stays inside, still load."""
        (tmp_path / "addons" / "sub").mkdir()
        (tmp_path / "addons" / "sub" / "nested.py").write_text(self._handler("nested"))

        assert registry.get_handler("sub/nested").label == "nested"
        assert registry.get_handler("sub/../good").label == "good"

    @pytest.mark.parametrize("name", [
        "../outside",
        "sub/../../outside",
        "../addons-sibling/outside",
        "ABSOLUTE",
    ])
    def test_a_name_that_leaves_the_addons_dir_is_not_run(self, registry, tmp_path, name):
        """Neither ``..`` nor an absolute path may run a script outside the addons directory."""
        marker = tmp_path / "ran"
        # Exists, so ``sub/..`` resolves and the escape really is attempted.
        (tmp_path / "addons" / "sub").mkdir(exist_ok=True)
        for parent in (tmp_path, tmp_path / "addons-sibling"):
            parent.mkdir(exist_ok=True)
            (parent / "outside.py").write_text(self._handler("outside", marker))
        if name == "ABSOLUTE":
            name = str(tmp_path / "outside")

        assert registry.get_handler(name) is None
        assert not marker.exists(), "code outside the addons directory was executed"

    def test_a_script_the_operator_symlinked_in_still_loads(self, registry, tmp_path):
        """A script the operator symlinked into the directory is still loaded."""
        real = tmp_path / "shared" / "linked_real.py"
        real.parent.mkdir()
        real.write_text(self._handler("linked"))
        (tmp_path / "addons" / "linked.py").symlink_to(real)

        assert registry.get_handler("linked").label == "linked"


class TestAddonPaths:
    """Where the addon keeps its files, standalone and when the driver installs it."""

    @staticmethod
    def _fresh_import(monkeypatch):
        """Import the addon module again, so its module-level defaults are recomputed."""
        import importlib
        import sys

        monkeypatch.delitem(
            sys.modules, "jumpstarter_driver_mitmproxy.bundled_addon",
            raising=False,
        )
        return importlib.import_module("jumpstarter_driver_mitmproxy.bundled_addon")

    def test_standalone_defaults_are_per_user_not_opt(self, monkeypatch):
        """Without a driver, the addon's files go under a per-user temp dir."""
        import getpass
        import tempfile

        monkeypatch.delenv("MITMPROXY_DATA_DIR", raising=False)
        monkeypatch.delenv("MITMPROXY_MOCK_DIR", raising=False)
        mod = self._fresh_import(monkeypatch)

        base = Path(tempfile.gettempdir()) / f"jumpstarter-mitmproxy-{getpass.getuser()}"
        assert mod.CAPTURE_SPOOL_DIR == str(base / "capture-spool")  # ty: ignore[unresolved-attribute]
        assert mod.CAPTURE_SOCKET == str(base / "capture.sock")  # ty: ignore[unresolved-attribute]
        assert mod.MitmproxyMockAddon.MOCK_DIR == str(base / "mock-responses")  # ty: ignore[unresolved-attribute]

    def test_data_dir_env_moves_every_default(self, monkeypatch, tmp_path):
        """MITMPROXY_DATA_DIR moves the socket, spool and mock directories together."""
        monkeypatch.setenv("MITMPROXY_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("MITMPROXY_MOCK_DIR", raising=False)
        mod = self._fresh_import(monkeypatch)

        assert mod.CAPTURE_SPOOL_DIR == str(tmp_path / "capture-spool")  # ty: ignore[unresolved-attribute]
        assert mod.CAPTURE_SOCKET == str(tmp_path / "capture.sock")  # ty: ignore[unresolved-attribute]
        assert mod.MitmproxyMockAddon.MOCK_DIR == str(tmp_path / "mock-responses")  # ty: ignore[unresolved-attribute]

    def test_standalone_default_survives_a_missing_login_name(self, monkeypatch):
        """A UID with no login name still gets a usable default."""
        import getpass

        def no_user():
            """Stand-in for getpass.getuser on a UID with no passwd entry."""
            raise KeyError("getpwuid(): uid not found")

        monkeypatch.delenv("MITMPROXY_DATA_DIR", raising=False)
        monkeypatch.setattr(getpass, "getuser", no_user)
        mod = self._fresh_import(monkeypatch)

        assert "jumpstarter-mitmproxy-" in mod.CAPTURE_SPOOL_DIR  # ty: ignore[unresolved-attribute]

    def test_installed_addon_uses_the_driver_directories(self, driver, tmp_path):
        """Executing the filled-in source resolves to the driver's paths."""
        import types

        driver._capture_socket_path = str(tmp_path / "data" / "capture.sock")
        source = driver._fill_addon_paths(
            (Path(__file__).parent / "bundled_addon.py").read_text()
        ).replace("addons = [MitmproxyMockAddon(), TrafficShaper()]", "addons = []")
        module = types.ModuleType("installed_addon")
        exec(compile(source, "mock_addon.py", "exec"), module.__dict__)  # noqa: S102

        assert module.CAPTURE_SPOOL_DIR == str(Path(driver.directories.data) / "capture-spool")  # ty: ignore[unresolved-attribute]
        assert module.CAPTURE_SOCKET == driver._capture_socket_path  # ty: ignore[unresolved-attribute]
        assert module.MitmproxyMockAddon.MOCK_DIR == driver.directories.mocks  # ty: ignore[unresolved-attribute]

    def test_fill_refuses_source_without_placeholders(self, driver):
        """A renamed placeholder is an error, not a silent fall-back to the defaults."""
        with pytest.raises(RuntimeError, match="_DRIVER_MOCK_DIR"):
            driver._fill_addon_paths("addons = []\n")


@pytest.fixture
def deep_merge_patch():
    """Import _deep_merge_patch lazily."""
    import importlib
    mod = importlib.import_module("jumpstarter_driver_mitmproxy.bundled_addon")
    return mod._deep_merge_patch  # ty: ignore[unresolved-attribute]


@pytest.fixture
def apply_patches(deep_merge_patch):
    """Import _apply_patches lazily."""
    import sys
    mod = sys.modules["jumpstarter_driver_mitmproxy.bundled_addon"]
    return mod._apply_patches  # ty: ignore[unresolved-attribute]


class TestDeepMergePatch:
    """Unit tests for _deep_merge_patch."""

    def test_simple_dict_merge(self, deep_merge_patch):
        target = {"a": 1, "b": 2}
        deep_merge_patch(target, {"b": 3, "c": 4})
        assert target == {"a": 1, "b": 3, "c": 4}

    def test_nested_dict_merge(self, deep_merge_patch):
        target = {"outer": {"inner": 1, "keep": True}}
        deep_merge_patch(target, {"outer": {"inner": 99}})
        assert target == {"outer": {"inner": 99, "keep": True}}

    def test_array_index(self, deep_merge_patch):
        target = {"items": [{"name": "a"}, {"name": "b"}]}
        deep_merge_patch(target, {"items[1]": {"name": "patched"}})
        assert target["items"][1]["name"] == "patched"
        assert target["items"][0]["name"] == "a"

    def test_nested_array_index(self, deep_merge_patch):
        target = {
            "list": [
                {"sub": {"val": "old", "extra": True}},
            ],
        }
        deep_merge_patch(target, {"list[0]": {"sub": {"val": "new"}}})
        assert target["list"][0]["sub"]["val"] == "new"
        assert target["list"][0]["sub"]["extra"] is True

    def test_scalar_replacement(self, deep_merge_patch):
        target = {"a": {"b": [1, 2, 3]}}
        deep_merge_patch(target, {"a": {"b": [10]}})
        assert target["a"]["b"] == [10]

    def test_sibling_fields_at_same_level(self, deep_merge_patch):
        target = {"a": 1, "b": 2, "c": 3}
        deep_merge_patch(target, {"a": 10, "c": 30})
        assert target == {"a": 10, "b": 2, "c": 30}

    def test_array_scalar_replacement(self, deep_merge_patch):
        target = {"items": ["a", "b", "c"]}
        deep_merge_patch(target, {"items[2]": "z"})
        assert target["items"] == ["a", "b", "z"]

    def test_missing_key_skipped(self, deep_merge_patch):
        """Missing array keys are auto-created rather than raising."""
        target = {"a": 1}
        deep_merge_patch(target, {"nonexistent[0]": "val"})
        assert target["nonexistent"] == ["val"]
        assert target["a"] == 1


class TestApplyPatches:
    """Unit tests for _apply_patches."""

    def test_basic_patch(self, apply_patches):
        body = json.dumps({"status": "active", "count": 5}).encode()
        result = apply_patches(body, {"status": "inactive"}, None, None)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["status"] == "inactive"
        assert parsed["count"] == 5

    def test_non_json_returns_none(self, apply_patches):
        result = apply_patches(b"not json", {"key": "val"}, None, None)
        assert result is None

    def test_empty_body_returns_none(self, apply_patches):
        result = apply_patches(b"", {"key": "val"}, None, None)
        assert result is None

    def test_nested_patch(self, apply_patches):
        body = json.dumps({
            "response": {"data": {"value": "old", "other": 1}},
        }).encode()
        result = apply_patches(
            body, {"response": {"data": {"value": "new"}}}, None, None,
        )
        parsed = json.loads(result)
        assert parsed["response"]["data"]["value"] == "new"
        assert parsed["response"]["data"]["other"] == 1

    def test_missing_key_continues_with_partial(self, apply_patches):
        """Patches with bad keys log a warning but don't crash."""
        body = json.dumps({"a": 1}).encode()
        result = apply_patches(
            body, {"missing[0]": "val"}, None, None,
        )
        # Should still return valid JSON (partial patch applied)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["a"] == 1


class TestPatchMocks:
    """Integration tests for set_mock_patch and patch scenario loading."""

    def test_set_mock_patch(self, driver, tmp_path):
        result = driver.set_mock_patch(
            "GET", "/api/v1/status",
            '{"data": {"status": "inactive"}}',
        )
        assert "Patch mock set" in result

        config = tmp_path / "mocks" / "endpoints.json"
        assert config.exists()

        data = json.loads(config.read_text())
        ep = data["endpoints"]["GET /api/v1/status"]
        assert "patch" in ep
        assert ep["patch"]["data"]["status"] == "inactive"

    def test_set_mock_patch_invalid_json(self, driver):
        result = driver.set_mock_patch("GET", "/test", "not json")
        assert "Invalid JSON" in result

    def test_set_mock_patch_non_object(self, driver):
        result = driver.set_mock_patch("GET", "/test", '"string"')
        assert "must be a JSON object" in result

    def test_set_mock_patch_list_and_remove(self, driver):
        driver.set_mock_patch(
            "GET", "/api/v1/status",
            '{"data": {"status": "inactive"}}',
        )
        mocks = json.loads(driver.list_mocks())
        assert "GET /api/v1/status" in mocks
        assert "patch" in mocks["GET /api/v1/status"]

        result = driver.remove_mock("GET", "/api/v1/status")
        assert "Removed" in result

    def test_load_yaml_scenario_with_mocks_key(self, driver, tmp_path):
        yaml_content = (
            "mocks:\n"
            "  https://api.example.com/rest/v3/status:\n"
            "  - method: GET\n"
            "    patch:\n"
            "      account:\n"
            "        subState: INACTIVE\n"
        )
        scenario_file = tmp_path / "mocks" / "test-patch.yaml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(yaml_content)

        result = driver.load_mock_scenario("test-patch.yaml")
        assert "1 endpoint(s)" in result

        config = tmp_path / "mocks" / "endpoints.json"
        data = json.loads(config.read_text())
        ep = data["endpoints"]["GET /rest/v3/status"]
        assert "patch" in ep
        assert ep["patch"]["account"]["subState"] == "INACTIVE"

    def test_load_scenario_content_with_mocks_key(self, driver):
        yaml_content = (
            "mocks:\n"
            "  https://api.example.com/rest/v3/status:\n"
            "  - method: GET\n"
            "    patch:\n"
            "      status: inactive\n"
        )
        result = driver.load_mock_scenario_content(
            "test.yaml", yaml_content,
        )
        assert "1 endpoint(s)" in result

    def test_patch_survives_flatten_and_convert(self, driver, tmp_path):
        """Verify a patch entry round-trips through _flatten_entry
        and _convert_url_endpoints correctly."""
        yaml_content = (
            "mocks:\n"
            "  https://api.example.com/rest/v3/modules/nonPII:\n"
            "  - method: GET\n"
            "    patch:\n"
            "      ModuleListResponse:\n"
            "        moduleList:\n"
            "          modules[0]:\n"
            "            status: Inactive\n"
        )
        scenario_file = tmp_path / "mocks" / "roundtrip.yaml"
        scenario_file.parent.mkdir(parents=True, exist_ok=True)
        scenario_file.write_text(yaml_content)

        driver.load_mock_scenario("roundtrip.yaml")

        config = tmp_path / "mocks" / "endpoints.json"
        data = json.loads(config.read_text())
        ep = data["endpoints"]["GET /rest/v3/modules/nonPII"]
        assert "patch" in ep
        assert (
            ep["patch"]["ModuleListResponse"]["moduleList"]["modules[0]"]["status"]
            == "Inactive"
        )


class TestTrafficShaping:
    """Weak-link emulation: rate, delay, jitter and failed flows.

    The proxy is often the only place in a rig that can add real delay — the
    device under test may have no kernel support for it — so these knobs are a
    first-class feature rather than something a caller bolts on with its own
    addon script.

    The config lands in a file the running addon polls, so what is asserted here
    is that file: its presence, its contents, and its removal.
    """

    @pytest.fixture(autouse=True)
    def _session(self, a_running_proxy):
        """Shaping needs a running proxy. See :func:`a_running_proxy`."""


    @staticmethod
    def _config(tmp_path):
        return tmp_path / "mocks" / "shaping.json"

    def test_shaping_is_written_where_the_addon_reads_it(self, driver, tmp_path):
        driver.shape(rate_kbit=400, latency_ms=250, jitter_ms=80, drop_pct=0.2)
        written = json.loads(self._config(tmp_path).read_text())
        assert written == {
            "rate_kbit": 400, "latency_ms": 250.0,
            "jitter_ms": 80.0, "drop_pct": 0.2,
        }

    def test_shaping_reports_only_what_was_asked_for(self, driver):
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is True
        assert reply["applied"]["rate_kbit"] == 400
        assert reply["applied"]["latency_ms"] == 0.0

    def test_get_shaping_answers_without_reading_the_file(self, driver):
        driver.shape(rate_kbit=400, latency_ms=100, jitter_ms=50)
        assert json.loads(driver.get_shaping())["rate_kbit"] == 400
        assert json.loads(driver.get_shaping())["latency_ms"] == 100.0

    def test_clearing_removes_the_file_rather_than_zeroing_it(
        self, driver, tmp_path,
    ):
        """A proxy nobody asked to shape should have nothing to read."""
        driver.shape(rate_kbit=400)
        assert self._config(tmp_path).exists()
        reply = json.loads(driver.clear_shaping())
        assert reply == {"ok": True, "cleared": True}
        assert not self._config(tmp_path).exists()
        assert json.loads(driver.get_shaping()) == {}

    def test_clearing_twice_is_not_an_error(self, driver):
        driver.clear_shaping()
        assert json.loads(driver.clear_shaping()) == {
            "ok": True, "cleared": False,
        }

    def test_all_zero_is_the_same_as_clearing(self, driver, tmp_path):
        driver.shape(rate_kbit=400)
        reply = json.loads(driver.shape())
        assert not self._config(tmp_path).exists()
        # Still an answer to ``shape``, so it carries ``applied`` like every
        # other one. It used to return ``clear_shaping``'s envelope, which a
        # caller reading ``applied`` could not read at all.
        assert reply == {"ok": True, "applied": {}, "cleared": True}

    def test_shaping_is_unavailable_until_a_session_says_otherwise(self):
        """Fail closed.

        ``start`` sets this either way — True when the bundled addon is copied,
        False when the fallback is generated — so the only state the default
        describes is "no session yet", where nothing can shape. Asserted on the
        invariant rather than through ``shape``, because the session guard now
        answers first and would mask it: the two guards are deliberately
        independent, and this is the one that has no other witness.
        """
        fresh = MitmproxyDriver(listen={"host": "127.0.0.1", "port": 18080})
        assert fresh._shaping_available is False

    def test_shaping_before_the_proxy_runs_is_refused(self, driver, tmp_path):
        """It used to answer ``ok: true``, write the file, and have the next
        ``start`` delete it — a request granted and then carried out by nobody.

        The refusal names the SESSION, not the addon. ``_shaping_available`` is
        only known once a session exists, so "this session runs the fallback
        addon" would be the wrong reason when there is no session at all.
        """
        driver._process = None
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is False
        assert "running proxy" in reply["error"]
        assert "fallback" not in reply["error"], reply["error"]
        assert not self._config(tmp_path).exists(), "and nothing was written"
        assert json.loads(driver.get_shaping()) == {}

    def test_shaping_after_the_proxy_dies_is_refused_too(self, driver):
        """A process that exited is not a session either, and ``poll`` is how
        that is told apart from a live one."""
        driver._process.poll.return_value = 1
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is False
        assert "running proxy" in reply["error"]

    def test_a_refused_update_leaves_the_shaping_in_effect_alone(
        self, driver, tmp_path,
    ):
        """Every other refusal test starts from an unshaped driver, so none of
        them can tell "refused" from "refused and wiped what was already
        running"."""
        driver.shape(rate_kbit=400, latency_ms=250, jitter_ms=80)
        # Refused by the ORDERING guard, not by the first range check: a refusal
        # that trips on ``rate_kbit=-1`` exercises the earliest branch and says
        # nothing about the ones behind it.
        assert not json.loads(
            driver.shape(latency_ms=50, jitter_ms=100),
        )["ok"]
        assert not json.loads(driver.shape(rate_kbit=-1))["ok"]

        assert json.loads(driver.get_shaping())["rate_kbit"] == 400
        assert json.loads(
            self._config(tmp_path).read_text(),
        )["rate_kbit"] == 400, "on disk too, where the addon reads it"

    def test_a_failed_write_is_refused_and_not_remembered(
        self, driver, tmp_path, monkeypatch,
    ):
        """The addon shapes from the file, so memory must not run ahead of it.

        ``self._shaping`` used to be assigned before the write, so a write that
        failed left ``get_shaping`` reporting a weak link that no addon would
        ever read — and ``get_shaping`` is what a test asserts on.
        """
        import json as _json
        monkeypatch.setattr(
            _json, "dump",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk is full")),
        )
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is False
        assert "disk is full" in reply["error"]
        assert json.loads(driver.get_shaping()) == {}, "nothing was remembered"
        assert not self._config(tmp_path).exists()

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_stopping_the_proxy_forgets_the_shaping(
        self, mock_popen, driver, tmp_path, caplog,
    ):
        """Shaping belongs to a session. Without this the driver kept reporting a
        weak link after the proxy applying it was gone."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        driver._process = None
        driver.start("mock", False, "")
        driver.shape(rate_kbit=400)
        assert self._config(tmp_path).exists()

        with caplog.at_level("WARNING"):
            driver.stop()
        assert json.loads(driver.get_shaping()) == {}
        assert not self._config(tmp_path).exists()
        # Audible at BOTH ends. Saying it only in ``start`` moved the silence
        # rather than removing it: by the time the next start looks, stop has
        # already deleted the file, so that warning could never fire.
        assert "Discarded the shaping config at stop" in caplog.text

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_start_that_discards_a_stale_config_says_so(
        self, mock_popen, driver, caplog,
    ):
        """A new session starts unshaped, which means a request made before it
        gets thrown away. Doing that silently is how a test ends up asserting
        against a link nobody is shaping."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        driver._process = None
        config = Path(driver.directories.mocks) / "shaping.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"rate_kbit": 400}))

        with caplog.at_level("WARNING"):
            driver.start("mock", False, "")
        assert "Discarded the shaping config at start" in caplog.text

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_start_with_nothing_to_discard_stays_quiet(
        self, mock_popen, driver, caplog,
    ):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        driver._process = None
        with caplog.at_level("WARNING"):
            driver.start("mock", False, "")
        assert "Discarded the shaping config" not in caplog.text

    def test_negative_values_are_refused(self, driver, tmp_path):
        """Every knob, including ``jitter_ms``.

        It was the one left out, and the one with a second guard behind this
        first one — so a negative jitter reaching the ordering check instead of
        being refused here would have gone unnoticed.
        """
        for kwargs in (
            {"rate_kbit": -1}, {"latency_ms": -1}, {"drop_pct": -1},
            {"jitter_ms": -1},
        ):
            reply = json.loads(driver.shape(**kwargs))
            assert reply["ok"] is False, kwargs
            assert "Invalid" in reply["error"], kwargs
            assert "must not be negative" in reply["error"], kwargs
        assert not self._config(tmp_path).exists(), "nothing was written"

    def test_a_value_that_is_not_a_finite_number_is_refused(
        self, driver, tmp_path,
    ):
        """NaN makes every range check below it False.

        So it passed all of them and was written out as a bare ``NaN`` — not JSON
        a strict reader accepts, and a figure the addon would have paced traffic
        by. Infinity got through the same way wherever there was no upper bound.
        """
        for knob in ("rate_kbit", "latency_ms", "jitter_ms", "drop_pct"):
            for value in (float("nan"), float("inf")):
                reply = json.loads(driver.shape(**{knob: value}))
                assert reply["ok"] is False, (knob, value)
                assert "finite" in reply["error"], (knob, value)
        assert not self._config(tmp_path).exists(), "nothing was written"

    def test_a_delay_past_the_ceiling_is_refused(self, driver, tmp_path):
        """A delay is a real sleep per flow, so an out-of-range figure is a hang
        rather than a weak link: every request behind it waits that long, past
        any client timeout, with nothing in the reply to say so. The usual way in
        is a value in the wrong unit — seconds where milliseconds belong."""
        from jumpstarter_driver_mitmproxy.driver import MAX_DELAY_MS

        for knob in ("latency_ms", "jitter_ms"):
            reply = json.loads(driver.shape(**{knob: MAX_DELAY_MS + 1}))
            assert reply["ok"] is False, knob
            assert f"Invalid {knob}" in reply["error"], knob
            assert "hang" in reply["error"], knob
        assert not self._config(tmp_path).exists(), "nothing was written"
        # The ceiling itself is accepted: the refusal is for what is past it.
        assert json.loads(
            driver.shape(latency_ms=MAX_DELAY_MS, jitter_ms=MAX_DELAY_MS),
        )["ok"] is True

    def test_drop_above_a_hundred_percent_is_refused(self, driver):
        assert "Invalid drop_pct" in json.loads(
            driver.shape(drop_pct=101),
        )["error"]

    def test_jitter_larger_than_the_delay_is_refused(self, driver):
        """The spread would reach below zero and be clamped there, quietly
        lowering the average delay below what was asked for."""
        result = driver.shape(latency_ms=50, jitter_ms=100)
        assert "Invalid jitter_ms" in result

    def test_jitter_without_a_delay_is_refused(self, driver):
        """The same rule at any latency, including none.

        A spread wider than the delay reaches below zero and gets clamped, so the
        average lands below what was asked for. The check used to exempt zero
        latency, which let this driver accept what the layer above it refuses.
        """
        assert not json.loads(driver.shape(jitter_ms=30))["ok"]
        assert not json.loads(
            driver.shape(latency_ms=50, jitter_ms=100),
        )["ok"]
        accepted = json.loads(driver.shape(latency_ms=50, jitter_ms=50))
        assert accepted["applied"]["jitter_ms"] == 50.0


    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_new_session_starts_unshaped(self, mock_popen, driver, tmp_path):
        """Regression: a weak link outlived the process that configured it.

        The config lives in a directory that survives the proxy — often a stable
        per-user path — so the addon of a fresh session reloaded whatever it
        found, while every status view reported the in-memory state and said
        nothing was shaped. Nobody would connect a slow proxy to a request made
        an hour earlier.
        """
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        # Written straight to disk, which is exactly how it arrives: the previous
        # process is gone and only its file is left. Asking THIS driver to shape
        # would be refused now, since there is no proxy yet to shape anything.
        driver._process = None
        self._config(tmp_path).parent.mkdir(parents=True, exist_ok=True)
        self._config(tmp_path).write_text(
            json.dumps({"rate_kbit": 400, "latency_ms": 250.0,
                        "jitter_ms": 0.0, "drop_pct": 0.0}),
        )

        driver.start("mock", False, "")
        assert not self._config(tmp_path).exists()
        assert json.loads(driver.get_shaping()) == {}


class _FakeClock:
    """Monotonic clock a test drives by hand, so pacing costs no real time."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestShapingArithmetic:
    """The bucket itself, and the config the addon reads.

    Both were previously unreachable from a test: the bucket had no injectable
    clock, so checking it meant sleeping, and the config parsing lived inside an
    addon that could only be exercised by running mitmproxy. A ``consume`` that
    returned zero unconditionally therefore passed everything while shaping
    nothing.
    """

    @staticmethod
    def _addon_module():
        """Load the addon the way mitmdump would: as a script, against the real
        mitmproxy.

        It used to be loaded with mitmproxy stubbed in through
        ``sys.modules.setdefault``, which was wrong twice over. The stub was
        never removed, so it decided what every LATER test in the session saw
        under the name ``mitmproxy`` — running one class alone and running the
        file exercised different modules. And the stub's ``ctx`` was forgiving
        where the real one is not, which is precisely how a hook that reached for
        the removed ``ctx.log`` passed here and died in production.

        mitmproxy is a hard dependency of this driver, so there is nothing to
        stub: use it.
        """
        import types
        from pathlib import Path
        pytest.importorskip("mitmproxy")
        source = (
            Path(__file__).parent / "bundled_addon.py"
        ).read_text().replace(
            "addons = [MitmproxyMockAddon(), TrafficShaper()]", "addons = []",
        )
        module = types.ModuleType("addon_under_test")
        exec(compile(source, "bundled_addon.py", "exec"), module.__dict__)  # noqa: S102
        return module

    def test_a_full_bucket_lets_a_small_body_through(self):
        module = self._addon_module()
        bucket = module._Bucket(50_000, clock=_FakeClock())
        assert bucket.consume(1_000) == 0.0

    def test_an_oversized_body_waits_for_its_own_transmission_time(self):
        """A zero here would mean no pacing at all, which is how a broken bucket
        hides: the proxy still works, just at full speed."""
        module = self._addon_module()
        bucket = module._Bucket(50_000, clock=_FakeClock())
        bucket.consume(50_000)                      # empties it
        assert bucket.consume(50_000) == pytest.approx(1.0)

    def test_the_bucket_refills_at_the_configured_rate(self):
        module = self._addon_module()
        clock = _FakeClock()
        bucket = module._Bucket(50_000, clock=clock)
        bucket.consume(50_000)
        clock.advance(0.5)
        # Half a second at 50 kB/s is 25 000 bytes of credit.
        assert bucket.consume(25_000) == 0.0
        assert bucket.consume(25_000) == pytest.approx(0.5)

    def test_the_debt_is_carried_so_the_average_holds(self):
        module = self._addon_module()
        clock = _FakeClock()
        bucket = module._Bucket(50_000, clock=clock)
        bucket.consume(150_000)
        clock.advance(1.0)
        assert bucket.consume(1) > 0, "the overdraft is still being paid off"

    def test_a_config_that_is_not_an_object_stops_shaping_and_does_not_raise(
        self, tmp_path, monkeypatch,
    ):
        """Regression: a JSON list was accepted, then every value read raised
        ``AttributeError`` inside the hook — permanently, because the file had
        already been recorded as loaded.

        Starts from a shaper that IS shaping. Asserting an empty config on one
        that never shaped holds whether or not the reset runs, which is no test
        at all.
        """
        module = self._addon_module()
        config = tmp_path / "shaping.json"
        config.write_text(json.dumps({"rate_kbit": 400}))
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", config)
        shaper._digest = None
        shaper._load()
        assert shaper._config and shaper._down is not None, "shaping to start with"

        config.write_text(json.dumps([1, 2]))
        for _ in range(2):          # twice: a refused file is not re-parsed
            shaper._load()
            assert shaper._config == {}
            assert shaper._down is None

    def test_a_read_failure_does_not_leave_a_good_config_ignored(
        self, tmp_path, monkeypatch,
    ):
        """Regression: shaping stayed dead after one transient read error.

        The failure stopped shaping but kept the digest of the last good read, so
        the unchanged file matched its own digest on every later poll and was
        never parsed again. Nothing was wrong with the file — it was simply never
        looked at again, and the only way out was to rewrite it.
        """
        module = self._addon_module()
        config = tmp_path / "shaping.json"
        config.write_text(json.dumps({"rate_kbit": 400}))
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", config)
        shaper._digest = None
        shaper._load()
        assert shaper._config["rate_kbit"] == 400.0

        real_read = Path.read_bytes
        reads = {"n": 0}

        def one_bad_read(self, *a, **k):
            reads["n"] += 1
            if reads["n"] == 1:
                raise OSError("transient")
            return real_read(self, *a, **k)

        monkeypatch.setattr(Path, "read_bytes", one_bad_read)
        shaper._load()
        assert shaper._config == {}, "not shaping while the file cannot be read"

        shaper._load()          # same unchanged file, and it must be read again
        assert shaper._config["rate_kbit"] == 400.0
        assert shaper._down is not None

    def test_a_falsey_value_where_a_number_belongs_is_refused(
        self, tmp_path, monkeypatch,
    ):
        """``or 0`` turned ``[]``, ``""``, ``false`` and ``null`` into a silent
        zero, so a malformed config read as "nothing was asked for" — the one
        answer that looks exactly like success."""
        module = self._addon_module()
        config = tmp_path / "shaping.json"
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", config)
        for bad in ([], "", None, {}):
            config.write_text(
                json.dumps({"rate_kbit": 400, "latency_ms": bad}),
            )
            shaper._digest = None
            shaper._load()
            assert shaper._config == {}, bad
            assert shaper._down is None, bad

    def test_a_non_numeric_value_does_not_leave_the_old_rate_running(
        self, tmp_path, monkeypatch,
    ):
        """Regression: the new config was stored before its values were coerced,
        so the previous rate kept metering under the new configuration's name."""
        module = self._addon_module()
        config = tmp_path / "shaping.json"
        config.write_text(json.dumps({"rate_kbit": 400}))
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", config)
        shaper._digest = None
        shaper._load()
        assert shaper._down is not None

        config.write_text(json.dumps({"rate_kbit": "fast"}))
        shaper._digest = None
        shaper._load()
        assert shaper._config == {}
        assert shaper._down is None, "a stale bucket must not survive"

    def test_a_good_config_still_loads(self, tmp_path, monkeypatch):
        module = self._addon_module()
        config = tmp_path / "shaping.json"
        config.write_text(json.dumps({"rate_kbit": 400, "latency_ms": 250}))
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", config)
        shaper._digest = None
        shaper._load()
        assert shaper._config["rate_kbit"] == 400.0
        assert shaper._config["latency_ms"] == 250.0
        assert shaper._down is not None
        # THROUGH the conversion, not on either side of it. Both layers were
        # tested — the config in kilobits, the bucket in bytes — and nothing
        # crossed between them, so dropping the ``/ 8`` left every test green
        # while a link asked for 400 kbit ran at 3.2 Mbit and reported 400.
        assert shaper._down._rate == 50_000.0
        assert shaper._up._rate == 50_000.0


class _FakeFlow:
    """The parts of an HTTP flow the shaper touches, and nothing else."""

    def __init__(self, request_body: bytes = b"", response_body: bytes = b""):
        self.request = type("_R", (), {"raw_content": request_body})()
        self.response = type("_S", (), {"raw_content": response_body})()
        self.killed = False

    def kill(self) -> None:
        self.killed = True


class TestShapingHooks:
    """The hooks themselves, executed.

    Nothing in this file used to call ``request`` or ``response`` at all, so four
    behaviors were accepted, validated, written to disk and reported by
    ``get_shaping`` while doing nothing: jitter could be zeroed, the drop branch
    could be disabled, the reload guard could be frozen and the bucket's capacity
    floor removed — all with a green suite. A "flaky network" test would never
    have failed.
    """

    @staticmethod
    def _shaper(tmp_path, monkeypatch, config: dict):
        module = TestShapingArithmetic._addon_module()
        path = tmp_path / "shaping.json"
        path.write_text(json.dumps(config))
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", path)
        shaper._digest = None
        shaper._load()
        return module, shaper, path

    @staticmethod
    def _run(coro, monkeypatch, module):
        """Run a hook, recording what it would have slept for."""
        import asyncio
        waited: list[float] = []

        async def _sleep(seconds):
            waited.append(seconds)

        monkeypatch.setattr(module.asyncio, "sleep", _sleep)
        asyncio.run(coro)
        return waited

    def test_the_delay_is_actually_awaited(self, tmp_path, monkeypatch):
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"latency_ms": 250},
        )
        flow = _FakeFlow()
        waited = self._run(shaper.request(flow), monkeypatch, module)
        assert waited == [pytest.approx(0.25)]

    def test_jitter_widens_the_delay_and_is_not_ignored(
        self, tmp_path, monkeypatch,
    ):
        """Regression: jitter was accepted, stored, reported — and unused.

        ``random.uniform`` is the only source of spread, so pinning it is enough
        to prove the value reaches the wait.
        """
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"latency_ms": 100, "jitter_ms": 40},
        )
        monkeypatch.setattr(module.random, "uniform", lambda low, high: high)
        flow = _FakeFlow()
        waited = self._run(shaper.request(flow), monkeypatch, module)
        assert waited == [pytest.approx(0.14)], "100 ms + the full +40 ms"

        monkeypatch.setattr(module.random, "uniform", lambda low, high: low)
        waited = self._run(shaper.request(_FakeFlow()), monkeypatch, module)
        assert waited == [pytest.approx(0.06)], "100 ms - the full 40 ms"

    def test_a_negative_spread_is_floored_at_zero_not_awaited_backwards(
        self, tmp_path, monkeypatch,
    ):
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"latency_ms": 10, "jitter_ms": 10},
        )
        monkeypatch.setattr(module.random, "uniform", lambda low, high: -1000)
        waited = self._run(shaper.request(_FakeFlow()), monkeypatch, module)
        assert waited == [], "no wait at all, rather than a negative one"

    def test_a_dropped_flow_is_killed(self, tmp_path, monkeypatch):
        """Regression: the branch could be disabled without a single failure."""
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"drop_pct": 5},
        )
        monkeypatch.setattr(module.random, "random", lambda: 0.01)   # 1% < 5%
        flow = _FakeFlow()
        self._run(shaper.request(flow), monkeypatch, module)
        assert flow.killed is True

    def test_a_flow_above_the_drop_threshold_survives(
        self, tmp_path, monkeypatch,
    ):
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"drop_pct": 5},
        )
        monkeypatch.setattr(module.random, "random", lambda: 0.5)     # 50% > 5%
        flow = _FakeFlow()
        self._run(shaper.request(flow), monkeypatch, module)
        assert flow.killed is False

    def test_a_killed_flow_is_not_also_delayed(self, tmp_path, monkeypatch):
        """A dead flow has nothing left to pace, and waiting on it would hold a
        connection open that was just refused."""
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"drop_pct": 100, "latency_ms": 250},
        )
        monkeypatch.setattr(module.random, "random", lambda: 0.0)
        waited = self._run(shaper.request(_FakeFlow()), monkeypatch, module)
        assert waited == []

    def test_a_response_body_is_paced(self, tmp_path, monkeypatch):
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"rate_kbit": 400},
        )
        # 50 000 bytes empties the one-second bucket; the next 50 000 wait a
        # second. Two calls, because a full bucket lets the first through.
        flow = _FakeFlow(response_body=b"x" * 50_000)
        assert self._run(shaper.response(flow), monkeypatch, module) == []
        waited = self._run(
            shaper.response(_FakeFlow(response_body=b"x" * 50_000)),
            monkeypatch, module,
        )
        assert waited and waited[0] == pytest.approx(1.0, abs=0.05)

    def test_an_unshaped_proxy_does_nothing_in_either_hook(
        self, tmp_path, monkeypatch,
    ):
        module = TestShapingArithmetic._addon_module()
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", tmp_path / "absent.json")
        flow = _FakeFlow(response_body=b"x" * 100_000)
        assert self._run(shaper.request(flow), monkeypatch, module) == []
        assert self._run(shaper.response(flow), monkeypatch, module) == []
        assert flow.killed is False

    def test_a_config_change_is_picked_up_between_flows(
        self, tmp_path, monkeypatch,
    ):
        """Hot reload, through the hook rather than by calling ``_load`` by hand.

        Every config test set ``_mtime`` itself to get past the guard, so the
        guard could have been frozen shut and nothing would have noticed.
        """
        module, shaper, path = self._shaper(
            tmp_path, monkeypatch, {"latency_ms": 250},
        )
        assert self._run(
            shaper.request(_FakeFlow()), monkeypatch, module,
        ) == [pytest.approx(0.25)]

        path.write_text(json.dumps({"latency_ms": 500}))
        assert self._run(
            shaper.request(_FakeFlow()), monkeypatch, module,
        ) == [pytest.approx(0.5)], "the new file must take effect"

        path.unlink()
        assert self._run(
            shaper.request(_FakeFlow()), monkeypatch, module,
        ) == [], "and removing it stops shaping"

    def test_a_request_body_is_paced_too(self, tmp_path, monkeypatch):
        """The mirror of the response test, and the reason it is needed.

        Disabling the upload branch entirely — or pointing both directions at one
        bucket — left every test green: the bucket was constructed and asserted
        on, never used. The contract says each direction is paced independently,
        so this drives the request leg and then proves the response leg still has
        its own allowance.
        """
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"rate_kbit": 400},
        )
        big = b"x" * 50_000
        # First request empties the upload bucket; the second waits a second.
        assert self._run(
            shaper.request(_FakeFlow(request_body=big)), monkeypatch, module,
        ) == []
        waited = self._run(
            shaper.request(_FakeFlow(request_body=big)), monkeypatch, module,
        )
        assert waited and waited[0] == pytest.approx(1.0, abs=0.05)

        # The download direction must be untouched by all that: separate bucket.
        assert self._run(
            shaper.response(_FakeFlow(response_body=big)), monkeypatch, module,
        ) == [], "one direction's traffic must not spend the other's allowance"

    def test_a_response_without_a_body_is_not_paced(self, tmp_path, monkeypatch):
        """A flow can reach the response hook with no response at all — an
        upstream that never answered. The guard for it was never exercised."""
        module, shaper, _ = self._shaper(
            tmp_path, monkeypatch, {"rate_kbit": 400},
        )
        flow = _FakeFlow()
        flow.response = None
        assert self._run(shaper.response(flow), monkeypatch, module) == []

    def test_the_capacity_floor_keeps_a_tiny_rate_usable(self):
        """Without the floor a rate under 1 byte/s gives a zero-capacity bucket.

        The tolerance used to be ``abs=1.0`` around 1.0, which covered the
        mutant's exact answer — the assertion could not fail. A rate of half a
        byte per second must make one byte wait two seconds, and that number is
        only right when the floor is there.
        """
        module = TestShapingArithmetic._addon_module()
        bucket = module._Bucket(0.5, clock=_FakeClock())
        assert bucket._capacity == 1.0
        assert bucket.consume(2) == pytest.approx(2.0)


    def test_a_same_tick_same_length_rewrite_is_noticed(
        self, tmp_path, monkeypatch,
    ):
        """The realistic version of the reload bug, and the one two earlier fixes
        both missed.

        The writer always emits the same four keys, so a rate change of 400 → 900
        is 85 bytes either way. On a coarse filesystem the two writes also share a
        timestamp. Neither ``mtime`` nor ``(mtime, size)`` can tell them apart, so
        the second was ignored while the driver reported the new figure from
        memory. Equal length here is the normal case, not a contrived one.
        """
        import os
        module = TestShapingArithmetic._addon_module()
        path = tmp_path / "shaping.json"
        first = json.dumps(
            {"rate_kbit": 400, "latency_ms": 0.0, "jitter_ms": 0.0,
             "drop_pct": 0.0}, indent=2,
        )
        second = first.replace("400", "900")
        assert len(first) == len(second), "the premise: identical length"

        path.write_text(first)
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", path)
        shaper._digest = None
        shaper._load()
        assert shaper._down._rate == 50_000.0

        stat = path.stat()
        path.write_text(second)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        shaper._load()
        assert shaper._config["rate_kbit"] == 900.0
        assert shaper._down._rate == 112_500.0, (
            "and the bucket has to follow, not just the reported figure"
        )

    def test_a_bad_file_is_read_once_but_its_replacement_is_read(
        self, tmp_path, monkeypatch,
    ):
        """The saving must not cost the recovery.

        Refusing a file has to stop it being re-read on every flow, and must NOT
        stop the good file that replaces it from being read.
        """
        module = TestShapingArithmetic._addon_module()
        path = tmp_path / "shaping.json"
        path.write_text("{not json")
        shaper = module.TrafficShaper()
        monkeypatch.setattr(shaper, "_path", path)
        shaper._digest = None

        # Counts PARSES, not reads. The file is read to hash it on every call by
        # design, so counting reads was true whether or not the early return
        # existed — the assertion could not fail. What "read once" actually means
        # is that an unusable file is not parsed and applied again.
        parses = []
        original = module.json.loads

        def _counted(*args, **kwargs):
            parses.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(module.json, "loads", _counted)

        shaper._load()
        assert shaper._config == {}
        # The digest of a REFUSED file still has to be recorded, or the name of
        # this test is a lie. Capturing it without this assertion made the
        # comparison below ``None == None`` under a mutant that recorded the
        # digest only after validation.
        assert shaper._digest is not None
        digest = shaper._digest
        before = len(parses)

        shaper._load()
        assert shaper._digest == digest
        assert len(parses) == before, (
            "an unusable file must not be parsed again while it is unchanged"
        )
        assert shaper._config == {}

        path.write_text(json.dumps({"rate_kbit": 400}))
        shaper._load()
        assert shaper._down._rate == 50_000.0, "the replacement must be read"


class TestShapingSeam:
    """The joins, driven end to end rather than from either side.

    Every previous round of tests covered both halves of a boundary and nothing
    across it: the driver's key name and the renderer's, kilobits and bytes, the
    constructed bucket and the used one. Each time the mutation survived. These
    tests cross the boundary instead.
    """

    @pytest.fixture(autouse=True)
    def _session(self, a_running_proxy):
        """Shaping needs a running proxy. See :func:`a_running_proxy`."""


    @staticmethod
    def _shaper_for(module, driver):
        """A shaper that derives its own path, the way the real addon does.

        Handing it a path computed by the test pins the file NAME and nothing
        else: the directory half of the join stayed unchecked, and pointing the
        addon at ``/tmp/wrong-dir`` left every test passing. Setting ``MOCK_DIR``
        — which is what the driver patches into the installed addon — makes
        ``__init__`` compute the whole path itself.
        """
        module.MitmproxyMockAddon.MOCK_DIR = str(driver.directories.mocks)
        shaper = module.TrafficShaper()
        shaper._digest = None
        return shaper

    def test_the_driver_writes_the_file_the_addon_reads(self, driver, tmp_path):
        """The name AND the directory are a contract between two processes.

        The driver writes the file; a standalone script reads it and cannot import
        the driver to agree on where. So the agreement is checked by writing with
        one and reading with the other, with the reader deriving its own path.
        """
        module = TestShapingArithmetic._addon_module()
        driver.shape(rate_kbit=400, latency_ms=250)

        written = Path(driver.directories.mocks) / module.TrafficShaper.CONFIG_NAME
        assert written.exists(), (
            "the addon looks for "
            f"{module.TrafficShaper.CONFIG_NAME!r}, the driver wrote something else"
        )

        shaper = self._shaper_for(module, driver)
        assert Path(shaper._path) == written, (
            "the addon derived a different path than the driver wrote to"
        )
        shaper._load()
        assert shaper._down._rate == 50_000.0, "400 kbit/s, read back through the file"
        assert shaper._config["latency_ms"] == 250.0

    def test_clearing_through_the_driver_stops_the_addon_shaping(
        self, driver, tmp_path,
    ):
        """The same seam in the other direction."""
        module = TestShapingArithmetic._addon_module()
        driver.shape(rate_kbit=400)
        shaper = self._shaper_for(module, driver)
        shaper._load()
        assert shaper._down is not None

        driver.clear_shaping()
        shaper._load()
        assert shaper._config == {}
        assert shaper._down is None

    def test_shaping_again_after_a_clear_takes_effect(self, driver):
        """Regression that survived three rounds: the missing-file path.

        ``shape(400)`` → ``clear_shaping()`` → ``shape(400)`` writes byte-identical
        content. If the digest of the pre-clear file is still remembered, the third
        step matches it and is ignored — leaving the proxy unshaped for good while
        the driver reports 400 from memory. The clear has to forget what it read.
        """
        module = TestShapingArithmetic._addon_module()
        driver.shape(rate_kbit=400)
        shaper = self._shaper_for(module, driver)
        shaper._load()
        assert shaper._down is not None

        driver.clear_shaping()
        shaper._load()
        assert shaper._down is None

        driver.shape(rate_kbit=400)
        shaper._load()
        assert shaper._down is not None, "the identical config must apply again"
        assert shaper._down._rate == 50_000.0

    def test_shaping_is_refused_when_the_session_cannot_shape(self, driver):
        """The fallback addon carries no shaper.

        Writing a config file that nothing in the process reads and calling it
        success is the worst of the available answers, so the driver says it
        cannot.
        """
        driver._shaping_available = False
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is False
        assert "unavailable" in reply["error"].lower()
        assert json.loads(driver.get_shaping()) == {}
        assert not (
            Path(driver.directories.mocks) / "shaping.json"
        ).exists()


class TestShapingDegradedConfigs:
    """Configs that are valid, partial, or broken while something was running.

    Each of these guards existed and none was executed, so each could be deleted
    with a green suite — while the failure it prevents happens on every flow.
    """

    def test_a_delay_only_config_does_not_crash_the_response_hook(
        self, tmp_path, monkeypatch,
    ):
        """The most natural request in proxy mode, and it hit an AttributeError.

        Real delay lives only on the proxy path, so ``ip set --latency 250`` with
        no rate is what an operator asks for. That leaves the config non-empty and
        the bucket ``None``, and the response hook reached for ``.consume`` on it.
        """
        module, shaper, _ = TestShapingHooks._shaper(
            tmp_path, monkeypatch, {"latency_ms": 250, "rate_kbit": 0},
        )
        assert shaper._config, "the config is not empty"
        assert shaper._down is None, "and there is no bucket to pace with"
        waited = TestShapingHooks._run(
            shaper.response(_FakeFlow(response_body=b"x" * 10_000)),
            monkeypatch, module,
        )
        assert waited == [], "no pacing without a rate, and no exception either"

    def test_a_delay_only_config_still_delays_the_request(
        self, tmp_path, monkeypatch,
    ):
        module, shaper, _ = TestShapingHooks._shaper(
            tmp_path, monkeypatch, {"latency_ms": 250},
        )
        waited = TestShapingHooks._run(
            shaper.request(_FakeFlow()), monkeypatch, module,
        )
        assert waited == [pytest.approx(0.25)]

    def test_a_file_that_breaks_while_shaping_stops_the_shaping(
        self, tmp_path, monkeypatch,
    ):
        """good → bad, the direction no test took.

        The atomic write in the driver cannot produce this, but an edit by hand or
        a partial sync on the filesystems the digest comment names can. Left
        unhandled, the previous rate kept metering under a config that no longer
        parses — the exact state ``_load`` promises never to be in.
        """
        module, shaper, path = TestShapingHooks._shaper(
            tmp_path, monkeypatch, {"rate_kbit": 400},
        )
        assert shaper._down._rate == 50_000.0

        path.write_text("{trunca")
        shaper._load()
        assert shaper._config == {}, "the old config must not survive"
        assert shaper._down is None, "and neither must the old bucket"

        # And the hooks stay harmless in that state.
        assert TestShapingHooks._run(
            shaper.response(_FakeFlow(response_body=b"x" * 10_000)),
            monkeypatch, module,
        ) == []


class TestShapingAvailability:
    """The edge from "which addon got deployed" to "can we shape".

    The refusal message was pinned by setting the flag by hand, which proves the
    flag is read and nothing about who sets it. Deleting the assignment left forty
    tests green and shaping a silent no-op again.
    """

    @pytest.fixture(autouse=True)
    def _session(self, a_running_proxy):
        """Shaping needs a running proxy. See :func:`a_running_proxy`."""


    @staticmethod
    def _hide_bundled_addon(monkeypatch):
        """Make the package look as though it shipped without the addon."""
        original = pathlib.Path.exists

        def _exists(self):
            if self.name == "bundled_addon.py":
                return False
            return original(self)

        monkeypatch.setattr(pathlib.Path, "exists", _exists)

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_session_on_the_fallback_addon_cannot_shape(
        self, mock_popen, driver, tmp_path, monkeypatch,
    ):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        self._hide_bundled_addon(monkeypatch)
        # This test IS the lifecycle: start from a stopped proxy so the addon is
        # really deployed, rather than from the session the fixture hands out.
        driver._process = None

        driver.start("mock", False, "")

        deployed = (tmp_path / "addons" / "mock_addon.py").read_text()
        assert "TrafficShaper" not in deployed, "the fallback has no shaper"
        reply = json.loads(driver.shape(rate_kbit=400))
        assert reply["ok"] is False
        assert "fallback" in reply["error"]
        assert not (tmp_path / "mocks" / "shaping.json").exists(), (
            "and no config was written for nobody to read"
        )

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_session_on_the_bundled_addon_can_shape(
        self, mock_popen, driver, tmp_path,
    ):
        """The other side of the same edge, so the flag cannot be pinned false."""
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc
        driver._process = None

        driver.start("mock", False, "")

        deployed = (tmp_path / "addons" / "mock_addon.py").read_text()
        assert "TrafficShaper" in deployed
        assert json.loads(driver.shape(rate_kbit=400))["ok"] is True

    @patch("jumpstarter_driver_mitmproxy.driver.subprocess.Popen")
    def test_a_restart_drops_the_shaping(self, mock_popen, driver, tmp_path):
        """Documented rather than assumed, because it is surprising.

        ``restart`` is a stop and a start, and a start always begins unshaped. So a
        restart quietly removes a weak link somebody configured — which is the
        right behavior for a fresh session, and worth a test saying so out loud.
        """
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")
        driver.shape(rate_kbit=400)
        assert (tmp_path / "mocks" / "shaping.json").exists()

        driver.restart("mock", False, "")
        assert not (tmp_path / "mocks" / "shaping.json").exists()
        assert json.loads(driver.get_shaping()) == {}

    def test_a_failed_write_leaves_no_temporary_file_behind(
        self, driver, tmp_path, monkeypatch,
    ):
        """The cleanup branch in the atomic write was never executed.

        The empty glob alone would also pass if no temporary file were ever
        created, so the spy is what makes this test mean "cleaned up" rather than
        "nothing to clean up".
        """
        import tempfile as _tempfile
        real_named = _tempfile.NamedTemporaryFile
        made: list[str] = []

        def spy(*a, **k):
            handle = real_named(*a, **k)
            made.append(handle.name)
            return handle

        monkeypatch.setattr(_tempfile, "NamedTemporaryFile", spy)
        import json as _json
        monkeypatch.setattr(
            _json, "dump",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk is full")),
        )
        assert not json.loads(driver.shape(rate_kbit=400))["ok"]

        assert made, "a temporary file was created, so there was one to remove"
        assert not any(Path(p).exists() for p in made), made
        leftovers = list((tmp_path / "mocks").glob("*.tmp"))
        assert leftovers == [], leftovers

    @pytest.mark.parametrize(
        ("where", "boom"),
        [("json.dump", "payload is not serializable"),
         ("os.replace", "cross-device link")],
    )
    def test_a_failed_write_never_touches_a_descriptor_by_number(
        self, driver, tmp_path, monkeypatch, where, boom,
    ):
        """The descriptor is closed exactly once, by whoever owns it.

        The shape this replaced held a bare number: ``mkstemp`` handed one over,
        ``os.fdopen`` took it, the ``with`` closed it, and the failure path closed
        the number AGAIN. That second close is not the harmless ``EBADF`` the code
        assumed — by then the number can belong to one of this driver's own threads
        (the capture server, its read loop), the call SUCCEEDS, and something else
        loses its file. A guard flag narrowed that window but could not close it:
        an ``io.open`` failure after the raw file took the descriptor left the flag
        claiming ownership of an already-closed number.

        Pinned in the two directions that shape can go wrong, over both failure
        points, because the previous version of this test injected only one of
        them: no descriptor leaks (nothing was left unclosed) and our code issues
        no ``os.close`` at all (nothing was closed twice). Neither can be read off
        the reply, which is why both are watched directly.
        """
        import os as _os
        import tempfile as _tempfile

        real_named = _tempfile.NamedTemporaryFile
        made: list[str] = []

        def spy(*a, **k):
            handle = real_named(*a, **k)
            made.append(handle.name)
            return handle

        monkeypatch.setattr(_tempfile, "NamedTemporaryFile", spy)

        closed: list[int] = []
        real_close = _os.close

        def recording_close(fd):
            closed.append(fd)
            return real_close(fd)

        monkeypatch.setattr(_os, "close", recording_close)

        def raiser(*a, **k):
            raise OSError(boom)

        if where == "json.dump":
            import json as _json
            monkeypatch.setattr(_json, "dump", raiser)
        else:
            monkeypatch.setattr(_os, "replace", raiser)

        # ``/dev/fd``, not ``/proc/self/fd``: the same thing on Linux (a
        # symlink to it) and a real filesystem on macOS, which this project's
        # CI matrix includes — the /proc form would have failed there for a
        # reason that has nothing to do with what is under test.
        before = set(_os.listdir("/dev/fd"))
        reply = json.loads(driver.shape(rate_kbit=400))
        after = set(_os.listdir("/dev/fd"))

        assert not reply["ok"]
        assert boom in reply["error"], (
            f"the test has to fail where it aimed, not somewhere earlier: "
            f"{reply['error']}"
        )
        assert made, "a temporary file was created, so a descriptor existed"
        assert closed == [], (
            f"this code must not close a descriptor by number; it closed {closed}"
        )
        assert after <= before, f"descriptor leaked: {sorted(after - before)}"
        assert list((tmp_path / "mocks").glob("*.tmp")) == []


class TestAddonsLogThroughTheStandardLibrary:
    """Addons log through the standard library, not through ``ctx.log``.

    Two measured reasons, and neither is "mocking was broken in production" — it
    was not. Inside a running master ``ctx.log`` exists and works, because the
    master assigns it; it is simply DEPRECATED there, and every call says so.

    What it does break is every context without a master: ``ctx`` declares ``log``
    under ``TYPE_CHECKING`` alone, so the attribute is absent and any use of it
    raises ``AttributeError`` inside the calling hook. That is how a test harness
    loads the addon, and how any tooling that imports it would — which is exactly
    why nothing here had ever run the addon against the real mitmproxy.
    """

    def test_ctx_log_is_absent_without_a_master(self):
        """The premise: outside a running master, ``ctx.log`` does not exist.

        Checked through behavior only, not mitmproxy's source, so the test keeps
        holding if a release drops the deprecated attribute altogether.
        """
        pytest.importorskip("mitmproxy")
        from mitmproxy import ctx
        assert not hasattr(ctx, "log"), (
            "outside a master it is absent, which is what makes reaching for it "
            "fatal to a hook in every context that has no master"
        )

    def test_no_shipped_addon_source_calls_ctx_log(self):
        """Both addons: the bundled one, and the fallback that ``driver.py``
        generates when the bundled file is missing. The fallback is the one no
        test can execute, so a source check is the only guard it can have."""
        import re
        here = Path(__file__).parent
        for name in ("bundled_addon.py", "driver.py"):
            source = (here / name).read_text()
            calls = re.findall(r"ctx\.log\.\w+\(", source)
            assert calls == [], f"{name}: {calls}"

    def test_the_mock_hook_installs_a_response_under_the_real_mitmproxy(
        self, tmp_path,
    ):
        """Executed against the genuine mitmproxy, in a subprocess.

        The addon used to be loaded here with mitmproxy stubbed in through
        ``sys.modules.setdefault``, and a stub with a forgiving ``ctx`` hides
        anything that reaches for a member the real module only has while a master
        runs. A subprocess gets the real module — no master, which is this
        harness's own situation — and answers the question that matters: does a
        mocked request come back.
        """
        pytest.importorskip("mitmproxy")
        import subprocess
        import sys
        import textwrap

        mocks = tmp_path / "mocks"
        mocks.mkdir()
        (mocks / "endpoints.json").write_text(
            json.dumps({"GET /path": {"status": 201, "body": {"ok": True}}}),
        )
        script = textwrap.dedent(
            """
            import asyncio, json, pathlib, sys, types
            mocks, source, spool = sys.argv[1], sys.argv[2], sys.argv[3]
            code = pathlib.Path(source).read_text().replace(
                "addons = [MitmproxyMockAddon(), TrafficShaper()]", "addons = []",
            )
            module = types.ModuleType("addon_under_test")
            exec(compile(code, "bundled_addon.py", "exec"), module.__dict__)
            # Redirected before constructing: the addon spools captures into a
            # fixed path under /opt that a test must not need.
            module.CAPTURE_SPOOL_DIR = spool
            module.MitmproxyMockAddon.MOCK_DIR = mocks

            from mitmproxy import ctx
            assert not hasattr(ctx, "log"), "nothing to prove if ctx.log exists"
            from mitmproxy.test import tflow

            addon = module.MitmproxyMockAddon()
            flow = tflow.tflow()                    # GET /path
            asyncio.run(addon.request(flow))
            assert flow.response is not None, "no response was installed"
            assert flow.response.status_code == 201, flow.response.status_code
            print("OK")
            """,
        )
        result = subprocess.run(
            [
                sys.executable, "-c", script, str(mocks),
                str(Path(__file__).parent / "bundled_addon.py"),
                str(tmp_path / "spool"),
            ],
            capture_output=True, text=True, timeout=120, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "OK" in result.stdout


class TestCaptureBufferPositions:
    """Test the monotonic-position bookkeeping in _SizeLimitedCaptureBuffer.

    watch_captured_requests relies on these to translate an absolute
    "how many entries have ever been appended" position into a live slice
    index, across both trims and clears.
    """

    def test_total_appended_survives_clear(self):
        buf = _SizeLimitedCaptureBuffer()
        buf.append({"a": 1})
        buf.append({"a": 2})
        assert buf.total_appended == 2
        buf.clear()
        # Nothing new has been appended, so the absolute position does not
        # reset just because the buffer is now empty.
        assert buf.total_appended == 2
        assert len(buf) == 0
        buf.append({"a": 3})
        assert buf.total_appended == 3

    def test_total_appended_survives_trim(self):
        buf = _SizeLimitedCaptureBuffer(max_bytes=1)
        # Every append exceeds the tiny byte budget, so each one but the
        # last gets trimmed away immediately.
        for i in range(5):
            buf.append({"i": i})
        assert len(buf) == 1
        assert buf.total_appended == 5

    def test_index_for_position_clamps_to_zero_after_discard(self):
        buf = _SizeLimitedCaptureBuffer()
        buf.append({"a": 1})
        buf.append({"a": 2})
        buf.clear()
        buf.append({"a": 3})
        # Position 1 pointed at an entry that clear() discarded; it must
        # clamp to the start of what remains rather than go negative.
        assert buf.index_for_position(1) == 0

    def test_index_for_position_tracks_live_entries_after_trim(self):
        buf = _SizeLimitedCaptureBuffer()
        for i in range(10):
            buf.append({"i": i})
        # Simulate a trim of the first three entries directly.
        del buf[:3]
        buf._discarded += 3
        # Position 5 (the sixth entry ever appended) is now at live index 2.
        assert buf.index_for_position(5) == 2
        assert buf[buf.index_for_position(5)] == {"i": 5}


class TestWatchCapturedRequests:
    """Test that watch_captured_requests survives buffer mutation.

    watch_captured_requests holds ``_capture_lock`` for the whole of its
    initial replay (the ``with: for req in ...: yield`` block does not
    release it between individual yields -- a plain ``yield`` does not run
    the context manager's ``__exit__``). The lock is only released once the
    generator is resumed *after* the last existing entry and discovers there
    is nothing more to iterate. These tests schedule that resuming call as a
    background task and yield once (``await asyncio.sleep(0)``) so it runs
    up to its own suspension point -- releasing the lock -- before the test
    mutates the buffer, mirroring how a real reader thread would only
    observe the lock free once the replay is actually done.
    """

    def test_watch_yields_existing_then_new_entries(self, driver):
        async def _scenario():
            with driver._capture_lock:
                driver._captured_requests.append({"method": "GET", "path": "/a"})
            agen = driver.watch_captured_requests()

            first = await agen.__anext__()
            assert json.loads(first)["path"] == "/a"

            next_task = asyncio.create_task(agen.__anext__())
            await asyncio.sleep(0)  # let it discover StopIteration, releasing the lock

            with driver._capture_lock:
                driver._captured_requests.append({"method": "GET", "path": "/b"})

            second = await next_task
            assert json.loads(second)["path"] == "/b"

        asyncio.run(_scenario())

    def test_watch_survives_clear_between_polls(self, driver):
        """A watcher that already consumed everything must not re-yield or
        hang after the buffer it is watching is cleared and refilled."""
        async def _scenario():
            with driver._capture_lock:
                driver._captured_requests.append({"method": "GET", "path": "/a"})
            agen = driver.watch_captured_requests()

            first = await agen.__anext__()
            assert json.loads(first)["path"] == "/a"

            next_task = asyncio.create_task(agen.__anext__())
            await asyncio.sleep(0)

            with driver._capture_lock:
                driver._captured_requests.clear()
                driver._captured_requests.append({"method": "GET", "path": "/b"})

            second = await next_task
            assert json.loads(second)["path"] == "/b"

        asyncio.run(_scenario())

    def test_watch_survives_trim_between_polls(self, driver):
        """A watcher must not skip an entry that shifted toward index 0
        because older entries ahead of it were auto-trimmed away."""
        async def _scenario():
            with driver._capture_lock:
                driver._captured_requests.append({"method": "GET", "path": "/old"})
                driver._captured_requests.append({"method": "GET", "path": "/seen"})
            agen = driver.watch_captured_requests()

            first = await agen.__anext__()
            assert json.loads(first)["path"] == "/old"
            second = await agen.__anext__()
            assert json.loads(second)["path"] == "/seen"

            next_task = asyncio.create_task(agen.__anext__())
            await asyncio.sleep(0)

            with driver._capture_lock:
                # Simulate a trim that drops "/old" but keeps "/seen", then
                # a new entry arrives.
                del driver._captured_requests[:1]
                driver._captured_requests._discarded += 1
                driver._captured_requests.append({"method": "GET", "path": "/new"})

            third = await next_task
            # Only the entry that arrived after the watcher's last position
            # should be yielded -- not "/seen" again.
            assert json.loads(third)["path"] == "/new"

        asyncio.run(_scenario())


class TestGetResponseBodyCap:
    """Test the fixed read cap on get_response_body's spool file reads."""

    def test_reads_small_file_without_truncation(self, driver, tmp_path):
        body_file = tmp_path / "body.json"
        body_file.write_text('{"ok": true}')
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/api/v1/status",
                "response_body_file": str(body_file),
                "response_status": 200,
            })
        result = json.loads(driver.get_response_body("status"))
        assert result["body"] == {"ok": True}
        assert result["truncated"] is False

    def test_truncates_at_fixed_cap(self, driver, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "jumpstarter_driver_mitmproxy.driver._GET_RESPONSE_BODY_MAX_BYTES", 10,
        )
        body_file = tmp_path / "body.txt"
        body_file.write_text("x" * 100)
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/api/v1/large",
                "response_body_file": str(body_file),
                "response_status": 200,
            })
        result = json.loads(driver.get_response_body("large"))
        assert result["truncated"] is True
        assert result["body"] == "x" * 10

    def test_missing_file_reports_error(self, driver, tmp_path):
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/api/v1/gone",
                "response_body_file": str(tmp_path / "does-not-exist.txt"),
                "response_status": 200,
            })
        result = json.loads(driver.get_response_body("gone"))
        assert "error" in result

    def test_oserror_reading_file_reports_error(self, driver, tmp_path, monkeypatch):
        body_file = tmp_path / "body.txt"
        body_file.write_text("hello")
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/api/v1/broken",
                "response_body_file": str(body_file),
                "response_status": 200,
            })

        real_open = open

        def _boom(path, mode="r", *a, **k):
            if str(path) == str(body_file):
                raise OSError("simulated disk failure")
            return real_open(path, mode, *a, **k)

        monkeypatch.setattr(
            "jumpstarter_driver_mitmproxy.driver.open", _boom, raising=False,
        )
        result = json.loads(driver.get_response_body("broken"))
        assert "error" in result


class TestExportCapturedRequestsBudget:
    """Test the reply-size budget in export_captured_requests."""

    def test_stops_reading_bodies_past_budget(self, driver, tmp_path, monkeypatch):
        # The budget covers the first entry with its body and the second without one,
        # so the second must be skipped rather than read.
        #
        # 1100, and the bodies 500 characters each, because the budget now prices whole
        # serialized entries rather than decoded body length. Measured on these two: the
        # first costs 767 bytes with its body, leaving 331 — under the 767 the second
        # would need, over the 325 its metadata costs. The old 5 was below one entry's
        # metadata, so nothing was kept at all. A 5-character body cannot show this
        # either: the skip marker is longer than the body, so dropping it saves nothing.
        monkeypatch.setattr(
            "jumpstarter_driver_mitmproxy.driver._CAPTURES_MAX_BYTES", 1100,
        )
        first_file = tmp_path / "first.json"
        first_file.write_text("x" * 500)
        second_file = tmp_path / "second.json"
        second_file.write_text("y" * 500)
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/a",
                "response_body_file": str(first_file),
                "response_content_type": "application/json",
            })
            driver._captured_requests.append({
                "method": "GET",
                "path": "/b",
                "response_body_file": str(second_file),
                "response_content_type": "application/json",
            })
        result = json.loads(driver.export_captured_requests(max_body_size=1048576))
        assert result[0]["response_body"] == "x" * 500
        assert result[1]["response_body"] is None
        assert result[1]["response_body_skipped"] == "export size budget exceeded"
        # The whole reply, not the sum of the bodies, is what has to fit: that is the
        # unit the transport charges and the one this budget previously miscounted.
        assert len(json.dumps(result).encode("utf-8")) <= 1100

    @pytest.mark.parametrize(
        ("label", "char"),
        [("quotes, which every JSON body is full of", '"'),
         ("non-ASCII, three UTF-8 bytes each", "\u6f22"),
         ("control characters, six bytes escaped", "\x00")],
    )
    def test_the_reply_fits_whatever_the_bodies_are_made_of(
        self, driver, tmp_path, monkeypatch, label, char,
    ):
        """Regression: the budget counted decoded characters, which is not what travels.

        A character can cost up to six bytes once serialized, and the metadata, headers and
        request body were not counted at all. Measured on a JSON payload that is 14%
        quote characters, six bodies totaling 3,706,998 characters serialized to 4,235,760
        bytes — past the 4 MB gRPC message limit, while the old count read 3.7 MB and let
        them through. Quotes are the case that matters: a JSON body is made of them.
        """
        # 30,000 so one body fits even in the worst case, leaving the rest to be shed:
        # json.dumps escapes non-ASCII and control characters as \uXXXX, six bytes each,
        # so 4000 of them serialize to 24,000. That escaping is exactly what the old
        # count missed, and it is why the budget is measured after serializing.
        monkeypatch.setattr(
            "jumpstarter_driver_mitmproxy.driver._CAPTURES_MAX_BYTES", 30_000,
        )
        for i in range(12):
            spool = tmp_path / f"body{i}.json"
            spool.write_text(char * 4000)
            with driver._capture_lock:
                driver._captured_requests.append({
                    "method": "GET",
                    "path": f"/{i}",
                    "response_body_file": str(spool),
                    "response_content_type": "application/json",
                })

        raw = driver.export_captured_requests(max_body_size=1048576)

        assert len(raw.encode("utf-8")) <= 30_000, (
            f"{label}: reply is {len(raw.encode('utf-8'))} bytes, over the budget"
        )
        result = json.loads(raw)
        # Some bodies have to survive, or the test would pass on an export that returns
        # nothing — and every entry kept must still name its request.
        assert any(e.get("response_body") for e in result), label
        assert all(e["path"] for e in result), label

    def test_an_unreadable_spool_file_does_not_lose_the_whole_export(
        self, driver, tmp_path, monkeypatch,
    ):
        """One bad spool file must cost its own body, not every captured request.

        Only FileNotFoundError was caught, so a spool file that exists but cannot be read —
        permissions, a full descriptor table, an I/O error — propagated out of the loop and
        failed the whole call. get_response_body already handles both; this site did not.
        """
        good = tmp_path / "good.json"
        good.write_text('{"ok": true}')
        broken = tmp_path / "broken.json"
        broken.write_text("unreadable")
        for path, spool in (("/good", good), ("/broken", broken)):
            with driver._capture_lock:
                driver._captured_requests.append({
                    "method": "GET",
                    "path": path,
                    "response_body_file": str(spool),
                    "response_content_type": "application/json",
                })

        real_open = open

        def _boom(path, mode="r", *a, **k):
            if str(path) == str(broken):
                raise PermissionError(13, "Permission denied")
            return real_open(path, mode, *a, **k)

        monkeypatch.setattr(
            "jumpstarter_driver_mitmproxy.driver.open", _boom, raising=False,
        )

        result = json.loads(driver.export_captured_requests(max_body_size=1048576))

        assert len(result) == 2, "the readable request must still be exported"
        assert result[0]["response_body"] == '{"ok": true}'
        assert result[1]["response_body"] is None
        assert "Permission denied" in result[1]["response_body_error"], result[1]
        assert "response_body_truncated" not in result[1]

    def _export_one(self, driver, tmp_path, content: bytes, max_body_size: int) -> dict:
        spool = tmp_path / "body.txt"
        spool.write_bytes(content)
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/a",
                "response_body_file": str(spool),
                "response_content_type": "text/plain",
            })
        return json.loads(driver.export_captured_requests(max_body_size=max_body_size))[0]

    def test_a_body_exactly_at_the_cap_is_not_truncated(self, driver, tmp_path):
        entry = self._export_one(driver, tmp_path, b"x" * 16, max_body_size=16)
        assert entry["response_body"] == "x" * 16
        assert "response_body_truncated" not in entry

    def test_the_cap_counts_bytes_not_characters(self, driver, tmp_path):
        # Three UTF-8 bytes per character: a character cap would read all 16.
        entry = self._export_one(driver, tmp_path, "漢".encode() * 16, max_body_size=12)
        assert entry["response_body"] == "漢" * 4
        assert entry["response_body_truncated"] is True

    def test_reads_bodies_within_budget(self, driver, tmp_path):
        body_file = tmp_path / "body.json"
        body_file.write_text('{"ok": true}')
        with driver._capture_lock:
            driver._captured_requests.append({
                "method": "GET",
                "path": "/a",
                "response_body_file": str(body_file),
                "response_content_type": "application/json",
            })
        result = json.loads(driver.export_captured_requests(max_body_size=1048576))
        assert result[0]["response_body"] == '{"ok": true}'
        assert "response_body_skipped" not in result[0]
