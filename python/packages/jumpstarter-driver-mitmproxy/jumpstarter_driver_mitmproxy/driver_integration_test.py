"""
Integration tests for the mitmproxy Jumpstarter driver.

These tests start a real mitmdump subprocess, configure mock endpoints,
and make actual HTTP requests through the proxy to verify the full
roundtrip: client -> gRPC (local mode) -> driver -> mitmdump -> HTTP.

Requires mitmdump to be installed and on PATH.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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


class _LocalHttpHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that returns a JSON response for GET requests."""

    def do_GET(self):
        body = json.dumps({
            "headers": dict(self.headers),
            "url": self.path,
        })
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, format, *args):
        # Silence request logs during tests
        pass


def _is_mitmdump_available() -> bool:
    import shutil
    return shutil.which("mitmdump") is not None


# Skip the entire module if mitmdump isn't available
pytestmark = pytest.mark.skipif(
    not _is_mitmdump_available(),
    reason="mitmdump not found on PATH",
)


@pytest.fixture(scope="class")
def proxy_port():
    return _free_port()


@pytest.fixture(scope="class")
def web_port():
    return _free_port()


@pytest.fixture(scope="class")
def client(tmp_path_factory, proxy_port, web_port):
    """Create a MitmproxyDriver wrapped in Jumpstarter's local serve harness."""
    tmp_path = tmp_path_factory.mktemp("mitmproxy")
    instance = MitmproxyDriver(
        listen={"host": "127.0.0.1", "port": proxy_port},
        web={"host": "127.0.0.1", "port": web_port},
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

    def test_start_passthrough_mode(self, client, proxy_port):
        result = client.start("passthrough")
        assert "passthrough" in result
        assert _wait_for_port("127.0.0.1", proxy_port), (
            f"mitmdump did not start on port {proxy_port}"
        )

        status = client.status()
        assert status["running"] is True
        assert status["mode"] == "passthrough"

        client.stop()


class TestMockEndpoints:
    """Mock configuration + real HTTP requests through the proxy."""

    @pytest.fixture(autouse=True)
    def _proxy_lifecycle(self, client, proxy_port):
        """Start the proxy once for the class, clear mocks between tests."""
        client.clear_mocks()
        if not client.is_running():
            client.start("mock")
            assert _wait_for_port("127.0.0.1", proxy_port)
        yield

    def test_simple_mock_response(self, client, proxy_port):
        client.set_mock("GET", "/api/v1/status", body={"id": "test-001", "online": True})
        time.sleep(0.3)

        response = requests.get(
            "http://example.com/api/v1/status",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=10,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "test-001"
        assert data["online"] is True

    def test_multiple_mock_endpoints(self, client, proxy_port):
        client.set_mock("GET", "/api/v1/health", body={"ok": True})
        client.set_mock("POST", "/api/v1/telemetry", status=202, body={"accepted": True})
        time.sleep(0.3)

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

    def test_mock_error_status_codes(self, client, proxy_port):
        client.set_mock("GET", "/api/v1/missing", status=404, body={"error": "not found"})
        client.set_mock("GET", "/api/v1/broken", status=500, body={"error": "internal error"})
        time.sleep(0.3)

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

    def test_clear_mocks(self, client, proxy_port):
        client.set_mock("GET", "/a", body={"x": 1})
        client.set_mock("GET", "/b", body={"x": 2})

        result = client.clear_mocks()
        assert "Cleared 2" in result

        mocks = client.list_mocks()
        assert len(mocks) == 0

    def test_remove_single_mock(self, client, proxy_port):
        client.set_mock("GET", "/keep", body={"x": 1})
        client.set_mock("GET", "/remove", body={"x": 2})

        client.remove_mock("GET", "/remove")

        mocks = client.list_mocks()
        assert "GET /keep" in mocks
        assert "GET /remove" not in mocks

    def test_context_manager_mock_endpoint(self, client, proxy_port):
        client.set_mock("GET", "/api/v1/base", body={"base": True})
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp_base = requests.get(
            "http://example.com/api/v1/base",
            proxies=proxies, timeout=10,
        )
        assert resp_base.status_code == 200
        assert resp_base.json()["base"] is True

        with client.mock_endpoint(
            "GET", "/api/v1/temp",
            body={"temporary": True},
        ):
            time.sleep(1)
            response = requests.get(
                "http://example.com/api/v1/temp",
                proxies=proxies, timeout=10,
            )
            assert response.status_code == 200
            assert response.json()["temporary"] is True

        mocks = client.list_mocks()
        assert "GET /api/v1/temp" not in mocks

    def test_hot_reload_mocks(self, client, proxy_port):
        """Verify that mocks added after start are picked up via hot-reload."""
        client.set_mock(
            "GET", "/api/v1/hotreload",
            body={"reloaded": True},
        )
        time.sleep(0.3)

        response = requests.get(
            "http://example.com/api/v1/hotreload",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=10,
        )
        assert response.status_code == 200
        assert response.json()["reloaded"] is True


class TestPassthrough:
    """HTTP through proxy to an upstream server."""

    @pytest.fixture
    def upstream(self):
        """Start a local HTTP server to act as the upstream."""
        server = HTTPServer(("127.0.0.1", 0), _LocalHttpHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield port
        server.shutdown()

    def test_passthrough_to_local_server(self, client, proxy_port, upstream):
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port), (
            f"mitmdump did not start on port {proxy_port}"
        )

        try:
            response = requests.get(
                f"http://127.0.0.1:{upstream}/get",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=15,
            )
            assert response.status_code == 200
            data = response.json()
            assert "headers" in data
        finally:
            client.stop()


class _BinaryHandler(BaseHTTPRequestHandler):
    """Serves a body large and binary enough to be spooled, not inlined."""

    body = bytes(range(256)) * 2048  # 512 KiB

    def do_GET(self):
        """Serve the same binary body for any path."""
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, format, *args):
        """Keep the test output quiet."""


class TestCaptureSpool:
    """Large and binary response bodies are spooled to the driver's data dir."""

    @pytest.fixture
    def upstream(self):
        """A local server that serves a large binary body."""
        server = HTTPServer(("127.0.0.1", 0), _BinaryHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_address[1]
        server.shutdown()

    @pytest.fixture(autouse=True)
    def _running(self, client, proxy_port):
        """Run the proxy in passthrough mode for each test."""
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port)
        client.clear_captured_requests()
        yield
        client.stop()

    @staticmethod
    def _fetch(proxy_port, upstream, path):
        """GET ``path`` from the upstream through the proxy and check the body arrived intact."""
        response = requests.get(
            f"http://127.0.0.1:{upstream}{path}",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=15,
        )
        assert response.content == _BinaryHandler.body
        return response

    def test_body_is_spooled_and_exported(self, client, proxy_port, upstream):
        """The body is spooled under the data dir and survives scenario export and download."""
        self._fetch(proxy_port, upstream, "/bin/first")
        req = client.wait_for_request("GET", "/bin/first")

        spooled = Path(req["response_body_file"])
        assert spooled.parent.name == "capture-spool"
        assert spooled.read_bytes() == _BinaryHandler.body

        _, files = client.export_captured_scenario(filter_pattern="/bin/*")
        assert len(files) == 1
        assert client.get_captured_file(files[0]) == _BinaryHandler.body

    def test_spool_dir_is_recreated_after_removal(self, client, proxy_port, upstream):
        """Removing the spool directory mid-session does not lose later bodies."""
        self._fetch(proxy_port, upstream, "/bin/first")
        spool_dir = Path(client.wait_for_request("GET", "/bin/first")["response_body_file"]).parent
        shutil.rmtree(spool_dir)

        self._fetch(proxy_port, upstream, "/bin/second")
        req = client.wait_for_request("GET", "/bin/second")

        assert req["response_body_file"], "the body was dropped instead of spooled"
        assert Path(req["response_body_file"]).read_bytes() == _BinaryHandler.body


class TestCaptureQueries:
    """get_response_body and export_captured_requests, through the client."""

    @pytest.fixture
    def json_upstream(self):
        server = HTTPServer(("127.0.0.1", 0), _LocalHttpHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield server.server_address[1]
        server.shutdown()

    @pytest.fixture
    def binary_upstream(self):
        server = HTTPServer(("127.0.0.1", 0), _BinaryHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield server.server_address[1]
        server.shutdown()

    @pytest.fixture(autouse=True)
    def _running(self, client, proxy_port):
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port)
        client.clear_captured_requests()
        yield
        client.stop()

    @staticmethod
    def _fetch(proxy_port, upstream, path):
        return requests.get(
            f"http://127.0.0.1:{upstream}{path}",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=15,
        )

    def test_get_response_body_returns_the_parsed_body(
        self, client, proxy_port, json_upstream,
    ):
        self._fetch(proxy_port, json_upstream, "/items/1")
        self._fetch(proxy_port, json_upstream, "/items/2")
        client.wait_for_request("GET", "/items/2")

        latest = client.get_response_body(r"^/items/\d+$")
        oldest = client.get_response_body(r"^/items/\d+$", index=0)

        assert latest["body"]["url"] == "/items/2"
        assert oldest["body"]["url"] == "/items/1"
        assert latest["status"] == 200
        assert latest["truncated"] is False

    def test_get_response_body_raises_when_nothing_matches(self, client):
        with pytest.raises(LookupError, match="No captured response"):
            client.get_response_body(r"^/nothing-here$")

    def test_export_inlines_text_bodies_and_skips_binary(
        self, client, proxy_port, json_upstream, binary_upstream,
    ):
        self._fetch(proxy_port, json_upstream, "/items/1")
        self._fetch(proxy_port, binary_upstream, "/bin/1")
        client.wait_for_request("GET", "/bin/1")

        by_path = {e["path"]: e for e in client.export_captured_requests()}

        assert json.loads(by_path["/items/1"]["response_body"])["url"] == "/items/1"
        assert by_path["/bin/1"]["response_body"] is None
        assert by_path["/bin/1"]["response_body_skipped"] == "binary content type"

    def test_export_rejects_a_non_positive_body_cap(self, client):
        with pytest.raises(ValueError, match="greater than zero"):
            client.export_captured_requests(max_body_size=0)


class TestTrafficShaping:
    """Shaping applied through the client to a real mitmdump."""

    @pytest.fixture
    def upstream(self):
        server = HTTPServer(("127.0.0.1", 0), _LocalHttpHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield server.server_address[1]
        server.shutdown()

    @pytest.fixture
    def running(self, client, proxy_port):
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port), (
            f"mitmdump did not start on port {proxy_port}"
        )
        yield
        client.stop()

    @staticmethod
    def _get(proxy_port, upstream):
        return requests.get(
            f"http://127.0.0.1:{upstream}/get",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=15,
        )

    def test_shape_refused_without_a_running_proxy(self, client):
        with pytest.raises(RuntimeError, match="running proxy"):
            client.shape(latency_ms=100)
        assert client.get_shaping() == {}

    def test_out_of_range_value_raises_value_error(self, client, running):
        with pytest.raises(ValueError, match="drop_pct"):
            client.shape(drop_pct=150)
        assert client.get_shaping() == {}

    def test_latency_is_added_and_cleared(self, client, running, proxy_port, upstream):
        with client.shaping(latency_ms=600) as applied:
            assert applied["latency_ms"] == 600
            assert client.get_shaping()["latency_ms"] == 600
            started = time.monotonic()
            assert self._get(proxy_port, upstream).status_code == 200
            assert time.monotonic() - started >= 0.55

        assert client.get_shaping() == {}
        assert client.clear_shaping() is False

    def test_full_drop_fails_the_request(self, client, running, proxy_port, upstream):
        with client.shaping(drop_pct=100), pytest.raises(requests.RequestException):
            self._get(proxy_port, upstream)
        assert self._get(proxy_port, upstream).status_code == 200

    def test_stop_discards_shaping(self, client, proxy_port):
        client.start("passthrough")
        assert _wait_for_port("127.0.0.1", proxy_port)
        client.shape(rate_kbit=400)
        client.stop()
        assert client.get_shaping() == {}


class TestRequestCapture:
    """End-to-end tests for request capture via the proxy."""

    @pytest.fixture(autouse=True)
    def _proxy_lifecycle(self, client, proxy_port):
        """Start the proxy once for the class, clear state between tests."""
        client.clear_mocks()
        client.set_mock("GET", "/api/v1/status", body={"id": "test-001", "online": True})
        client.set_mock("GET", "/api/v1/health", body={"ok": True})
        client.set_mock("GET", "/api/v1/delayed", body={"ok": True})
        client.set_mock("GET", "/api/v1/first", body={"n": 1})
        client.set_mock("GET", "/api/v1/second", body={"n": 2})
        client.set_mock("GET", "/api/v1/third", body={"n": 3})
        if not client.is_running():
            client.start("mock")
            assert _wait_for_port("127.0.0.1", proxy_port)
        time.sleep(0.3)
        client.clear_captured_requests()
        yield

    def test_captured_requests_appear(self, client, proxy_port):
        requests.get(
            "http://example.com/api/v1/status",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=10,
        )
        result = client.wait_for_request("GET", "/api/v1/status", 5.0)
        assert result["method"] == "GET"
        assert result["path"] == "/api/v1/status"
        assert result["response_status"] == 200
        assert result["was_mocked"] is True

    def test_clear_captured_requests(self, client, proxy_port):
        requests.get(
            "http://example.com/api/v1/health",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=10,
        )
        client.wait_for_request("GET", "/api/v1/health", 5.0)

        result = client.clear_captured_requests()
        assert "Cleared" in result

        captured = client.get_captured_requests()
        assert len(captured) == 0

    def test_wait_for_request(self, client, proxy_port):
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

    def test_wait_for_request_timeout(self, client, proxy_port):
        with pytest.raises(TimeoutError):
            client.wait_for_request("GET", "/api/nonexistent", 1.0)

    def test_capture_context_manager(self, client, proxy_port):
        with client.capture() as cap:
            requests.get(
                "http://example.com/api/v1/status",
                proxies={"http": f"http://127.0.0.1:{proxy_port}"},
                timeout=10,
            )
            cap.wait_for_request("GET", "/api/v1/status", 5.0)

        assert len(cap.requests) >= 1
        assert cap.requests[0]["method"] == "GET"

    def test_assert_request_made(self, client, proxy_port):
        requests.get(
            "http://example.com/api/v1/health",
            proxies={"http": f"http://127.0.0.1:{proxy_port}"},
            timeout=10,
        )
        client.wait_for_request("GET", "/api/v1/health", 5.0)

        result = client.assert_request_made("GET", "/api/v1/health")
        assert result["method"] == "GET"

        with pytest.raises(AssertionError, match="not captured"):
            client.assert_request_made("POST", "/api/v1/missing")

    def test_multiple_requests_captured_in_order(self, client, proxy_port):
        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        requests.get("http://example.com/api/v1/first", proxies=proxies, timeout=10)
        requests.get("http://example.com/api/v1/second", proxies=proxies, timeout=10)
        requests.get("http://example.com/api/v1/third", proxies=proxies, timeout=10)

        client.wait_for_request("GET", "/api/v1/third", 5.0)

        captured = client.get_captured_requests()
        assert len(captured) >= 3

        paths = [r["path"] for r in captured]
        assert "/api/v1/first" in paths
        assert "/api/v1/second" in paths
        assert "/api/v1/third" in paths

        idx_first = paths.index("/api/v1/first")
        idx_second = paths.index("/api/v1/second")
        idx_third = paths.index("/api/v1/third")
        assert idx_first < idx_second < idx_third


class TestConditionalMocks:
    """Conditional mock rules with real HTTP requests through the proxy."""

    @pytest.fixture(autouse=True)
    def _proxy_lifecycle(self, client, proxy_port):
        """Start the proxy once for the class, clear mocks between tests."""
        client.clear_mocks()
        if not client.is_running():
            client.start("mock")
            assert _wait_for_port("127.0.0.1", proxy_port)
        yield

    def test_conditional_body_json_match(self, client, proxy_port):
        """POST with matching JSON body -> 200, non-matching -> 401."""
        client.set_mock_conditional("POST", "/api/auth", [
            {
                "match": {"body_json": {"username": "admin",
                                        "password": "secret"}},
                "status": 200,
                "body": {"token": "mock-token-001"},
            },
            {"status": 401, "body": {"error": "unauthorized"}},
        ])
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp_ok = requests.post(
            "http://example.com/api/auth",
            json={"username": "admin", "password": "secret"},
            proxies=proxies, timeout=10,
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["token"] == "mock-token-001"

        resp_fail = requests.post(
            "http://example.com/api/auth",
            json={"username": "hacker", "password": "wrong"},
            proxies=proxies, timeout=10,
        )
        assert resp_fail.status_code == 401
        assert resp_fail.json()["error"] == "unauthorized"

    def test_conditional_header_match(self, client, proxy_port):
        """GET with matching header -> 200, without -> 401."""
        client.set_mock_conditional("GET", "/api/data", [
            {
                "match": {"headers": {"Authorization": "Bearer tok123"}},
                "status": 200,
                "body": {"items": [1, 2, 3]},
            },
            {"status": 401, "body": {"error": "unauthorized"}},
        ])
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp_ok = requests.get(
            "http://example.com/api/data",
            headers={"Authorization": "Bearer tok123"},
            proxies=proxies, timeout=10,
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["items"] == [1, 2, 3]

        resp_fail = requests.get(
            "http://example.com/api/data",
            proxies=proxies, timeout=10,
        )
        assert resp_fail.status_code == 401

    def test_conditional_query_match(self, client, proxy_port):
        """GET with matching query param -> 200, without -> default."""
        client.set_mock_conditional("GET", "/api/search", [
            {
                "match": {"query": {"q": "hello"}},
                "status": 200,
                "body": {"results": ["hello world"]},
            },
            {"status": 200, "body": {"results": []}},
        ])
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp_match = requests.get(
            "http://example.com/api/search?q=hello",
            proxies=proxies, timeout=10,
        )
        assert resp_match.status_code == 200
        assert resp_match.json()["results"] == ["hello world"]

        resp_default = requests.get(
            "http://example.com/api/search",
            proxies=proxies, timeout=10,
        )
        assert resp_default.status_code == 200
        assert resp_default.json()["results"] == []

    def test_conditional_with_template(self, client, proxy_port):
        """Rule containing body_template with dynamic expressions."""
        client.set_mock_conditional("GET", "/api/echo", [
            {
                "match": {"headers": {"X-Mode": "dynamic"}},
                "status": 200,
                "body_template": {
                    "path": "{{request_path}}",
                    "mode": "dynamic",
                },
            },
            {"status": 200, "body": {"mode": "static"}},
        ])
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp = requests.get(
            "http://example.com/api/echo",
            headers={"X-Mode": "dynamic"},
            proxies=proxies, timeout=10,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "dynamic"
        assert "/api/echo" in data["path"]

        resp_static = requests.get(
            "http://example.com/api/echo",
            proxies=proxies, timeout=10,
        )
        assert resp_static.json()["mode"] == "static"


class TestEnhancedTemplates:
    """Tests for enhanced template expressions with real HTTP requests."""

    @pytest.fixture(autouse=True)
    def _proxy_lifecycle(self, client, proxy_port):
        """Start the proxy once for the class, clear mocks between tests."""
        client.clear_mocks()
        if not client.is_running():
            client.start("mock")
            assert _wait_for_port("127.0.0.1", proxy_port)
        yield

    def test_request_body_json_in_template(self, client, proxy_port):
        """Echo a JSON field from request body via template."""
        client.set_mock_template(
            "POST", "/api/echo",
            template={"echoed_name": "{{request_body_json(name)}}"},
        )
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp = requests.post(
            "http://example.com/api/echo",
            json={"name": "Alice", "age": 30},
            proxies=proxies, timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["echoed_name"] == "Alice"

    def test_request_query_in_template(self, client, proxy_port):
        """Echo a query param in the response via template."""
        client.set_mock_template(
            "GET", "/api/greet",
            template={"greeting": "Hello, {{request_query(name)}}!"},
        )
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp = requests.get(
            "http://example.com/api/greet?name=Bob",
            proxies=proxies, timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["greeting"] == "Hello, Bob!"

    def test_state_in_template(self, client, proxy_port):
        """Set state then read it via {{state(key)}} in template."""
        client.set_state("current_user", "Alice")
        client.set_mock_template(
            "GET", "/api/whoami",
            template={"user": "{{state(current_user)}}"},
        )
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        resp = requests.get(
            "http://example.com/api/whoami",
            proxies=proxies, timeout=10,
        )
        assert resp.status_code == 200
        assert resp.json()["user"] == "Alice"


class TestAuthScenario:
    """Full auth token flow using conditional rules."""

    @pytest.fixture(autouse=True)
    def _proxy_lifecycle(self, client, proxy_port):
        """Start the proxy once for the class, clear mocks between tests."""
        client.clear_mocks()
        if not client.is_running():
            client.start("mock")
            assert _wait_for_port("127.0.0.1", proxy_port)
        yield

    def test_auth_token_flow(self, client, proxy_port):
        """Login with credentials -> get token -> use token for data."""
        client.set_mock_conditional("POST", "/api/auth", [
            {
                "match": {"body_json": {"username": "admin",
                                        "password": "secret"}},
                "status": 200,
                "body": {"token": "mock-token-001"},
            },
            {"status": 401, "body": {"error": "unauthorized"}},
        ])
        client.set_mock_conditional("GET", "/api/data", [
            {
                "match": {"headers": {
                    "Authorization": "Bearer mock-token-001",
                }},
                "status": 200,
                "body": {"items": [1, 2, 3]},
            },
            {"status": 401, "body": {"error": "unauthorized"}},
        ])
        time.sleep(0.3)

        proxies = {"http": f"http://127.0.0.1:{proxy_port}"}

        login_resp = requests.post(
            "http://example.com/api/auth",
            json={"username": "admin", "password": "secret"},
            proxies=proxies, timeout=10,
        )
        assert login_resp.status_code == 200
        token = login_resp.json()["token"]
        assert token == "mock-token-001"

        data_resp = requests.get(
            "http://example.com/api/data",
            headers={"Authorization": f"Bearer {token}"},
            proxies=proxies, timeout=10,
        )
        assert data_resp.status_code == 200
        assert data_resp.json()["items"] == [1, 2, 3]

        unauth_resp = requests.get(
            "http://example.com/api/data",
            proxies=proxies, timeout=10,
        )
        assert unauth_resp.status_code == 401

        bad_login = requests.post(
            "http://example.com/api/auth",
            json={"username": "hacker", "password": "nope"},
            proxies=proxies, timeout=10,
        )
        assert bad_login.status_code == 401
