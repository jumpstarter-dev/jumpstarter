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

import base64
import fnmatch
import json
from contextlib import contextmanager
from ipaddress import IPv6Address, ip_address
from pathlib import Path
from threading import Event
from typing import Any, Generator

import click
import yaml

from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group


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
              replay_file: str = "", port: int = 0) -> str:
        """Start the proxy in the specified mode.

        Args:
            mode: One of "mock", "passthrough", "record", "replay".
            web_ui: Launch mitmweb (browser UI) instead of mitmdump.
            replay_file: Flow file path for replay mode.
            port: Override the listen port (0 uses the configured default).

        Returns:
            Status message with connection details.
        """
        return self.call("start", mode, web_ui, replay_file, port)

    def stop(self) -> str:
        """Stop the proxy process.

        Returns:
            Status message.
        """
        return self.call("stop")

    def restart(self, mode: str = "", web_ui: bool = False,
                replay_file: str = "", port: int = 0) -> str:
        """Restart the proxy (optionally with new config).

        Args:
            mode: New mode (empty string keeps current mode).
            web_ui: Enable/disable web UI.
            replay_file: Flow file for replay mode.
            port: Override the listen port (0 keeps current port).

        Returns:
            Status message from start().
        """
        return self.call("restart", mode, web_ui, replay_file, port)

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

    # ── CLI (jmp shell) ────────────────────────────────────────

    def cli(self):  # noqa: C901
        @driver_click_group(self)
        def base():
            """Mitmproxy driver"""
            pass

        # ── Lifecycle commands ─────────────────────────────────

        @base.command("start")
        @click.option(
            "--mode", "-m",
            type=click.Choice(["mock", "passthrough", "record", "replay"]),
            default="mock", show_default=True,
        )
        @click.option("--web-ui", "-w", is_flag=True, help="Enable mitmweb browser UI.")
        @click.option("--replay-file", default="", help="Flow file for replay mode.")
        @click.option("--port", "-p", default=0, type=int,
                       help="Override listen port (default: from exporter config).")
        def start_cmd(mode: str, web_ui: bool, replay_file: str, port: int):
            """Start the mitmproxy process."""
            click.echo(self.start(mode=mode, web_ui=web_ui, replay_file=replay_file, port=port))

        @base.command("stop")
        def stop_cmd():
            """Stop the mitmproxy process."""
            click.echo(self.stop())

        @base.command("restart")
        @click.option(
            "--mode", "-m",
            type=click.Choice(["mock", "passthrough", "record", "replay"]),
            default=None,
            help="New mode (keeps current mode if omitted).",
        )
        @click.option("--web-ui", "-w", is_flag=True, help="Enable mitmweb browser UI.")
        @click.option("--replay-file", default="", help="Flow file for replay mode.")
        @click.option("--port", "-p", default=0, type=int,
                       help="Override listen port (default: keeps current port).")
        def restart_cmd(mode: str | None, web_ui: bool, replay_file: str, port: int):
            """Stop and restart the mitmproxy process."""
            click.echo(self.restart(mode=mode or "", web_ui=web_ui,
                                    replay_file=replay_file, port=port))

        # ── Status command ─────────────────────────────────────

        @base.command("status")
        def status_cmd():
            """Show proxy status."""
            info = self.status()
            running = info.get("running", False)
            mode = info.get("mode", "unknown")
            pid = info.get("pid")

            if not running:
                click.echo("Proxy is not running.")
                return

            click.echo(f"Proxy is running (PID {pid})")
            click.echo(f"  Mode:    {mode}")
            click.echo(f"  Listen:  {info.get('proxy_address')}")
            if info.get("web_ui_enabled"):
                click.echo(f"  Web UI:  {info.get('web_ui_address')}")
            click.echo(f"  Mocks:   {info.get('mock_count', 0)} endpoint(s)")
            if info.get("flow_file"):
                click.echo(f"  Flow:    {info.get('flow_file')}")

        # ── Mock management commands ───────────────────────────

        @base.group("mock")
        def mock_group():
            """Mock endpoint management."""
            pass

        @mock_group.command("list")
        def mock_list_cmd():
            """List configured mock endpoints."""
            mocks = self.list_mocks()
            if not mocks:
                click.echo("No mocks configured.")
                return
            for key, defn in mocks.items():
                summary = _mock_summary(defn)
                click.echo(f"  {key}  ->  {summary}")

        @mock_group.command("clear")
        def mock_clear_cmd():
            """Remove all mock endpoint definitions."""
            click.echo(self.clear_mocks())

        @mock_group.command("load")
        @click.argument("scenario_file")
        def mock_load_cmd(scenario_file: str):
            """Load a mock scenario from a YAML/JSON file or a directory.

            SCENARIO_FILE is a path to a local scenario file, or a
            directory produced by 'capture save' (scenario.yaml is
            loaded automatically from inside the directory). Any
            companion files referenced by 'file:' entries are uploaded
            automatically.
            """
            local_path = Path(scenario_file)
            if local_path.is_dir():
                local_path = local_path / "scenario.yaml"
            if not local_path.exists():
                click.echo(f"File not found: {local_path}")
                return
            try:
                content = local_path.read_text()
            except OSError as e:
                click.echo(f"Error reading file: {e}")
                return

            # Upload companion files referenced by file: entries
            _upload_scenario_files(self, local_path, content)

            click.echo(
                self.load_mock_scenario_content(local_path.name, content)
            )

        # ── Flow file commands ─────────────────────────────────

        @base.group("flow")
        def flow_group():
            """Recorded flow file management."""
            pass

        @flow_group.command("list")
        def flow_list_cmd():
            """List recorded flow files on the exporter."""
            files = self.list_flow_files()
            if not files:
                click.echo("No flow files found.")
                return
            for f in files:
                size = _human_size(f.get("size_bytes", 0))
                click.echo(f"  {f['name']}  ({size}, {f.get('modified', '')})")

        @flow_group.command("save")
        @click.argument("name")
        @click.argument("output", default=None, required=False)
        def flow_save_cmd(name: str, output: str | None):
            """Download a flow file from the exporter to a local file.

            NAME is the filename as shown by 'j proxy flow list'.
            OUTPUT defaults to NAME in the current directory.
            """
            dest = Path(output) if output else Path(name)
            data = self.get_flow_file(name)
            dest.write_bytes(data)
            click.echo(f"Flow file saved to: {dest.resolve()}")

        # ── Capture commands ───────────────────────────────────

        @base.group("capture", invoke_without_command=True)
        @click.option(
            "-f", "--filter",
            "filter_pattern",
            default="",
            help="Path glob filter for watch (e.g. '/api/v1/*').",
        )
        @click.pass_context
        def capture_group(ctx, filter_pattern: str):
            """Request capture management.

            When invoked without a subcommand, streams live requests
            (equivalent to 'capture watch').
            """
            if ctx.invoked_subcommand is not None:
                return
            click.echo("Watching captured requests (Ctrl+C to stop)...")
            try:
                for event in self.watch_captured_requests():
                    if filter_pattern:
                        path = event.get("path", "").split("?")[0]
                        if not fnmatch.fnmatch(path, filter_pattern):
                            continue
                    click.echo(_format_capture_entry(event))
            except KeyboardInterrupt:
                pass

        @capture_group.command("list")
        def capture_list_cmd():
            """Show captured requests."""
            reqs = self.get_captured_requests()
            if not reqs:
                click.echo("No captured requests.")
                return
            click.echo(f"{len(reqs)} captured request(s):")
            for r in reqs:
                click.echo(_format_capture_entry(r))

        @capture_group.command("clear")
        def capture_clear_cmd():
            """Clear all captured requests."""
            click.echo(self.clear_captured_requests())

        @capture_group.command("watch")
        @click.option(
            "-f", "--filter",
            "filter_pattern",
            default="",
            help="Path glob filter (e.g. '/api/v1/*').",
        )
        def capture_watch_cmd(filter_pattern: str):
            """Watch captured requests in real-time.

            Streams live requests to the terminal as they flow through
            the proxy. Press Ctrl+C to stop.
            """
            click.echo("Watching captured requests (Ctrl+C to stop)...")
            try:
                for event in self.watch_captured_requests():
                    if filter_pattern:
                        path = event.get("path", "").split("?")[0]
                        if not fnmatch.fnmatch(path, filter_pattern):
                            continue
                    click.echo(_format_capture_entry(event))
            except KeyboardInterrupt:
                pass

        @capture_group.command("save")
        @click.argument("directory", type=click.Path())
        @click.option(
            "-f", "--filter",
            "filter_pattern",
            default="",
            help="Path glob filter (e.g. '/api/v1/*').",
        )
        @click.option(
            "--exclude-mocked",
            is_flag=True,
            help="Skip requests served by the mock addon.",
        )
        def capture_save_cmd(directory: str, filter_pattern: str,
                             exclude_mocked: bool):
            """Save captured traffic as a scenario to DIRECTORY.

            Generates a v2 scenario from captured requests, suitable for
            loading with 'j proxy mock load'. JSON response bodies are
            included inline; binary/large bodies are saved as companion
            files under responses/ preserving the URL path structure.

            Creates DIRECTORY and writes scenario.yaml plus any response
            files inside it.
            """
            yaml_str, file_paths = self.export_captured_scenario(
                filter_pattern=filter_pattern,
                exclude_mocked=exclude_mocked,
            )

            out_dir = Path(directory)
            out_dir.mkdir(parents=True, exist_ok=True)
            yaml_path = out_dir / "scenario.yaml"
            yaml_path.write_text(yaml_str)
            click.echo(f"Scenario written to: {yaml_path.resolve()}")

            # Download companion files from the exporter
            for rel_path in file_paths:
                data = self.get_captured_file(rel_path)
                file_path = out_dir / rel_path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_bytes(data)
                click.echo(f"  {rel_path}")

            # Clean up spool files after successful export
            click.echo(self.clean_capture_spool())

        # ── Web UI forwarding ──────────────────────────────────

        @base.command("web")
        @click.option("--address", default="localhost", show_default=True)
        @click.option("--port", default=8081, show_default=True, type=int)
        def web_cmd(address: str, port: int):
            """Forward mitmweb UI to a local TCP port.

            Opens a local listener that tunnels to the mitmweb web UI
            running on the exporter host.
            """
            from contextlib import asynccontextmanager
            from functools import partial

            from jumpstarter.client.adapters import blocking
            from jumpstarter.common import TemporaryTcpListener
            from jumpstarter.streams.common import forward_stream

            async def handler(client, method, conn):
                async with conn:
                    async with client.stream_async(method) as stream:
                        async with forward_stream(conn, stream):
                            pass

            @blocking
            @asynccontextmanager
            async def portforward(*, client, method, local_host, local_port):
                async with TemporaryTcpListener(
                    partial(handler, client, method),
                    local_host=local_host,
                    local_port=local_port,
                ) as addr:
                    yield addr

            with portforward(
                client=self,
                method="connect_web",
                local_host=address,
                local_port=port,
            ) as addr:
                host = ip_address(addr[0])
                actual_port = addr[1]
                if isinstance(host, IPv6Address):
                    url = f"http://[{host}]:{actual_port}"
                else:
                    url = f"http://{host}:{actual_port}"
                auth_url = f"{url}/?token=jumpstarter"
                click.echo(f"mitmweb UI available at: {auth_url}")
                click.echo("Press Ctrl+C to stop forwarding.")
                try:
                    # Loop with a timeout so the main thread can
                    # receive and handle KeyboardInterrupt promptly.
                    stop = Event()
                    while not stop.wait(timeout=0.5):
                        pass
                except KeyboardInterrupt:
                    click.echo("\nStopping...")
                    # The portforward context manager teardown may block
                    # waiting for active connections to drain. Install a
                    # handler so a second Ctrl+C force-exits immediately.
                    import os as _os
                    import signal as _signal
                    _signal.signal(
                        _signal.SIGINT, lambda *_: _os._exit(0),
                    )

        # ── CA certificate ─────────────────────────────────────

        @base.command("cert")
        @click.argument("output", default="mitmproxy-ca-cert.pem")
        def cert_cmd(output: str):
            """Download the mitmproxy CA certificate to a local file.

            OUTPUT is the local file path to write the PEM certificate to.
            Defaults to mitmproxy-ca-cert.pem in the current directory.
            """

            pem = self.get_ca_cert()
            out = Path(output)
            out.write_text(pem)
            click.echo(f"CA certificate written to: {out.resolve()}")

        return base

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

    def set_mock_patch(self, method: str, path: str,
                       patches: dict) -> str:
        """Mock an endpoint in patch mode (passthrough + field overwrite).

        The request passes through to the real server. When the response
        comes back, the specified fields are deep-merged into the JSON
        body before delivery to the DUT.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: URL path to match.
            patches: Dict to deep-merge into the response body. Use
                ``key[N]`` syntax for array indexing.

        Returns:
            Confirmation message.

        Example::

            proxy.set_mock_patch(
                "GET", "/rest/v3/experience/modules/nonPII",
                {"ModuleListResponse": {"moduleList": {"modules[0]": {
                    "status": "Inactive"
                }}}},
            )
        """
        return self.call(
            "set_mock_patch", method, path, json.dumps(patches),
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
        """Load a mock scenario from a JSON or YAML file on the exporter.

        Replaces all current mocks. Files with ``.yaml`` or ``.yml``
        extensions are parsed as YAML; all others as JSON.

        Args:
            scenario_file: Filename (relative to mock_dir) or
                absolute path (.json, .yaml, .yml).

        Returns:
            Status message with endpoint count.
        """
        return self.call("load_mock_scenario", scenario_file)

    def load_mock_scenario_content(self, filename: str, content: str) -> str:
        """Upload and load a mock scenario from raw file content.

        Reads a local scenario file on the client side and sends its
        contents to the exporter for parsing and activation.

        Args:
            filename: Original filename (extension determines parser).
            content: Raw file content as a string.

        Returns:
            Status message with endpoint count.
        """
        return self.call("load_mock_scenario_content", filename, content)

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

    def get_flow_file(self, name: str) -> bytes:
        """Download a recorded flow file from the exporter via streaming.

        Uses chunked streaming transfer so files of any size can be
        downloaded without hitting gRPC message limits.

        Args:
            name: Filename as returned by :meth:`list_flow_files`
                (e.g. ``capture_20260101.bin``).

        Returns:
            Raw file content.
        """
        chunks = []
        for b64_chunk in self.streamingcall("get_flow_file", name):
            chunks.append(base64.b64decode(b64_chunk))
        return b"".join(chunks)

    # ── CA certificate ──────────────────────────────────────────

    def get_ca_cert_path(self) -> str:
        """Get the path to the mitmproxy CA certificate on the exporter.

        This certificate must be installed on the DUT for HTTPS
        interception.

        Returns:
            Path to the PEM certificate file.
        """
        return self.call("get_ca_cert_path")

    def get_ca_cert(self) -> str:
        """Read the mitmproxy CA certificate from the exporter.

        Returns the PEM-encoded certificate contents so it can be
        saved locally or pushed to the DUT.

        Returns:
            PEM-encoded CA certificate string.

        Raises:
            RuntimeError: If the certificate has not been generated yet
                (start the proxy once to create it).

        Example::

            pem = proxy.get_ca_cert()
            Path("/tmp/mitmproxy-ca.pem").write_text(pem)
        """
        result = self.call("get_ca_cert")
        if result.startswith("Error:"):
            raise RuntimeError(result)
        return result

    # ── Capture management ──────────────────────────────────────

    def get_captured_requests(self) -> list[dict]:
        """Return all captured requests.

        Returns:
            List of captured request dicts.
        """
        return json.loads(self.call("get_captured_requests"))

    def watch_captured_requests(self) -> Generator[dict, None, None]:
        """Stream captured requests as they arrive.

        Yields existing requests first, then new ones in real-time.

        Yields:
            Parsed capture event dicts.
        """
        for event_json in self.streamingcall("watch_captured_requests"):
            yield json.loads(event_json)

    def clear_captured_requests(self) -> str:
        """Clear all captured requests.

        Returns:
            Message with the count of cleared requests.
        """
        return self.call("clear_captured_requests")

    def export_captured_scenario(
        self, filter_pattern: str = "", exclude_mocked: bool = False,
    ) -> tuple[str, list[str]]:
        """Export captured requests as a v2 scenario YAML string.

        Deduplicates by ``METHOD /path`` (last response wins). JSON
        bodies are rendered as native YAML. Large/binary bodies are
        written to files on the exporter and listed for download.

        Args:
            filter_pattern: Optional path glob (e.g. ``/api/v1/*``).
            exclude_mocked: Skip requests served by the mock addon.

        Returns:
            Tuple of (yaml_string, file_paths_list) where file_paths
            are relative paths that can be fetched with
            :meth:`get_captured_file`.
        """
        result = json.loads(self.call(
            "export_captured_scenario", filter_pattern, exclude_mocked,
        ))
        return result["yaml"], result.get("files", [])

    def get_captured_file(self, relative_path: str) -> bytes:
        """Download a captured file from the exporter via streaming.

        Uses chunked streaming transfer so files of any size can be
        downloaded without hitting gRPC message limits.

        Args:
            relative_path: Path relative to files_dir on the exporter.

        Returns:
            Raw file content.
        """
        chunks = []
        for b64_chunk in self.streamingcall("get_captured_file", relative_path):
            chunks.append(base64.b64decode(b64_chunk))
        return b"".join(chunks)

    def clean_capture_spool(self) -> str:
        """Remove spooled response body files on the exporter.

        Call after exporting a scenario to free disk space.

        Returns:
            Message with the count of removed files.
        """
        return self.call("clean_capture_spool")

    def upload_mock_file(self, relative_path: str, data: bytes) -> str:
        """Upload a binary file to the exporter's mock files directory.

        Used when loading a scenario that has ``file:`` references
        pointing to local files on the client.

        Args:
            relative_path: Path relative to files_dir on the exporter.
            data: Raw file content.

        Returns:
            Confirmation message.
        """
        return self.call(
            "upload_mock_file", relative_path,
            base64.b64encode(data).decode("ascii"),
        )

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
    def mock_patch_endpoint(
        self,
        method: str,
        path: str,
        patches: dict,
    ) -> Generator[None, None, None]:
        """Context manager for a temporary patch mock endpoint.

        Sets up a patch mock on entry and removes it on exit.

        Args:
            method: HTTP method.
            path: URL path.
            patches: Dict to deep-merge into the response body.

        Example::

            with proxy.mock_patch_endpoint(
                "GET", "/rest/v3/experience/modules/nonPII",
                {"ModuleListResponse": {"moduleList": {"modules[0]": {
                    "status": "Inactive"
                }}}},
            ):
                # real server response is patched
                pass
            # patch is automatically cleaned up
        """
        self.set_mock_patch(method, path, patches)
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
            scenario_file: Path to a scenario file (.json, .yaml, .yml)
                or a scenario directory containing ``scenario.yaml``.

        Example::

            with proxy.mock_scenario("update-available"):
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


# ── CLI helpers ────────────────────────────────────────────────


# Fixed column widths (characters):
#   2 (indent) + 8 (timestamp) + 1 + 7 (method) + 1 + PATH + 1 + 3 (status)
#   + 1 + 7 (duration) + 1 + 8 (size) + 1 + 13 (tag) = 54 + PATH
_FIXED_COLS_WIDTH = 54
_MIN_PATH_WIDTH = 20

_METHOD_COLORS: dict[str, str] = {
    "GET": "green",
    "POST": "blue",
    "PUT": "yellow",
    "PATCH": "yellow",
    "DELETE": "red",
    "HEAD": "cyan",
    "OPTIONS": "cyan",
}


def _style_status(status: str | int) -> str:
    """Color a status code string by HTTP status class."""
    text = str(status).rjust(3)
    code = int(status) if str(status).isdigit() else 0
    if code >= 500:
        return click.style(text, fg="red", bold=True)
    if code >= 400:
        return click.style(text, fg="yellow")
    if code >= 300:
        return click.style(text, fg="cyan")
    if code >= 200:
        return click.style(text, fg="green")
    return click.style(text, fg="white")


def _format_capture_entry(entry: dict) -> str:
    """Format a captured request entry for terminal display.

    Shows timestamp, method, path, status, duration, size, and mock tag
    in fixed-width columns for consistent alignment.
    """
    method = entry.get("method", "")
    path = entry.get("path", "")
    status = entry.get("response_status", "")
    was_mocked = entry.get("was_mocked", False)
    timestamp = entry.get("timestamp", 0)
    duration_ms = entry.get("duration_ms", 0)
    response_size = entry.get("response_size", 0)

    # Format timestamp as HH:MM:SS
    import time as _time
    if timestamp:
        ts_str = click.style(
            _time.strftime("%H:%M:%S", _time.localtime(timestamp)),
            fg="bright_black",
        )
    else:
        ts_str = click.style("--:--:--", fg="bright_black")

    # Color-code HTTP method (padded to 7 chars — length of "OPTIONS")
    styled_method = click.style(
        method.ljust(7), fg=_METHOD_COLORS.get(method, "white"), bold=True,
    )

    # Compute path column width from terminal size, giving path all remaining space
    import shutil as _shutil
    term_width = _shutil.get_terminal_size((100, 24)).columns
    path_width = max(term_width - _FIXED_COLS_WIDTH, _MIN_PATH_WIDTH)

    # Pad or truncate path to computed column width
    if len(path) > path_width:
        path_col = path[: path_width - 1] + "\u2026"
    else:
        path_col = path.ljust(path_width)

    # Color-code status by class (padded to 3 chars)
    styled_status = _style_status(status) if status else click.style("  -", fg="bright_black")

    # Format duration (fixed 7-char column)
    if duration_ms:
        if duration_ms >= 1000:
            dur_text = f"{duration_ms / 1000:.1f}s"
        else:
            dur_text = f"{duration_ms}ms"
    else:
        dur_text = "-"
    dur_str = click.style(dur_text.rjust(7), fg="bright_black")

    # Format response size (fixed 8-char column)
    size_str = click.style(_human_size(response_size).rjust(8), fg="bright_black")

    # Mock/patched/passthrough tag (fixed 13-char column — length of "[passthrough]")
    was_patched = entry.get("was_patched", False)
    if was_patched:
        tag = click.style("[patched]".ljust(13), fg="yellow")
    elif was_mocked:
        tag = click.style("[mocked]".ljust(13), fg="green")
    else:
        tag = click.style("[passthrough]", fg="bright_black")

    return f"  {ts_str} {styled_method} {path_col} {styled_status} {dur_str} {size_str} {tag}"


def _mock_summary(defn: dict) -> str:
    """One-line summary of a mock endpoint definition."""
    if "rules" in defn:
        return f"{len(defn['rules'])} rule(s)"
    if "sequence" in defn:
        return f"{len(defn['sequence'])} step(s)"
    if "body_template" in defn:
        return f"{defn.get('status', 200)} (template)"
    if "addon" in defn:
        return f"addon:{defn['addon']}"
    if "file" in defn:
        return f"{defn.get('status', 200)} file:{defn['file']}"
    status = defn.get("status", 200)
    latency = defn.get("latency_ms")
    s = str(status)
    if latency:
        s += f" (+{latency}ms)"
    return s


def _human_size(nbytes: int) -> str:
    """Format a byte count as a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _collect_file_entries(endpoints: dict) -> list[dict]:
    """Collect all dicts that might contain a ``file`` key from a scenario.

    Walks top-level endpoints plus nested ``rules`` and ``sequence``
    entries. Also handles URL-keyed list values (from ``mocks:`` key
    format) where each list item is an endpoint dict.
    """
    entries: list[dict] = []
    for ep in endpoints.values():
        if isinstance(ep, list):
            # URL-keyed list format: each item is an endpoint dict
            items = ep
        elif isinstance(ep, dict):
            items = [ep]
        else:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            entries.append(item)
            # Check nested response dict
            if isinstance(item.get("response"), dict):
                entries.append(item["response"])
            for rule in item.get("rules", []):
                if isinstance(rule, dict):
                    entries.append(rule)
            for step in item.get("sequence", []):
                if isinstance(step, dict):
                    entries.append(step)
    return entries


def _upload_scenario_files(
    client: MitmproxyClient, scenario_path: Path, content: str,
) -> None:
    """Scan a scenario for ``file:`` references and upload them.

    Reads each referenced file relative to the scenario file's parent
    directory and uploads it to the exporter's mock files directory.
    Handles files at the endpoint level and inside ``rules`` entries.
    """
    try:
        if scenario_path.suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(content)
        else:
            raw = json.loads(content)
    except (yaml.YAMLError, json.JSONDecodeError):
        return

    if not isinstance(raw, dict):
        return

    if "endpoints" in raw:
        endpoints = raw["endpoints"]
    elif "mocks" in raw:
        endpoints = raw["mocks"]
    else:
        endpoints = raw
    if not isinstance(endpoints, dict):
        return

    base_dir = scenario_path.parent

    for entry in _collect_file_entries(endpoints):
        if "file" not in entry:
            continue
        file_ref = entry["file"]
        file_path = base_dir / file_ref
        if file_path.exists():
            click.echo(f"  uploading {file_ref}")
            client.upload_mock_file(file_ref, file_path.read_bytes())
