"""
Jumpstarter client for the mitmproxy driver.

This module runs on the test client side and communicates with the
MitmproxyDriver running on the exporter host via Jumpstarter's gRPC
transport. It provides a Pythonic API for controlling the proxy,
configuring mock endpoints, and managing traffic recordings.

Usage in pytest::

    def test_device_status(client):
        proxy = client.proxy  # MitmproxyClient instance

        proxy.start(mode="mock", web_ui=True)

        proxy.set_mock(
            "GET", "/api/v1/status",
            body={"id": "device-001", "status": "online"},
        )

        # ... interact with DUT ...

        proxy.stop()

Or with context managers for cleaner test code::

    def test_update_check(client):
        proxy = client.proxy

        with proxy.session(mode="mock", web_ui=True):
            with proxy.mock_endpoint("GET", "/api/v1/updates/check",
                                     body={"update_available": True}):
                # ... test update notification flow ...
                pass
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator

from jumpstarter.client import DriverClient


class CaptureContext:
    """Context for a capture session.

    Returned by :meth:`MitmproxyClient.capture`. While inside the
    ``with`` block, ``requests`` returns live data from the driver.
    After exiting, it returns a frozen snapshot taken at exit time.

    Example::

        with proxy.capture() as cap:
            # ... interact with DUT ...
            pass
        assert cap.requests  # frozen snapshot
    """

    def __init__(self, client: "MitmproxyClient"):
        self._client = client
        self._snapshot: list[dict] | None = None

    @property
    def requests(self) -> list[dict]:
        """Captured requests (live while in context, frozen after exit)."""
        if self._snapshot is not None:
            return self._snapshot
        return self._client.get_captured_requests()

    def assert_request_made(self, method: str, path: str) -> dict:
        """Assert that a matching request was captured.

        Raises:
            AssertionError: If no matching request is found.
        """
        return self._client.assert_request_made(method, path)

    def wait_for_request(
        self, method: str, path: str, timeout: float = 10.0,
    ) -> dict:
        """Wait for a matching request.

        Raises:
            TimeoutError: If no match within timeout.
        """
        return self._client.wait_for_request(method, path, timeout)

    def _freeze(self):
        """Take a snapshot (called on context exit)."""
        self._snapshot = self._client.get_captured_requests()


class MitmproxyClient(DriverClient):
    """Client for controlling mitmproxy on the exporter host.

    All methods delegate to the corresponding ``@export``-decorated
    methods on ``MitmproxyDriver`` via Jumpstarter's RPC mechanism.
    """

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self, mode: str = "mock", web_ui: bool = False,
              replay_file: str = "") -> str:
        """Start the proxy in the specified mode.

        Args:
            mode: One of "mock", "passthrough", "record", "replay".
            web_ui: Launch mitmweb (browser UI) instead of mitmdump.
            replay_file: Flow file path for replay mode.

        Returns:
            Status message with connection details.
        """
        return self.call("start", mode, web_ui, replay_file)

    def stop(self) -> str:
        """Stop the proxy process.

        Returns:
            Status message.
        """
        return self.call("stop")

    def restart(self, mode: str = "", web_ui: bool = False,
                replay_file: str = "") -> str:
        """Restart the proxy (optionally with new config).

        Args:
            mode: New mode (empty string keeps current mode).
            web_ui: Enable/disable web UI.
            replay_file: Flow file for replay mode.

        Returns:
            Status message from start().
        """
        return self.call("restart", mode, web_ui, replay_file)

    # ── Status ──────────────────────────────────────────────────

    def status(self) -> dict:
        """Get proxy status as a dict.

        Returns:
            Dict with keys: running, mode, pid, proxy_address,
            web_ui_enabled, web_ui_address, mock_count, flow_file.
        """
        return json.loads(self.call("status"))

    def is_running(self) -> bool:
        """Check if the proxy process is alive."""
        return self.call("is_running")

    @property
    def web_ui_url(self) -> str | None:
        """Get the mitmweb UI URL if available."""
        info = self.status()
        return info.get("web_ui_address")

    # ── Mock management ─────────────────────────────────────────

    def set_mock(self, method: str, path: str, status: int = 200,
                 body: dict | list | str = "",
                 content_type: str = "application/json",
                 headers: dict | None = None) -> str:
        """Add or update a mock endpoint.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path to match. Append "*" for prefix matching.
            status: HTTP status code to return.
            body: Response body. Dicts/lists are JSON-serialized.
            content_type: Response Content-Type header.
            headers: Additional response headers.

        Returns:
            Confirmation message.

        Example::

            proxy.set_mock(
                "GET", "/api/v1/status",
                body={"id": "device-001", "status": "online"},
            )
        """
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        elif not body:
            body = "{}"

        headers_str = json.dumps(headers or {})

        return self.call(
            "set_mock", method, path, status, body, content_type,
            headers_str,
        )

    def remove_mock(self, method: str, path: str) -> str:
        """Remove a mock endpoint.

        Args:
            method: HTTP method.
            path: URL path.

        Returns:
            Confirmation or not-found message.
        """
        return self.call("remove_mock", method, path)

    def clear_mocks(self) -> str:
        """Remove all mock endpoint definitions."""
        return self.call("clear_mocks")

    def list_mocks(self) -> dict:
        """List all configured mock endpoints.

        Returns:
            Dict of mock definitions keyed by "METHOD /path".
        """
        return json.loads(self.call("list_mocks"))

    # ── V2: File, latency, sequence, template, addon ────────

    def set_mock_file(self, method: str, path: str,
                      file_path: str,
                      content_type: str = "",
                      status: int = 200,
                      headers: dict | None = None) -> str:
        """Mock an endpoint to serve a file from disk.

        Args:
            method: HTTP method.
            path: URL path.
            file_path: Path relative to files_dir on the exporter.
            content_type: MIME type (auto-detected if empty).
            status: HTTP status code.
            headers: Additional response headers.

        Example::

            proxy.set_mock_file(
                "GET", "/api/v1/downloads/firmware.bin",
                "firmware/test.bin",
            )
        """
        return self.call(
            "set_mock_file", method, path, file_path,
            content_type, status, json.dumps(headers or {}),
        )

    def set_mock_with_latency(self, method: str, path: str,
                              status: int = 200,
                              body: dict | list | str = "",
                              latency_ms: int = 1000,
                              content_type: str = "application/json") -> str:
        """Mock an endpoint with simulated network latency.

        Example::

            proxy.set_mock_with_latency(
                "GET", "/api/v1/status",
                body={"status": "online"},
                latency_ms=3000,  # 3-second delay
            )
        """
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        elif not body:
            body = "{}"
        return self.call(
            "set_mock_with_latency", method, path, status,
            body, latency_ms, content_type,
        )

    def set_mock_sequence(self, method: str, path: str,
                          sequence: list[dict]) -> str:
        """Mock an endpoint with a stateful response sequence.

        Args:
            sequence: List of response steps. Each step has:
                - status (int)
                - body (dict)
                - repeat (int, optional — last entry repeats forever)

        Example::

            proxy.set_mock_sequence("GET", "/api/v1/auth/token", [
                {"status": 200, "body": {"token": "aaa"}, "repeat": 3},
                {"status": 401, "body": {"error": "expired"}, "repeat": 1},
                {"status": 200, "body": {"token": "bbb"}},
            ])
        """
        return self.call(
            "set_mock_sequence", method, path,
            json.dumps(sequence),
        )

    def set_mock_template(self, method: str, path: str,
                          template: dict,
                          status: int = 200) -> str:
        """Mock with a dynamic body template (evaluated per-request).

        Supported expressions: ``{{now_iso}}``, ``{{uuid}}``,
        ``{{random_int(min, max)}}``, ``{{random_choice(a, b)}}``,
        ``{{counter(name)}}``, etc.

        Example::

            proxy.set_mock_template("GET", "/api/v1/weather", {
                "temp_f": "{{random_int(60, 95)}}",
                "condition": "{{random_choice('sunny', 'rain')}}",
                "timestamp": "{{now_iso}}",
            })
        """
        return self.call(
            "set_mock_template", method, path,
            json.dumps(template), status,
        )

    def set_mock_addon(self, method: str, path: str,
                       addon_name: str,
                       addon_config: dict | None = None) -> str:
        """Delegate an endpoint to a custom addon script.

        The addon must be a .py file in the addons directory with
        a ``Handler`` class implementing ``handle(flow, config)``.

        Example::

            proxy.set_mock_addon(
                "GET", "/streaming/audio/channel/*",
                "hls_audio_stream",
                addon_config={
                    "segment_duration_s": 6,
                    "channels": {"ch101": {"name": "Rock"}},
                },
            )
        """
        return self.call(
            "set_mock_addon", method, path, addon_name,
            json.dumps(addon_config or {}),
        )

    def list_addons(self) -> list[str]:
        """List available addon scripts on the exporter."""
        return json.loads(self.call("list_addons"))

    def load_mock_scenario(self, scenario_file: str) -> str:
        """Load a mock scenario from a JSON file on the exporter.

        Replaces all current mocks.

        Args:
            scenario_file: Filename (relative to mock_dir) or
                absolute path.

        Returns:
            Status message with endpoint count.
        """
        return self.call("load_mock_scenario", scenario_file)

    # ── V2: Conditional mocks ──────────────────────────────────

    def set_mock_conditional(self, method: str, path: str,
                             rules: list[dict]) -> str:
        """Mock an endpoint with conditional response rules.

        Rules are evaluated in order. First match wins. A rule with
        no ``match`` key is the default fallback.

        Args:
            method: HTTP method.
            path: URL path.
            rules: List of rule dicts, each with optional ``match``
                conditions and response fields (``status``, ``body``,
                ``body_template``, ``headers``, etc.).

        Returns:
            Confirmation message.

        Example::

            proxy.set_mock_conditional("POST", "/api/auth", [
                {
                    "match": {"body_json": {"username": "admin",
                                            "password": "secret"}},
                    "status": 200,
                    "body": {"token": "mock-token-001"},
                },
                {"status": 401, "body": {"error": "unauthorized"}},
            ])
        """
        return self.call(
            "set_mock_conditional", method, path,
            json.dumps(rules),
        )

    @contextmanager
    def mock_conditional(
        self,
        method: str,
        path: str,
        rules: list[dict],
    ) -> Generator[None, None, None]:
        """Context manager for a temporary conditional mock.

        Sets up conditional rules on entry and removes the mock
        on exit.

        Args:
            method: HTTP method.
            path: URL path.
            rules: Conditional rules list.

        Example::

            with proxy.mock_conditional("POST", "/api/auth", [
                {"match": {"body_json": {"user": "admin"}},
                 "status": 200, "body": {"token": "t1"}},
                {"status": 401, "body": {"error": "denied"}},
            ]):
                # test auth flow
                pass
        """
        self.set_mock_conditional(method, path, rules)
        try:
            yield
        finally:
            self.remove_mock(method, path)

    # ── State store ────────────────────────────────────────────

    def set_state(self, key: str, value: Any) -> str:
        """Set a key in the shared state store.

        Accepts any JSON-serializable value.

        Args:
            key: State key name.
            value: Any JSON-serializable value.

        Returns:
            Confirmation message.
        """
        return self.call("set_state", key, json.dumps(value))

    def get_state(self, key: str) -> Any:
        """Get a value from the shared state store.

        Args:
            key: State key name.

        Returns:
            The deserialized value, or None if not found.
        """
        return json.loads(self.call("get_state", key))

    def clear_state(self) -> str:
        """Clear all keys from the shared state store.

        Returns:
            Confirmation message.
        """
        return self.call("clear_state")

    def get_all_state(self) -> dict:
        """Get the entire shared state store.

        Returns:
            Dict of all state key-value pairs.
        """
        return json.loads(self.call("get_all_state"))

    # ── Flow file management ────────────────────────────────────

    def list_flow_files(self) -> list[dict]:
        """List recorded flow files on the exporter.

        Returns:
            List of dicts with name, path, size_bytes, modified.
        """
        return json.loads(self.call("list_flow_files"))

    # ── CA certificate ──────────────────────────────────────────

    def get_ca_cert_path(self) -> str:
        """Get the path to the mitmproxy CA certificate on the exporter.

        This certificate must be installed on the DUT for HTTPS
        interception.

        Returns:
            Path to the PEM certificate file.
        """
        return self.call("get_ca_cert_path")

    # ── Capture management ──────────────────────────────────────

    def get_captured_requests(self) -> list[dict]:
        """Return all captured requests.

        Returns:
            List of captured request dicts.
        """
        return json.loads(self.call("get_captured_requests"))

    def clear_captured_requests(self) -> str:
        """Clear all captured requests.

        Returns:
            Message with the count of cleared requests.
        """
        return self.call("clear_captured_requests")

    def wait_for_request(self, method: str, path: str,
                         timeout: float = 10.0) -> dict:
        """Wait for a matching request to be captured.

        Args:
            method: HTTP method to match.
            path: URL path to match (supports ``*`` suffix wildcard).
            timeout: Maximum seconds to wait.

        Returns:
            The matching captured request dict.

        Raises:
            TimeoutError: If no match is found within timeout.
        """
        result = json.loads(
            self.call("wait_for_request", method, path, timeout)
        )
        if "error" in result:
            raise TimeoutError(result["error"])
        return result

    def assert_request_made(self, method: str, path: str) -> dict:
        """Assert that a matching request has been captured.

        Args:
            method: HTTP method to match.
            path: URL path to match (supports ``*`` suffix wildcard).

        Returns:
            The first matching captured request dict.

        Raises:
            AssertionError: If no matching request is found, with a
                helpful message listing all captured paths.
        """
        captured = self.get_captured_requests()
        for req in captured:
            if req.get("method") == method:
                req_path = req.get("path", "")
                if path.endswith("*"):
                    if req_path.startswith(path[:-1]):
                        return req
                elif req_path == path:
                    return req

        paths = [
            f"  {r.get('method')} {r.get('path')}" for r in captured
        ]
        path_list = "\n".join(paths) if paths else "  (none)"
        raise AssertionError(
            f"Expected {method} {path} but it was not captured.\n"
            f"Captured requests:\n{path_list}"
        )

    @contextmanager
    def capture(self) -> Generator[CaptureContext, None, None]:
        """Context manager for capturing requests.

        Clears captured requests on entry, freezes a snapshot on exit.

        Yields:
            A :class:`CaptureContext` for inspecting captured requests.

        Example::

            with proxy.capture() as cap:
                # ... DUT makes HTTP requests through the proxy ...
                cap.wait_for_request("GET", "/api/v1/status")

            # After the block, cap.requests is a frozen snapshot
            assert len(cap.requests) == 1
        """
        self.clear_captured_requests()
        ctx = CaptureContext(self)
        try:
            yield ctx
        finally:
            ctx._freeze()

    # ── Context managers ────────────────────────────────────────

    @contextmanager
    def session(
        self,
        mode: str = "mock",
        web_ui: bool = False,
        replay_file: str = "",
    ) -> Generator[MitmproxyClient, None, None]:
        """Context manager for a proxy session.

        Starts the proxy on entry and stops it on exit, ensuring
        clean teardown even if the test fails.

        Args:
            mode: Operational mode.
            web_ui: Enable mitmweb browser UI.
            replay_file: Flow file for replay mode.

        Yields:
            This client instance.

        Example::

            with proxy.session(mode="mock", web_ui=True) as p:
                p.set_mock("GET", "/api/health", body={"ok": True})
                # ... run test ...
        """
        self.start(mode=mode, web_ui=web_ui, replay_file=replay_file)
        try:
            yield self
        finally:
            self.stop()

    @contextmanager
    def mock_endpoint(
        self,
        method: str,
        path: str,
        status: int = 200,
        body: dict | list | str = "",
        content_type: str = "application/json",
        headers: dict | None = None,
    ) -> Generator[None, None, None]:
        """Context manager for a temporary mock endpoint.

        Sets up the mock on entry and removes it on exit. Useful for
        test-specific overrides on top of a base scenario.

        Args:
            method: HTTP method.
            path: URL path.
            status: HTTP status code.
            body: Response body.
            content_type: Content-Type header.
            headers: Additional headers.

        Example::

            with proxy.mock_endpoint(
                "GET", "/api/v1/updates/check",
                body={"update_available": True, "version": "2.6.0"},
            ):
                # DUT will see the update
                trigger_update_check()
                assert_update_dialog_shown()
            # mock is automatically cleaned up
        """
        self.set_mock(
            method, path, status, body, content_type, headers,
        )
        try:
            yield
        finally:
            self.remove_mock(method, path)

    @contextmanager
    def mock_scenario(
        self, scenario_file: str,
    ) -> Generator[None, None, None]:
        """Context manager for a complete mock scenario.

        Loads a scenario file on entry and clears all mocks on exit.

        Args:
            scenario_file: Path to scenario JSON file.

        Example::

            with proxy.mock_scenario("update-available.json"):
                # all endpoints from the scenario are active
                test_full_update_flow()
        """
        self.load_mock_scenario(scenario_file)
        try:
            yield
        finally:
            self.clear_mocks()

    @contextmanager
    def recording(self) -> Generator[MitmproxyClient, None, None]:
        """Context manager for recording traffic.

        Starts in record mode and stops when done. The flow file
        path is available via ``status()["flow_file"]``.

        Example::

            with proxy.recording() as p:
                # drive through a test scenario on the DUT
                run_golden_path_scenario()

            # flow file saved, check status for path
            files = p.list_flow_files()
        """
        self.start(mode="record")
        try:
            yield self
        finally:
            self.stop()

    # ── Convenience methods ─────────────────────────────────────

    def mock_error(self, method: str, path: str,
                   status: int = 503,
                   message: str = "Service Unavailable") -> str:
        """Shortcut to mock an error response.

        Args:
            method: HTTP method.
            path: URL path.
            status: Error HTTP status code (default 503).
            message: Error message body.

        Returns:
            Confirmation message.
        """
        return self.set_mock(
            method, path, status,
            body={"error": message, "status": status},
        )

    def mock_timeout(self, method: str, path: str) -> str:
        """Mock a gateway timeout (504) response.

        Useful for testing DUT timeout/retry behavior.

        Args:
            method: HTTP method.
            path: URL path.

        Returns:
            Confirmation message.
        """
        return self.set_mock(
            method, path, 504,
            body={"error": "Gateway Timeout"},
        )
