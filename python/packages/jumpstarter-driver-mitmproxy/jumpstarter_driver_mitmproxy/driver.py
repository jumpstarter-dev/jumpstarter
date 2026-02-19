"""
Jumpstarter exporter driver for mitmproxy.

Manages mitmdump/mitmweb as a subprocess on the exporter host, providing
HTTP(S) interception, traffic recording, server replay, and API endpoint
mocking for DUT (device under test) HiL testing.

The driver supports four operational modes:

- **mock**: Intercept traffic and return mock responses for configured
  API endpoints. This is the primary mode for DUT testing where
  you need deterministic backend responses.

- **passthrough**: Transparent proxy that logs traffic without
  modifying it. Useful for debugging what the DUT is actually
  sending to production servers.

- **record**: Capture all traffic to a binary flow file for later
  replay. Use this to record a "golden" session against a real
  backend, then replay it deterministically in CI.

- **replay**: Serve responses from a previously recorded flow file.
  Combined with record mode, this enables fully offline testing.

Each mode can optionally run with the mitmweb UI for interactive
debugging, or headless via mitmdump for CI/CD pipelines.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, model_validator

from jumpstarter.driver import Driver, export, exportstream

logger = logging.getLogger(__name__)


class ListenConfig(BaseModel):
    """Proxy listener address configuration."""

    host: str = "0.0.0.0"
    port: int = 8080


class WebConfig(BaseModel):
    """mitmweb UI address configuration."""

    host: str = "0.0.0.0"
    port: int = 8081


class DirectoriesConfig(BaseModel):
    """Directory layout configuration.

    All subdirectories default to ``{data}/<name>`` when left empty.
    """

    data: str = "/opt/jumpstarter/mitmproxy"
    conf: str = ""
    flows: str = ""
    addons: str = ""
    mocks: str = ""
    files: str = ""

    @model_validator(mode="after")
    def _resolve_defaults(self) -> "DirectoriesConfig":
        if not self.conf:
            self.conf = str(Path(self.data) / "conf")
        if not self.flows:
            self.flows = str(Path(self.data) / "flows")
        if not self.addons:
            self.addons = str(Path(self.data) / "addons")
        if not self.mocks:
            self.mocks = str(Path(self.data) / "mock-responses")
        if not self.files:
            self.files = str(Path(self.data) / "mock-files")
        return self


@dataclass(kw_only=True)
class MitmproxyDriver(Driver):
    """Jumpstarter exporter driver for mitmproxy.

    Manages a mitmdump or mitmweb process on the exporter host, exposing
    proxy control, mock configuration, and traffic recording APIs to
    the Jumpstarter client.

    Configuration fields are automatically populated from the exporter
    YAML config under the ``config:`` key.

    Example exporter config::

        export:
          proxy:
            type: jumpstarter_driver_mitmproxy.driver.MitmproxyDriver
            config:
              listen:
                port: 8080
              web:
                port: 8081
              directories:
                data: /opt/jumpstarter/mitmproxy
              ssl_insecure: true
              mock_scenario: happy-path.yaml
              mocks:
                GET /api/v1/health:
                  status: 200
                  body: {ok: true}
    """

    # ── Configuration (from exporter YAML) ──────────────────────

    listen: dict = field(default_factory=dict)
    """Proxy listener address (host/port). See :class:`ListenConfig`."""

    web: dict = field(default_factory=dict)
    """mitmweb UI address (host/port). See :class:`WebConfig`."""

    directories: dict = field(default_factory=dict)
    """Directory layout. See :class:`DirectoriesConfig`."""

    ssl_insecure: bool = True
    """Skip upstream SSL certificate verification (useful for dev/test)."""

    mock_scenario: str = ""
    """Scenario file to auto-load on startup (relative to mocks dir or absolute)."""

    mocks: dict = field(default_factory=dict)
    """Inline mock endpoint definitions, loaded at startup."""

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.listen = ListenConfig.model_validate(self.listen)
        self.web = WebConfig.model_validate(self.web)
        self.directories = DirectoriesConfig.model_validate(self.directories)

    # ── Internal state (not from config) ────────────────────────

    _process: subprocess.Popen | None = field(
        default=None, init=False, repr=False
    )
    _mock_endpoints: dict = field(default_factory=dict, init=False)
    _state_store: dict = field(default_factory=dict, init=False)
    _current_mode: str = field(default="stopped", init=False)
    _web_ui_enabled: bool = field(default=False, init=False)
    _current_flow_file: str | None = field(default=None, init=False)

    # Capture infrastructure
    _capture_socket_path: str | None = field(
        default=None, init=False, repr=False
    )
    _capture_server_sock: socket.socket | None = field(
        default=None, init=False, repr=False
    )
    _capture_server_thread: threading.Thread | None = field(
        default=None, init=False, repr=False
    )
    _capture_reader_thread: threading.Thread | None = field(
        default=None, init=False, repr=False
    )
    _captured_requests: list = field(default_factory=list, init=False)
    _capture_lock: threading.Lock = field(
        default_factory=threading.Lock, init=False
    )
    _capture_running: bool = field(default=False, init=False)

    @classmethod
    def client(cls) -> str:
        """Return the import path of the corresponding client class."""
        return "jumpstarter_driver_mitmproxy.client.MitmproxyClient"

    # ── Lifecycle ───────────────────────────────────────────────

    @export
    def start(self, mode: str = "mock", web_ui: bool = False,
              replay_file: str = "") -> str:
        """Start the mitmproxy process.

        Args:
            mode: Operational mode. One of:
                - ``"mock"``: Intercept and mock configured endpoints
                - ``"passthrough"``: Transparent logging proxy
                - ``"record"``: Capture traffic to a flow file
                - ``"replay"``: Serve from a recorded flow file
            web_ui: If True, start mitmweb (browser UI) instead of
                mitmdump (headless CLI).
            replay_file: Path to a flow file (required for replay mode).
                Can be absolute or relative to ``flow_dir``.

        Returns:
            Status message with proxy and (optionally) web UI URLs.
        """
        if self._process is not None and self._process.poll() is None:
            return (
                f"Already running in '{self._current_mode}' mode "
                f"(PID {self._process.pid}). Stop first."
            )

        if mode not in ("mock", "passthrough", "record", "replay"):
            return (
                f"Unknown mode '{mode}'. "
                f"Use: mock, passthrough, record, replay"
            )

        if mode == "replay" and not replay_file:
            return "Error: replay_file is required for replay mode"

        # Ensure directories exist
        Path(self.directories.flows).mkdir(parents=True, exist_ok=True)
        Path(self.directories.mocks).mkdir(parents=True, exist_ok=True)

        # Start capture server (before addon generation so socket path is set)
        self._start_capture_server()

        # Select binary: mitmweb for UI, mitmdump for headless
        binary = "mitmweb" if web_ui else "mitmdump"

        cmd = [
            binary,
            "--listen-host", self.listen.host,
            "--listen-port", str(self.listen.port),
            "--set", f"confdir={self.directories.conf}",
            "--quiet",
        ]

        if web_ui:
            cmd.extend([
                "--web-host", self.web.host,
                "--web-port", str(self.web.port),
                "--set", "web_open_browser=false",
            ])

        if self.ssl_insecure:
            cmd.extend(["--set", "ssl_insecure=true"])

        # Mode-specific flags
        if mode == "mock":
            self._load_startup_mocks()
            self._write_mock_config()
            addon_path = Path(self.directories.addons) / "mock_addon.py"
            if not addon_path.exists():
                self._generate_default_addon(addon_path)
            cmd.extend(["-s", str(addon_path)])

        elif mode == "record":
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            flow_file = str(
                Path(self.directories.flows) / f"capture_{timestamp}.bin"
            )
            cmd.extend(["-w", flow_file])
            self._current_flow_file = flow_file

        elif mode == "replay":
            # Resolve relative paths against flow_dir
            replay_path = Path(replay_file)
            if not replay_path.is_absolute():
                replay_path = Path(self.directories.flows) / replay_path
            if not replay_path.exists():
                self._stop_capture_server()
                return f"Replay file not found: {replay_path}"
            cmd.extend([
                "--server-replay", str(replay_path),
                "--server-replay-nopop",
            ])

        # passthrough: no extra flags needed

        logger.info("Starting %s: %s", binary, " ".join(cmd))

        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for startup
        time.sleep(2)

        if self._process.poll() is not None:
            stderr = self._process.stderr.read().decode() if self._process.stderr else ""
            self._process = None
            self._stop_capture_server()
            return f"Failed to start: {stderr[:500]}"

        self._current_mode = mode
        self._web_ui_enabled = web_ui

        msg = (
            f"Started in '{mode}' mode on "
            f"{self.listen.host}:{self.listen.port} "
            f"(PID {self._process.pid})"
        )

        if web_ui:
            msg += f" | Web UI: http://{self.web.host}:{self.web.port}"

        if mode == "record" and self._current_flow_file:
            msg += f" | Recording to: {self._current_flow_file}"

        return msg

    @export
    def stop(self) -> str:
        """Stop the running mitmproxy process.

        Sends SIGINT for a graceful shutdown, then SIGTERM if needed.

        Returns:
            Status message.
        """
        if self._process is None or self._process.poll() is not None:
            self._process = None
            self._current_mode = "stopped"
            self._web_ui_enabled = False
            return "Not running"

        pid = self._process.pid

        # Graceful shutdown via SIGINT (flushes flow files)
        self._process.send_signal(signal.SIGINT)
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            logger.warning("Graceful shutdown timed out, sending SIGTERM")
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.error("SIGTERM timed out, sending SIGKILL")
                self._process.kill()
                self._process.wait()

        prev_mode = self._current_mode
        flow_file = self._current_flow_file

        self._process = None
        self._current_mode = "stopped"
        self._web_ui_enabled = False
        self._current_flow_file = None

        # Stop capture server (do NOT clear _captured_requests — tests may
        # read captures after stop)
        self._stop_capture_server()

        msg = f"Stopped (was '{prev_mode}' mode, PID {pid})"
        if prev_mode == "record" and flow_file:
            msg += f" | Flow saved to: {flow_file}"

        return msg

    @export
    def restart(self, mode: str = "", web_ui: bool = False,
                replay_file: str = "") -> str:
        """Stop and restart with the given (or previous) configuration.

        If mode is empty, restarts with the same mode as before.

        Returns:
            Status message from start().
        """
        restart_mode = mode if mode else self._current_mode
        restart_web = web_ui or self._web_ui_enabled

        if restart_mode == "stopped":
            restart_mode = "mock"

        self.stop()
        time.sleep(1)
        return self.start(restart_mode, restart_web, replay_file)

    # ── Status ──────────────────────────────────────────────────

    @export
    def status(self) -> str:
        """Get the current status of the proxy.

        Returns:
            JSON string with status details.
        """
        running = (
            self._process is not None
            and self._process.poll() is None
        )
        info = {
            "running": running,
            "mode": self._current_mode,
            "pid": self._process.pid if running else None,
            "proxy_address": (
                f"{self.listen.host}:{self.listen.port}"
                if running else None
            ),
            "web_ui_enabled": self._web_ui_enabled,
            "web_ui_address": (
                f"http://{self.web.host}:{self.web.port}"
                if running and self._web_ui_enabled else None
            ),
            "mock_count": len(self._mock_endpoints),
            "flow_file": self._current_flow_file,
        }
        return json.dumps(info)

    @export
    def is_running(self) -> bool:
        """Check if the mitmproxy process is alive.

        Returns:
            True if the process is running.
        """
        return (
            self._process is not None
            and self._process.poll() is None
        )

    @exportstream
    @asynccontextmanager
    async def connect_web(self):
        """Stream a TCP connection to the mitmweb UI."""
        from anyio import connect_tcp

        async with await connect_tcp(
            remote_host=self.web.host,
            remote_port=self.web.port,
        ) as stream:
            yield stream

    # ── Mock management ─────────────────────────────────────────

    @export
    def set_mock(self, method: str, path: str, status: int,
                 body: str,
                 content_type: str = "application/json",
                 headers: str = "{}") -> str:
        """Add or update a mock endpoint.

        The addon script reads mock definitions from a JSON file on
        disk. This method updates that file and, if the proxy is
        running in mock mode, the addon will pick up changes on the
        next request (it watches the file modification time).

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path to match (e.g., "/api/v1/status").
                Append ``*`` for prefix matching.
            status: HTTP status code to return.
            body: Response body as a JSON string.
            content_type: Response Content-Type header.
            headers: Additional response headers as a JSON string.

        Returns:
            Confirmation message.
        """
        key = f"{method.upper()} {path}"
        self._mock_endpoints[key] = {
            "status": int(status),
            "body": json.loads(body) if body else {},
            "content_type": content_type,
            "headers": json.loads(headers) if headers else {},
        }
        self._write_mock_config()
        return f"Mock set: {key} → {int(status)}"

    @export
    def remove_mock(self, method: str, path: str) -> str:
        """Remove a mock endpoint.

        Args:
            method: HTTP method.
            path: URL path.

        Returns:
            Confirmation or not-found message.
        """
        key = f"{method.upper()} {path}"
        if key in self._mock_endpoints:
            del self._mock_endpoints[key]
            self._write_mock_config()
            return f"Removed mock: {key}"
        return f"Mock not found: {key}"

    @export
    def clear_mocks(self) -> str:
        """Remove all mock endpoint definitions.

        Returns:
            Confirmation message.
        """
        count = len(self._mock_endpoints)
        self._mock_endpoints.clear()
        self._write_mock_config()
        return f"Cleared {count} mock(s)"

    @export
    def list_mocks(self) -> str:
        """List all currently configured mock endpoints.

        Returns:
            JSON string with all mock definitions.
        """
        return json.dumps(self._mock_endpoints, indent=2)

    @export
    def set_mock_file(self, method: str, path: str,
                      file_path: str,
                      content_type: str = "",
                      status: int = 200,
                      headers: str = "{}") -> str:
        """Mock an endpoint to serve a file from disk.

        The file path is relative to the files directory
        (default: ``{data}/mock-files/``).

        Args:
            method: HTTP method (GET, POST, etc.)
            path: URL path to match.
            file_path: Path to file, relative to files_dir.
            content_type: Response Content-Type. Auto-detected from
                extension if empty.
            status: HTTP status code.
            headers: Additional response headers as JSON string.

        Returns:
            Confirmation message.

        Example::

            proxy.set_mock_file(
                "GET", "/api/v1/downloads/firmware.bin",
                "firmware/test.bin",
                content_type="application/octet-stream",
            )
        """
        import mimetypes as _mt

        if not content_type:
            guessed, _ = _mt.guess_type(file_path)
            content_type = guessed or "application/octet-stream"

        key = f"{method.upper()} {path}"
        endpoint: dict = {
            "status": int(status),
            "file": file_path,
            "content_type": content_type,
        }
        extra_headers = json.loads(headers) if headers else {}
        if extra_headers:
            endpoint["headers"] = extra_headers

        self._mock_endpoints[key] = endpoint
        self._write_mock_config()
        return f"File mock set: {key} → {file_path} ({content_type})"

    @export
    def set_mock_with_latency(self, method: str, path: str,
                              status: int, body: str,
                              latency_ms: int,
                              content_type: str = "application/json") -> str:
        """Mock an endpoint with simulated network latency.

        Args:
            method: HTTP method.
            path: URL path.
            status: HTTP status code.
            body: Response body as JSON string.
            latency_ms: Delay in milliseconds before responding.
            content_type: Response Content-Type.

        Returns:
            Confirmation message.
        """
        key = f"{method.upper()} {path}"
        self._mock_endpoints[key] = {
            "status": int(status),
            "body": json.loads(body) if body else {},
            "content_type": content_type,
            "latency_ms": int(latency_ms),
        }
        self._write_mock_config()
        return f"Mock set: {key} → {int(status)} (+{int(latency_ms)}ms)"

    @export
    def set_mock_sequence(self, method: str, path: str,
                          sequence_json: str) -> str:
        """Mock an endpoint with a stateful response sequence.

        Each call to the endpoint advances through the sequence.
        Entries with ``"repeat": N`` are returned N times before
        advancing. The last entry repeats indefinitely.

        Args:
            method: HTTP method.
            path: URL path.
            sequence_json: JSON array of response steps, e.g.::

                [
                    {"status": 200, "body": {"ok": true}, "repeat": 3},
                    {"status": 503, "body": {"error": "down"}, "repeat": 1},
                    {"status": 200, "body": {"ok": true}}
                ]

        Returns:
            Confirmation message.
        """
        key = f"{method.upper()} {path}"
        try:
            sequence = json.loads(sequence_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        if not isinstance(sequence, list) or len(sequence) == 0:
            return "Sequence must be a non-empty JSON array"

        self._mock_endpoints[key] = {"sequence": sequence}
        self._write_mock_config()
        return (
            f"Sequence mock set: {key} → "
            f"{len(sequence)} step(s)"
        )

    @export
    def set_mock_template(self, method: str, path: str,
                          template_json: str,
                          status: int = 200) -> str:
        """Mock an endpoint with a dynamic body template.

        Template expressions are evaluated per-request::

            {{now_iso}}               → ISO 8601 timestamp
            {{random_int(10, 99)}}    → random integer
            {{random_choice(a, b)}}   → random selection
            {{uuid}}                  → UUID v4
            {{counter(name)}}         → auto-incrementing counter
            {{request_path}}          → matched URL path

        Args:
            method: HTTP method.
            path: URL path.
            template_json: JSON object with template expressions.
            status: HTTP status code.

        Returns:
            Confirmation message.
        """
        key = f"{method.upper()} {path}"
        try:
            template = json.loads(template_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        self._mock_endpoints[key] = {
            "status": int(status),
            "body_template": template,
        }
        self._write_mock_config()
        return f"Template mock set: {key} → {int(status)}"

    @export
    def set_mock_addon(self, method: str, path: str,
                       addon_name: str,
                       addon_config_json: str = "{}") -> str:
        """Delegate an endpoint to a custom addon script.

        The addon script must exist in the addons directory as
        ``{addon_name}.py`` and contain a ``Handler`` class with
        a ``handle(flow, config) -> bool`` method.

        Args:
            method: HTTP method (use "WEBSOCKET" for WebSocket).
            path: URL path (wildcards supported).
            addon_name: Name of the addon (filename without .py).
            addon_config_json: JSON config passed to the handler.

        Returns:
            Confirmation message.
        """
        key = f"{method.upper()} {path}"
        try:
            addon_config = json.loads(addon_config_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        endpoint: dict = {"addon": addon_name}
        if addon_config:
            endpoint["addon_config"] = addon_config

        self._mock_endpoints[key] = endpoint
        self._write_mock_config()
        return f"Addon mock set: {key} → {addon_name}"

    @export
    def set_mock_conditional(self, method: str, path: str,
                             rules_json: str) -> str:
        """Mock an endpoint with conditional response rules.

        Rules are evaluated in order; the first rule whose ``match``
        conditions are satisfied wins. A rule with no ``match`` key
        acts as a default fallback.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: URL path to match.
            rules_json: JSON array of rule objects, each containing
                optional ``match`` conditions and response fields
                (``status``, ``body``, ``body_template``, ``headers``,
                ``content_type``, ``latency_ms``, ``sequence``).

        Returns:
            Confirmation message.

        Example rules_json::

            [
                {
                    "match": {"body_json": {"username": "admin"}},
                    "status": 200,
                    "body": {"token": "abc"}
                },
                {
                    "status": 401,
                    "body": {"error": "unauthorized"}
                }
            ]
        """
        key = f"{method.upper()} {path}"
        try:
            rules = json.loads(rules_json)
        except json.JSONDecodeError as e:
            return f"Invalid JSON: {e}"

        if not isinstance(rules, list) or len(rules) == 0:
            return "Rules must be a non-empty JSON array"

        self._mock_endpoints[key] = {"rules": rules}
        self._write_mock_config()
        return (
            f"Conditional mock set: {key} → "
            f"{len(rules)} rule(s)"
        )

    # ── State store ────────────────────────────────────────────

    @export
    def set_state(self, key: str, value_json: str) -> str:
        """Set a key in the shared state store.

        The value is stored as a decoded JSON value (any type: str,
        int, dict, list, bool, null). The state is written to
        ``state.json`` alongside ``endpoints.json`` so the addon
        can hot-reload it.

        Args:
            key: State key name.
            value_json: JSON-encoded value.

        Returns:
            Confirmation message.
        """
        value = json.loads(value_json)
        self._state_store[key] = value
        self._write_state()
        return f"State set: {key}"

    @export
    def get_state(self, key: str) -> str:
        """Get a value from the shared state store.

        Args:
            key: State key name.

        Returns:
            JSON-encoded value, or ``"null"`` if not found.
        """
        return json.dumps(self._state_store.get(key))

    @export
    def clear_state(self) -> str:
        """Clear all keys from the shared state store.

        Returns:
            Confirmation message.
        """
        count = len(self._state_store)
        self._state_store.clear()
        self._write_state()
        return f"Cleared {count} state key(s)"

    @export
    def get_all_state(self) -> str:
        """Get the entire shared state store.

        Returns:
            JSON-encoded dict of all state key-value pairs.
        """
        return json.dumps(self._state_store)

    @export
    def list_addons(self) -> str:
        """List available addon scripts in the addons directory.

        Returns:
            JSON array of addon names (without .py extension).
        """
        addon_path = Path(self.directories.addons)
        if not addon_path.exists():
            return json.dumps([])

        addons = [
            f.stem for f in sorted(addon_path.glob("*.py"))
            if not f.name.startswith("_")
        ]
        return json.dumps(addons)

    @export
    def load_mock_scenario(self, scenario_file: str) -> str:
        """Load a complete mock scenario from a JSON or YAML file.

        Replaces all current mocks with the contents of the file.
        Files with ``.yaml`` or ``.yml`` extensions are parsed as YAML;
        all other extensions are parsed as JSON.

        Args:
            scenario_file: Filename (relative to mock_dir) or absolute
                path to a mock definitions file (.json, .yaml, .yml).

        Returns:
            Status message with count of loaded endpoints.
        """
        path = Path(scenario_file)
        if not path.is_absolute():
            path = Path(self.directories.mocks) / path

        if not path.exists():
            return f"Scenario file not found: {path}"

        try:
            with open(path) as f:
                if path.suffix in (".yaml", ".yml"):
                    raw = yaml.safe_load(f)
                else:
                    raw = json.load(f)
        except (json.JSONDecodeError, yaml.YAMLError, OSError) as e:
            return f"Failed to load scenario: {e}"

        # Handle v2 format (with "endpoints" wrapper) or v1 flat format
        if "endpoints" in raw:
            self._mock_endpoints = raw["endpoints"]
        else:
            self._mock_endpoints = raw

        self._write_mock_config()
        return (
            f"Loaded {len(self._mock_endpoints)} endpoint(s) "
            f"from {path.name}"
        )

    # ── Flow file management ────────────────────────────────────

    @export
    def list_flow_files(self) -> str:
        """List recorded flow files in the flow directory.

        Returns:
            JSON array of flow file info (name, size, modified time).
        """
        flow_path = Path(self.directories.flows)
        files = []
        for f in sorted(flow_path.glob("*.bin")):
            stat = f.stat()
            files.append({
                "name": f.name,
                "path": str(f),
                "size_bytes": stat.st_size,
                "modified": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.localtime(stat.st_mtime),
                ),
            })
        return json.dumps(files, indent=2)

    # ── CA certificate access ───────────────────────────────────

    @export
    def get_ca_cert_path(self) -> str:
        """Get the path to the mitmproxy CA certificate (PEM).

        This certificate must be installed on the DUT for HTTPS
        interception to work without certificate errors.

        Returns:
            Absolute path to the CA certificate file.
        """
        cert_path = Path(self.directories.conf) / "mitmproxy-ca-cert.pem"
        if cert_path.exists():
            return str(cert_path)
        return f"CA cert not found at {cert_path}. Start proxy once to generate."

    @export
    def get_ca_cert(self) -> str:
        """Read and return the mitmproxy CA certificate (PEM).

        Returns:
            The PEM-encoded CA certificate contents, or an error
            message starting with ``"Error:"`` if not found.
        """
        cert_path = Path(self.directories.conf) / "mitmproxy-ca-cert.pem"
        if not cert_path.exists():
            return (
                f"Error: CA cert not found at {cert_path}. "
                f"Start proxy once to generate."
            )
        return cert_path.read_text()

    # ── Capture management ────────────────────────────────────

    @export
    def get_captured_requests(self) -> str:
        """Return all captured requests as a JSON array.

        Returns:
            JSON string of captured request records.
        """
        with self._capture_lock:
            return json.dumps(self._captured_requests)

    @export
    def clear_captured_requests(self) -> str:
        """Clear all captured requests.

        Returns:
            Message with the number of cleared requests.
        """
        with self._capture_lock:
            count = len(self._captured_requests)
            self._captured_requests.clear()
        return f"Cleared {count} captured request(s)"

    @export
    def wait_for_request(self, method: str, path: str,
                         timeout: float = 10.0) -> str:
        """Wait for a matching request to be captured.

        Polls the capture buffer at 0.2s intervals until a matching
        request is found or the timeout expires.

        Args:
            method: HTTP method to match (e.g., "GET").
            path: URL path to match. Append ``*`` for prefix matching.
            timeout: Maximum time to wait in seconds.

        Returns:
            JSON string of the matching request, or a JSON object
            with an "error" key on timeout.
        """
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            with self._capture_lock:
                for req in self._captured_requests:
                    if self._request_matches(req, method, path):
                        return json.dumps(req)
            time.sleep(0.2)
        return json.dumps({
            "error": f"Timed out waiting for {method} {path} "
                     f"after {timeout}s"
        })

    # ── Capture internals ──────────────────────────────────────

    def _start_capture_server(self):
        """Create a Unix domain socket for receiving capture events."""
        # Use a short path to avoid the ~104-char AF_UNIX limit on macOS.
        # Try {data}/capture.sock first; fall back to a temp file.
        preferred = str(Path(self.directories.data) / "capture.sock")
        if len(preferred) < 100:
            sock_path = preferred
            Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
        else:
            fd, sock_path = tempfile.mkstemp(
                prefix="jmp_cap_", suffix=".sock",
            )
            os.close(fd)

        # Remove stale socket / temp placeholder
        try:
            Path(sock_path).unlink()
        except FileNotFoundError:
            pass

        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)
        server.settimeout(1.0)

        self._capture_socket_path = sock_path
        self._capture_server_sock = server
        self._capture_running = True

        self._capture_server_thread = threading.Thread(
            target=self._capture_accept_loop,
            daemon=True,
        )
        self._capture_server_thread.start()
        logger.debug("Capture server listening on %s", sock_path)

    def _capture_accept_loop(self):
        """Accept connections on the capture socket."""
        while self._capture_running:
            try:
                conn, _ = self._capture_server_sock.accept()
                self._capture_reader_thread = threading.Thread(
                    target=self._capture_read_loop,
                    args=(conn,),
                    daemon=True,
                )
                self._capture_reader_thread.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _capture_read_loop(self, conn: socket.socket):
        """Read newline-delimited JSON events from a capture connection."""
        buf = b""
        try:
            while self._capture_running:
                try:
                    data = conn.recv(65536)
                except OSError:
                    break
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        with self._capture_lock:
                            self._captured_requests.append(event)
                    except json.JSONDecodeError:
                        logger.debug("Bad capture JSON: %s", line[:200])
        finally:
            conn.close()

    def _stop_capture_server(self):
        """Shut down the capture socket and background threads."""
        self._capture_running = False

        if self._capture_server_sock is not None:
            try:
                self._capture_server_sock.close()
            except OSError:
                pass
            self._capture_server_sock = None

        if self._capture_server_thread is not None:
            self._capture_server_thread.join(timeout=5)
            self._capture_server_thread = None

        if self._capture_reader_thread is not None:
            self._capture_reader_thread.join(timeout=5)
            self._capture_reader_thread = None

        if self._capture_socket_path is not None:
            try:
                Path(self._capture_socket_path).unlink()
            except FileNotFoundError:
                pass
            self._capture_socket_path = None

    @staticmethod
    def _request_matches(req: dict, method: str, path: str) -> bool:
        """Check if a captured request matches method and path.

        Supports exact match and wildcard (``*`` suffix) prefix matching.
        """
        if req.get("method") != method:
            return False
        req_path = req.get("path", "")
        if path.endswith("*"):
            return req_path.startswith(path[:-1])
        return req_path == path

    # ── Internal helpers ────────────────────────────────────────

    def _load_startup_mocks(self):
        """Load mock_scenario file and inline mocks at startup.

        The scenario file is loaded first as a base layer, then inline
        ``mocks`` from the exporter config are overlaid on top (higher
        priority).
        """
        if self.mock_scenario:
            scenario_path = Path(self.mock_scenario)
            if not scenario_path.is_absolute():
                scenario_path = Path(self.directories.mocks) / scenario_path
            if scenario_path.exists():
                with open(scenario_path) as f:
                    if scenario_path.suffix in (".yaml", ".yml"):
                        raw = yaml.safe_load(f)
                    else:
                        raw = json.load(f)
                if "endpoints" in raw:
                    self._mock_endpoints = raw["endpoints"]
                else:
                    self._mock_endpoints = raw

        if self.mocks:
            self._mock_endpoints.update(self.mocks)

    def _write_mock_config(self):
        """Write mock endpoint definitions to disk in v2 format."""
        mock_path = Path(self.directories.mocks)
        mock_path.mkdir(parents=True, exist_ok=True)
        config_file = mock_path / "endpoints.json"

        v2_config = {
            "config": {
                "files_dir": self.directories.files,
                "addons_dir": self.directories.addons,
                "default_latency_ms": 0,
                "default_content_type": "application/json",
            },
            "endpoints": self._mock_endpoints,
        }

        with open(config_file, "w") as f:
            json.dump(v2_config, f, indent=2)
        logger.debug(
            "Wrote %d mock(s) to %s",
            len(self._mock_endpoints),
            config_file,
        )

    def _write_state(self):
        """Write shared state store to disk for addon hot-reload."""
        mock_path = Path(self.directories.mocks)
        mock_path.mkdir(parents=True, exist_ok=True)
        state_file = mock_path / "state.json"

        with open(state_file, "w") as f:
            json.dump(self._state_store, f, indent=2)
        logger.debug(
            "Wrote %d state key(s) to %s",
            len(self._state_store),
            state_file,
        )

    def _generate_default_addon(self, path: Path):
        """Install the bundled v2 mitmproxy addon script.

        Copies the full-featured MitmproxyMockAddon that supports
        file serving, templates, sequences, and custom addon delegation.
        If the bundled addon isn't available (e.g., running outside the
        package), falls back to generating a minimal v2-compatible addon.
        """
        path.parent.mkdir(parents=True, exist_ok=True)

        # Try to copy the bundled addon
        bundled = Path(__file__).parent / "bundled_addon.py"
        if bundled.exists():
            import shutil
            shutil.copy2(bundled, path)
            # Patch the MOCK_DIR to match this driver's config
            content = path.read_text()
            content = content.replace(
                '/opt/jumpstarter/mitmproxy/mock-responses',
                self.directories.mocks,
            )
            content = content.replace(
                '/opt/jumpstarter/mitmproxy/capture.sock',
                self._capture_socket_path or '',
            )
            path.write_text(content)
            logger.info("Installed bundled v2 addon: %s", path)
            return

        # Fallback: generate minimal v2-compatible addon inline
        addon_code = f'''\
"""
Auto-generated mitmproxy addon (v2 format) for DUT backend mocking.
Reads from: {self.directories.mocks}/endpoints.json
Managed by jumpstarter-driver-mitmproxy.
"""
import json, os, time
from pathlib import Path
from mitmproxy import http, ctx

class MitmproxyMockAddon:
    MOCK_DIR = "{self.directories.mocks}"
    def __init__(self):
        self.config = {{}}
        self.endpoints = {{}}
        self.files_dir = Path(self.MOCK_DIR).parent / "mock-files"
        self._config_mtime = 0.0
        self._config_path = Path(self.MOCK_DIR) / "endpoints.json"
        self._load_config()

    def _load_config(self):
        if not self._config_path.exists():
            return
        try:
            mtime = self._config_path.stat().st_mtime
            if mtime <= self._config_mtime:
                return
            with open(self._config_path) as f:
                raw = json.load(f)
            if "endpoints" in raw:
                self.config = raw.get("config", {{}})
                self.endpoints = raw["endpoints"]
            else:
                self.endpoints = raw
            if self.config.get("files_dir"):
                self.files_dir = Path(self.config["files_dir"])
            self._config_mtime = mtime
            ctx.log.info(f"Loaded {{len(self.endpoints)}} endpoint(s)")
        except Exception as e:
            ctx.log.error(f"Config load failed: {{e}}")

    def request(self, flow: http.HTTPFlow):
        self._load_config()
        method, path = flow.request.method, flow.request.path
        ep = self.endpoints.get(f"{{method}} {{path}}")
        if ep is None:
            for pat, e in self.endpoints.items():
                if pat.endswith("*"):
                    pm, pp = pat.split(" ", 1)
                    if method == pm and path.startswith(pp.rstrip("*")):
                        ep = e
                        break
        if ep is None:
            return
        latency = ep.get("latency_ms", self.config.get("default_latency_ms", 0))
        if latency > 0:
            time.sleep(latency / 1000.0)
        status = int(ep.get("status", 200))
        ct = ep.get("content_type", "application/json")
        hdrs = {{"Content-Type": ct}}
        hdrs.update(ep.get("headers", {{}}))
        if "file" in ep:
            fp = self.files_dir / ep["file"]
            body = fp.read_bytes() if fp.exists() else b"file not found"
        elif "body" in ep:
            b = ep["body"]
            body = json.dumps(b).encode() if isinstance(b, (dict, list)) else str(b).encode()
        else:
            body = b""
        ctx.log.info(f"Mock: {{method}} {{path}} -> {{status}}")
        flow.response = http.Response.make(status, body, hdrs)

    def response(self, flow: http.HTTPFlow):
        if flow.response:
            ctx.log.debug(f"{{flow.request.method}} {{flow.request.pretty_url}} -> {{flow.response.status_code}}")

addons = [MitmproxyMockAddon()]
'''
        with open(path, "w") as f:
            f.write(addon_code)
        logger.info("Generated fallback v2 addon: %s", path)
