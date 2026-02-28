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


@pytest.fixture
def deep_merge_patch():
    """Import _deep_merge_patch lazily to avoid module-level side effects."""
    import importlib
    import sys
    # Temporarily mock Path.mkdir to prevent /opt/jumpstarter creation
    original_mkdir = Path.mkdir

    def safe_mkdir(self, *args, **kwargs):
        if str(self).startswith("/opt/"):
            return
        return original_mkdir(self, *args, **kwargs)

    Path.mkdir = safe_mkdir
    try:
        if "jumpstarter_driver_mitmproxy.bundled_addon" in sys.modules:
            mod = sys.modules["jumpstarter_driver_mitmproxy.bundled_addon"]
        else:
            mod = importlib.import_module(
                "jumpstarter_driver_mitmproxy.bundled_addon"
            )
        return mod._deep_merge_patch
    finally:
        Path.mkdir = original_mkdir


@pytest.fixture
def apply_patches(deep_merge_patch):
    """Import _apply_patches lazily."""
    import sys
    mod = sys.modules["jumpstarter_driver_mitmproxy.bundled_addon"]
    return mod._apply_patches


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

    def test_missing_key_raises(self, deep_merge_patch):
        target = {"a": 1}
        with pytest.raises(KeyError):
            deep_merge_patch(target, {"nonexistent[0]": "val"})


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
