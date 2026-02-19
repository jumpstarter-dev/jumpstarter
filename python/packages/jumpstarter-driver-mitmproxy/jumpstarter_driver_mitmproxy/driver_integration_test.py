"""
Integration tests for the mitmproxy Jumpstarter driver.

These tests start a real mitmdump subprocess, configure mock endpoints,
and make actual HTTP requests through the proxy to verify the full
roundtrip: client -> gRPC (local mode) -> driver -> mitmdump -> HTTP.

Requires mitmdump to be installed and on PATH.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest
import requests

from .driver import MitmproxyDriver
from jumpstarter.common.utils import serve


def _free_port() -> int:
    """Bind to port 0 and return the OS-assigned port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(host: str, port: int, timeout: float = 10) -> bool:
    """TCP retry loop to confirm a port is accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def _can_reach_internet() -> bool:
    """Quick TCP probe to check internet connectivity."""
    try:
        with socket.create_connection(("httpbin.org", 80), timeout=3):
            return True
    except OSError:
        return False


def _is_mitmdump_available() -> bool:
    import shutil
    return shutil.which("mitmdump") is not None


# Skip the entire module if mitmdump isn't available
pytestmark = pytest.mark.skipif(
    not _is_mitmdump_available(),
    reason="mitmdump not found on PATH",
)


@pytest.fixture
def proxy_port():
    return _free_port()


@pytest.fixture
def web_port():
    return _free_port()


@pytest.fixture
def client(tmp_path, proxy_port, web_port):
    """Create a MitmproxyDriver wrapped in Jumpstarter's local serve harness."""
    instance = MitmproxyDriver(
        listen_host="127.0.0.1",
        listen_port=proxy_port,
        web_host="127.0.0.1",
        web_port=web_port,
        confdir=str(tmp_path / "confdir"),
        flow_dir=str(tmp_path / "flows"),
        addon_dir=str(tmp_path / "addons"),
        mock_dir=str(tmp_path / "mocks"),
        ssl_insecure=True,
    )
    with serve(instance) as client:
        yield client


def _start_mock_with_endpoints(client, proxy_port, mocks):
    """Set mocks before starting the proxy so the addon loads them on init.

    This avoids any hot-reload timing considerations: endpoints are
    on disk when mitmdump first reads the config.
    """
    for method, path, kwargs in mocks:
        client.set_mock(method, path, **kwargs)
    client.start("mock")
    assert _wait_for_port("127.0.0.1", proxy_port), (
        f"mitmdump did not start on port {proxy_port}"
    )


class TestProxyLifecycle:
    """Start/stop with a real mitmdump process."""

    def test_start_mock_mode_and_status(self, client, proxy_port):
        result = client.start("mock")
        assert "mock" in result
        assert str(proxy_port) in result

        status = client.status()
        assert status["running"] is True
        assert status["mode"] == "mock"
        assert status["pid"] is not None

        client.stop()

    def test_stop_proxy(self, client, proxy_port):
        client.start("mock")
        assert client.is_running() is True

        result = client.stop()
        assert "Stopped" in result

        status = client.status()
        assert status["running"] is False
        assert status["mode"] == "stopped"

    def test_start_passthrough_mode(self, client):
        result = client.start("passthrough")
        assert "passthrough" in result

        status = client.status()
        assert status["running"] is True
        assert status["mode"] == "passthrough"

        client.stop()


class TestMockEndpoints:
    """Mock configuration + real HTTP requests through the proxy."""

    def test_simple_mock_response(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/status", {
                "body": {"id": "test-001", "online": True},
            }),
        ])

        try:
            response = requests.get(
                "http://example.com/api/v1/status",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["id"] == "test-001"
            assert data["online"] is True
        finally:
            client.stop()

    def test_multiple_mock_endpoints(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/health", {"body": {"ok": True}}),
            ("POST", "/api/v1/telemetry", {
                "status": 202, "body": {"accepted": True},
            }),
        ])

        try:
            proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

            resp_get = requests.get(
                "http://example.com/api/v1/health",
                proxies=proxies, timeout=10,
            )
            assert resp_get.status_code == 200
            assert resp_get.json()["ok"] is True

            resp_post = requests.post(
                "http://example.com/api/v1/telemetry",
                proxies=proxies, timeout=10,
            )
            assert resp_post.status_code == 202
            assert resp_post.json()["accepted"] is True
        finally:
            client.stop()

    def test_mock_error_status_codes(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/missing", {
                "status": 404, "body": {"error": "not found"},
            }),
            ("GET", "/api/v1/broken", {
                "status": 500, "body": {"error": "internal error"},
            }),
        ])

        try:
            proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

            resp_404 = requests.get(
                "http://example.com/api/v1/missing",
                proxies=proxies, timeout=10,
            )
            assert resp_404.status_code == 404
            assert resp_404.json()["error"] == "not found"

            resp_500 = requests.get(
                "http://example.com/api/v1/broken",
                proxies=proxies, timeout=10,
            )
            assert resp_500.status_code == 500
            assert resp_500.json()["error"] == "internal error"
        finally:
            client.stop()

    def test_clear_mocks(self, client, proxy_port):
        client.set_mock("GET", "/a", body={"x": 1})
        client.set_mock("GET", "/b", body={"x": 2})
        client.start("mock")

        try:
            result = client.clear_mocks()
            assert "Cleared 2" in result

            mocks = client.list_mocks()
            assert len(mocks) == 0
        finally:
            client.stop()

    def test_remove_single_mock(self, client, proxy_port):
        client.set_mock("GET", "/keep", body={"x": 1})
        client.set_mock("GET", "/remove", body={"x": 2})
        client.start("mock")

        try:
            client.remove_mock("GET", "/remove")

            mocks = client.list_mocks()
            assert "GET /keep" in mocks
            assert "GET /remove" not in mocks
        finally:
            client.stop()

    def test_context_manager_mock_endpoint(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/base", {"body": {"base": True}}),
        ])

        try:
            proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

            # Verify base mock works
            resp_base = requests.get(
                "http://example.com/api/v1/base",
                proxies=proxies, timeout=10,
            )
            assert resp_base.status_code == 200
            assert resp_base.json()["base"] is True

            # Use mock_endpoint context manager to add a temporary mock
            with client.mock_endpoint(
                "GET", "/api/v1/temp",
                body={"temporary": True},
            ):
                # Allow addon to detect config change
                time.sleep(1)
                response = requests.get(
                    "http://example.com/api/v1/temp",
                    proxies=proxies, timeout=10,
                )
                assert response.status_code == 200
                assert response.json()["temporary"] is True

            # After exiting the context manager, mock should be removed
            mocks = client.list_mocks()
            assert "GET /api/v1/temp" not in mocks
        finally:
            client.stop()

    def test_hot_reload_mocks(self, client, proxy_port):
        """Verify that mocks added after start are picked up via hot-reload."""
        client.start("mock")
        assert _wait_for_port("127.0.0.1", proxy_port)

        try:
            # Set mock after proxy is already running
            client.set_mock(
                "GET", "/api/v1/hotreload",
                body={"reloaded": True},
            )
            # Give the addon time to detect the file change on next request
            time.sleep(1)

            response = requests.get(
                "http://example.com/api/v1/hotreload",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            assert response.status_code == 200
            assert response.json()["reloaded"] is True
        finally:
            client.stop()


@pytest.mark.skipif(
    not _can_reach_internet(),
    reason="No internet connectivity (httpbin.org unreachable)",
)
class TestPassthrough:
    """Real HTTP through proxy to the internet."""

    def test_passthrough_to_public_api(self, client, proxy_port):
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port), (
            f"mitmdump did not start on port {proxy_port}"
        )

        try:
            response = requests.get(
                "http://httpbin.org/get",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=15,
            )
            assert response.status_code == 200
            data = response.json()
            assert "headers" in data
        finally:
            client.stop()


class TestRequestCapture:
    """End-to-end tests for request capture via the proxy."""

    def test_captured_requests_appear(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/status", {
                "body": {"id": "test-001", "online": True},
            }),
        ])

        try:
            client.clear_captured_requests()

            requests.get(
                "http://example.com/api/v1/status",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            # Wait for the capture event to arrive
            result = client.wait_for_request("GET", "/api/v1/status", 5.0)
            assert result["method"] == "GET"
            assert result["path"] == "/api/v1/status"
            assert result["response_status"] == 200
            assert result["was_mocked"] is True
        finally:
            client.stop()

    def test_clear_captured_requests(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/health", {"body": {"ok": True}}),
        ])

        try:
            requests.get(
                "http://example.com/api/v1/health",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            # Wait for capture
            client.wait_for_request("GET", "/api/v1/health", 5.0)

            result = client.clear_captured_requests()
            assert "Cleared" in result

            captured = client.get_captured_requests()
            assert len(captured) == 0
        finally:
            client.stop()

    def test_wait_for_request(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/delayed", {"body": {"ok": True}}),
        ])

        try:
            client.clear_captured_requests()

            # Send request after a short delay in background
            def delayed_request():
                time.sleep(1)
                requests.get(
                    "http://example.com/api/v1/delayed",
                    proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                    timeout=10,
                )

            t = threading.Thread(target=delayed_request)
            t.start()

            result = client.wait_for_request("GET", "/api/v1/delayed", 10.0)
            assert result["method"] == "GET"
            assert result["path"] == "/api/v1/delayed"

            t.join(timeout=5)
        finally:
            client.stop()

    def test_wait_for_request_timeout(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/status", {"body": {"ok": True}}),
        ])

        try:
            client.clear_captured_requests()
            with pytest.raises(TimeoutError):
                client.wait_for_request("GET", "/api/nonexistent", 1.0)
        finally:
            client.stop()

    def test_capture_context_manager(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/status", {
                "body": {"id": "test-001"},
            }),
        ])

        try:
            with client.capture() as cap:
                requests.get(
                    "http://example.com/api/v1/status",
                    proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                    timeout=10,
                )
                cap.wait_for_request("GET", "/api/v1/status", 5.0)

            # After exit, snapshot is frozen
            assert len(cap.requests) >= 1
            assert cap.requests[0]["method"] == "GET"
        finally:
            client.stop()

    def test_assert_request_made(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/health", {"body": {"ok": True}}),
        ])

        try:
            client.clear_captured_requests()

            requests.get(
                "http://example.com/api/v1/health",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            # Wait for capture to arrive
            client.wait_for_request("GET", "/api/v1/health", 5.0)

            # Should pass
            result = client.assert_request_made("GET", "/api/v1/health")
            assert result["method"] == "GET"

            # Should fail
            with pytest.raises(AssertionError, match="not captured"):
                client.assert_request_made("POST", "/api/v1/missing")
        finally:
            client.stop()

    def test_multiple_requests_captured_in_order(self, client, proxy_port):
        _start_mock_with_endpoints(client, proxy_port, [
            ("GET", "/api/v1/first", {"body": {"n": 1}}),
            ("GET", "/api/v1/second", {"body": {"n": 2}}),
            ("GET", "/api/v1/third", {"body": {"n": 3}}),
        ])

        try:
            client.clear_captured_requests()
            proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

            requests.get(
                "http://example.com/api/v1/first",
                proxies=proxies, timeout=10,
            )
            requests.get(
                "http://example.com/api/v1/second",
                proxies=proxies, timeout=10,
            )
            requests.get(
                "http://example.com/api/v1/third",
                proxies=proxies, timeout=10,
            )

            # Wait for the last request to be captured
            client.wait_for_request("GET", "/api/v1/third", 5.0)

            captured = client.get_captured_requests()
            assert len(captured) >= 3

            paths = [r["path"] for r in captured]
            assert "/api/v1/first" in paths
            assert "/api/v1/second" in paths
            assert "/api/v1/third" in paths

            # Verify ordering: first should appear before second,
            # second before third
            idx_first = paths.index("/api/v1/first")
            idx_second = paths.index("/api/v1/second")
            idx_third = paths.index("/api/v1/third")
            assert idx_first < idx_second < idx_third
        finally:
            client.stop()
