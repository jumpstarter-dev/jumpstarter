"""
Tests for the mitmproxy Jumpstarter driver and client.

These tests verify the driver/client contract using Jumpstarter's
local testing harness (no real hardware or network needed).
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter_driver_mitmproxy.driver import MitmproxyDriver


@pytest.fixture
def driver(tmp_path):
    """Create a MitmproxyDriver with temp directories."""
    d = MitmproxyDriver(
        listen_host="127.0.0.1",
        listen_port=18080,
        web_host="127.0.0.1",
        web_port=18081,
        confdir=str(tmp_path / "confdir"),
        flow_dir=str(tmp_path / "flows"),
        addon_dir=str(tmp_path / "addons"),
        mock_dir=str(tmp_path / "mocks"),
        ssl_insecure=True,
    )
    yield d
    # Ensure capture server is cleaned up after each test
    d._stop_capture_server()


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


class TestStatus:
    """Test status reporting."""

    def test_status_when_stopped(self, driver):
        info = json.loads(driver.status())
        assert info["running"] is False
        assert info["mode"] == "stopped"
        assert info["pid"] is None

    def test_is_running_when_stopped(self, driver):
        assert driver.is_running() is False


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
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 12345
        mock_popen.return_value = proc

        driver.start("mock", False, "")

        addon_file = tmp_path / "addons" / "mock_addon.py"
        assert addon_file.exists()

        content = addon_file.read_text()
        assert "MitmproxyMockAddon" in content
        assert "addons = [MitmproxyMockAddon()]" in content


class TestCACert:
    """Test CA certificate path reporting."""

    def test_ca_cert_not_found(self, driver):
        result = driver.get_ca_cert_path()
        assert "not found" in result

    def test_ca_cert_found(self, driver, tmp_path):
        cert_path = tmp_path / "confdir" / "mitmproxy-ca-cert.pem"
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text("FAKE CERT")
        result = driver.get_ca_cert_path()
        assert result == str(cert_path)


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
