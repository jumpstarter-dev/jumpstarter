"""
Enhanced mitmproxy addon for DUT backend mocking.

Supports the v2 mock configuration format:
  - JSON body responses
  - File-based responses (binary, images, firmware, etc.)
  - Response templates with dynamic expressions
  - Stateful response sequences
  - Request matching (headers, query params)
  - Simulated latency
  - Delegation to custom addon scripts (streaming, WebSocket, etc.)

Loaded by mitmdump/mitmweb via:
    mitmdump -s mock_addon.py

Configuration is read from:
    {mock_dir}/endpoints.json    (v1 flat format)
    {mock_dir}/*.json            (v2 format with "endpoints" key)

The addon hot-reloads config when the file changes on disk.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import random
import re
import socket as _socket
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from mitmproxy import ctx, http

# ── Template engine (lightweight, no dependencies) ──────────


class TemplateEngine:
    """Simple template expression evaluator for dynamic mock bodies.

    Supported expressions:
        {{now_iso}}                    → Current ISO 8601 timestamp
        {{now_epoch}}                  → Current Unix timestamp
        {{random_int(min, max)}}       → Random integer in range
        {{random_float(min, max)}}     → Random float in range
        {{random_choice(a, b, c)}}     → Random selection from list
        {{uuid}}                       → Random UUID v4
        {{counter(name)}}              → Auto-incrementing counter
        {{env(VAR_NAME)}}              → Environment variable
        {{request_path}}               → The matched request path
        {{request_header(name)}}       → Value of a request header
    """

    _counters: dict[str, int] = defaultdict(int)
    _pattern = re.compile(r"\{\{(.+?)\}\}")

    @classmethod
    def render(cls, template: Any, flow: http.HTTPFlow | None = None) -> Any:
        """Recursively render template expressions in a value."""
        if isinstance(template, str):
            return cls._render_string(template, flow)
        elif isinstance(template, dict):
            return {k: cls.render(v, flow) for k, v in template.items()}
        elif isinstance(template, list):
            return [cls.render(v, flow) for v in template]
        return template

    @classmethod
    def _render_string(cls, s: str, flow: http.HTTPFlow | None) -> Any:
        """Render a single string, resolving all {{...}} expressions."""
        # If the entire string is one expression, return native type
        match = cls._pattern.fullmatch(s.strip())
        if match:
            return cls._evaluate(match.group(1).strip(), flow)

        # Otherwise, substitute within the string
        def replacer(m):
            result = cls._evaluate(m.group(1).strip(), flow)
            return str(result)

        return cls._pattern.sub(replacer, s)

    @classmethod
    def _evaluate(cls, expr: str, flow: http.HTTPFlow | None) -> Any:
        """Evaluate a single template expression."""
        if expr == "now_iso":
            return datetime.now(timezone.utc).isoformat()
        elif expr == "now_epoch":
            return int(time.time())
        elif expr == "uuid":
            import uuid
            return str(uuid.uuid4())
        elif expr.startswith("random_int("):
            args = cls._parse_args(expr)
            return random.randint(int(args[0]), int(args[1]))
        elif expr.startswith("random_float("):
            args = cls._parse_args(expr)
            return round(random.uniform(float(args[0]), float(args[1])), 2)
        elif expr.startswith("random_choice("):
            args = cls._parse_args(expr)
            return random.choice(args)
        elif expr.startswith("counter("):
            args = cls._parse_args(expr)
            name = args[0]
            cls._counters[name] += 1
            return cls._counters[name]
        elif expr.startswith("env("):
            args = cls._parse_args(expr)
            return os.environ.get(args[0], "")
        elif expr == "request_path" and flow:
            return flow.request.path
        elif expr.startswith("request_header(") and flow:
            args = cls._parse_args(expr)
            return flow.request.headers.get(args[0], "")
        else:
            ctx.log.warn(f"Unknown template expression: {{{{{expr}}}}}")
            return f"{{{{{expr}}}}}"

    @staticmethod
    def _parse_args(expr: str) -> list[str]:
        """Parse arguments from an expression like 'func(a, b, c)'."""
        inner = expr[expr.index("(") + 1 : expr.rindex(")")]
        args = []
        for arg in inner.split(","):
            arg = arg.strip().strip("'\"")
            args.append(arg)
        return args


# ── Custom addon loader ─────────────────────────────────────


class AddonRegistry:
    """Loads and manages custom addon scripts for complex mock behaviors.

    Custom addons are Python files in the addons directory that
    implement a handler class. They're referenced from the mock
    config by name (filename without .py extension).

    Example addon structure::

        # addons/hls_audio_stream.py
        class Handler:
            def handle(self, flow: http.HTTPFlow, config: dict) -> bool:
                # Return True if this addon handled the request
                ...
    """

    def __init__(self, addons_dir: str):
        self.addons_dir = Path(addons_dir)
        self._handlers: dict[str, Any] = {}

    def get_handler(self, name: str) -> Any | None:
        """Load and cache a custom addon handler by name."""
        if name in self._handlers:
            return self._handlers[name]

        script_path = self.addons_dir / f"{name}.py"
        if not script_path.exists():
            ctx.log.error(f"Addon script not found: {script_path}")
            return None

        try:
            spec = importlib.util.spec_from_file_location(
                f"hil_addon_{name}", script_path,
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            if hasattr(module, "Handler"):
                handler = module.Handler()
                self._handlers[name] = handler
                ctx.log.info(f"Loaded addon: {name}")
                return handler
            else:
                ctx.log.error(
                    f"Addon {name} missing Handler class"
                )
                return None
        except Exception as e:
            ctx.log.error(f"Failed to load addon {name}: {e}")
            return None

    def reload(self, name: str):
        """Force reload an addon (e.g., after file change)."""
        if name in self._handlers:
            del self._handlers[name]
        return self.get_handler(name)


# ── Capture client ──────────────────────────────────────────

CAPTURE_SOCKET = "/opt/jumpstarter/mitmproxy/capture.sock"


class CaptureClient:
    """Sends captured request events to the driver via Unix socket.

    Connects lazily and reconnects once on failure. If the socket is
    unavailable (e.g., driver not running), events are silently dropped.
    """

    def __init__(self, socket_path: str = CAPTURE_SOCKET):
        self._socket_path = socket_path
        self._sock: _socket.socket | None = None

    def _connect(self) -> bool:
        try:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            sock.connect(self._socket_path)
            self._sock = sock
            return True
        except OSError:
            self._sock = None
            return False

    def send_event(self, event: dict):
        """Send a JSON event line. Reconnects once on failure."""
        payload = json.dumps(event) + "\n"
        for attempt in range(2):
            if self._sock is None:
                if not self._connect():
                    return
            try:
                self._sock.sendall(payload.encode())
                return
            except OSError:
                self.close()
                if attempt == 0:
                    continue
                return

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None


# ── Main addon ──────────────────────────────────────────────


class MitmproxyMockAddon:
    """Enhanced mock addon with file serving, templates, sequences,
    and custom addon delegation.

    Configuration format (v2)::

        {
          "config": {
            "files_dir": "/opt/jumpstarter/mitmproxy/mock-files",
            "addons_dir": "/opt/jumpstarter/mitmproxy/addons",
            "default_latency_ms": 0
          },
          "endpoints": {
            "GET /api/v1/status": {
              "status": 200,
              "body": {"ok": true}
            },
            "GET /firmware.bin": {
              "status": 200,
              "file": "firmware/test.bin",
              "content_type": "application/octet-stream"
            },
            "GET /stream/audio*": {
              "addon": "hls_audio_stream"
            }
          }
        }

    Also supports the v1 flat format (just endpoints, no wrapper).
    """

    # Default config directory — overridden by env var or config
    MOCK_DIR = os.environ.get(
        "MITMPROXY_MOCK_DIR", "/opt/jumpstarter/mitmproxy/mock-responses"
    )

    def __init__(self):
        self.config: dict = {}
        self.endpoints: dict[str, dict] = {}
        self.files_dir: Path = Path(self.MOCK_DIR) / "../mock-files"
        self.addon_registry: AddonRegistry | None = None
        self._config_mtime: float = 0
        self._config_path = Path(self.MOCK_DIR) / "endpoints.json"
        self._sequence_state: dict[str, int] = defaultdict(int)
        self._capture_client = CaptureClient(CAPTURE_SOCKET)
        self._load_config()

    # ── Config loading ──────────────────────────────────────

    def _load_config(self):
        """Load or reload config if the file has changed on disk."""
        if not self._config_path.exists():
            return

        try:
            mtime = self._config_path.stat().st_mtime
            if mtime <= self._config_mtime:
                return  # No changes

            with open(self._config_path) as f:
                raw = json.load(f)

            # Detect v1 vs v2 format
            if "endpoints" in raw:
                # v2 format
                self.config = raw.get("config", {})
                self.endpoints = raw["endpoints"]
            else:
                # v1 flat format (backward compatible)
                self.config = {}
                self.endpoints = raw

            # Apply config
            files_dir = self.config.get("files_dir")
            if files_dir:
                self.files_dir = Path(files_dir)
            else:
                self.files_dir = Path(self.MOCK_DIR).parent / "mock-files"

            addons_dir = self.config.get(
                "addons_dir",
                str(Path(self.MOCK_DIR).parent / "addons"),
            )
            self.addon_registry = AddonRegistry(addons_dir)

            self._config_mtime = mtime
            ctx.log.info(
                f"Loaded {len(self.endpoints)} endpoint(s) "
                f"(files: {self.files_dir}, addons: {addons_dir})"
            )

        except Exception as e:
            ctx.log.error(f"Failed to load config: {e}")

    # ── Request matching ────────────────────────────────────

    def _find_endpoint(
        self, method: str, path: str, flow: http.HTTPFlow,
    ) -> tuple[str, dict] | None:
        """Find the best matching endpoint for a request.

        Matching priority:
        1. Exact match: "GET /api/v1/status"
        2. Wildcard match: "GET /api/v1/nav*"
        3. Priority field (higher = matched first)
        4. Match conditions (headers, query params)

        For WebSocket upgrades, also checks "WEBSOCKET /path".
        """
        self._load_config()  # Hot-reload

        candidates: list[tuple[int, str, dict]] = []

        # Check for WebSocket upgrade
        is_websocket = (
            flow.request.headers.get("Upgrade", "").lower() == "websocket"
        )

        check_keys = [f"{method} {path}"]
        if is_websocket:
            check_keys.append(f"WEBSOCKET {path}")

        for key in check_keys:
            if key in self.endpoints:
                ep = self.endpoints[key]
                if self._matches_conditions(ep, flow):
                    priority = ep.get("priority", 0)
                    candidates.append((priority, key, ep))

        # Wildcard matching
        for pattern, ep in self.endpoints.items():
            if not pattern.endswith("*"):
                continue

            parts = pattern.split(" ", 1)
            if len(parts) != 2:
                continue

            pat_method, pat_path = parts
            prefix = pat_path.rstrip("*")

            match_method = (
                pat_method == method
                or (is_websocket and pat_method == "WEBSOCKET")
            )

            if match_method and path.startswith(prefix):
                if self._matches_conditions(ep, flow):
                    priority = ep.get("priority", 0)
                    candidates.append((priority, pattern, ep))

        if not candidates:
            return None

        # Sort by priority (highest first), then by specificity
        # (longer patterns = more specific)
        candidates.sort(key=lambda c: (-c[0], -len(c[1])))
        return (candidates[0][1], candidates[0][2])

    def _matches_conditions(
        self, endpoint: dict, flow: http.HTTPFlow,
    ) -> bool:
        """Check if a request matches the endpoint's conditions."""
        match_rules = endpoint.get("match")
        if not match_rules:
            return True

        # Header presence check
        required_headers = match_rules.get("headers", {})
        for header, value in required_headers.items():
            actual = flow.request.headers.get(header)
            if actual is None:
                return False
            if value and actual != value:
                return False

        # Header absence check
        absent_headers = match_rules.get("headers_absent", [])
        for header in absent_headers:
            if header in flow.request.headers:
                return False

        # Query parameter check
        required_params = match_rules.get("query", {})
        for param, value in required_params.items():
            actual = flow.request.query.get(param)
            if actual is None:
                return False
            if value and actual != value:
                return False

        # Body content check (substring)
        body_contains = match_rules.get("body_contains")
        if body_contains:
            body = flow.request.get_text() or ""
            if body_contains not in body:
                return False

        return True

    # ── Response generation ─────────────────────────────────

    def request(self, flow: http.HTTPFlow):
        """Main request hook: find and apply mock responses."""
        result = self._find_endpoint(
            flow.request.method, flow.request.path, flow,
        )

        if result is None:
            return  # No mock, passthrough to real server

        key, endpoint = result

        # Delegate to custom addon
        if "addon" in endpoint:
            self._handle_addon(flow, endpoint)
            return

        # Handle response sequences (stateful)
        if "sequence" in endpoint:
            self._handle_sequence(flow, key, endpoint)
            return

        # Handle regular response
        self._send_response(flow, endpoint)

    def _send_response(self, flow: http.HTTPFlow, endpoint: dict):
        """Build and send a mock response from an endpoint definition."""
        status = int(endpoint.get("status", 200))
        content_type = endpoint.get(
            "content_type",
            self.config.get("default_content_type", "application/json"),
        )

        # Simulated latency
        latency_ms = endpoint.get(
            "latency_ms",
            self.config.get("default_latency_ms", 0),
        )
        if latency_ms > 0:
            time.sleep(latency_ms / 1000.0)

        # Build response headers
        resp_headers = {"Content-Type": content_type}
        resp_headers.update(endpoint.get("headers", {}))

        # Determine body source
        if "file" in endpoint:
            body = self._read_file(endpoint["file"])
            if body is None:
                flow.response = http.Response.make(
                    500,
                    json.dumps({
                        "error": f"Mock file not found: {endpoint['file']}"
                    }).encode(),
                    {"Content-Type": "application/json"},
                )
                return
        elif "body_template" in endpoint:
            rendered = TemplateEngine.render(
                endpoint["body_template"], flow,
            )
            body = json.dumps(rendered).encode()
        elif "body" in endpoint:
            body_val = endpoint["body"]
            if isinstance(body_val, (dict, list)):
                body = json.dumps(body_val).encode()
            elif isinstance(body_val, str):
                body = body_val.encode()
            else:
                body = str(body_val).encode()
        else:
            body = b""

        ctx.log.info(
            f"Mock: {flow.request.method} {flow.request.path} "
            f"→ {status} ({len(body)} bytes)"
        )

        flow.response = http.Response.make(status, body, resp_headers)
        flow.metadata["_jmp_mocked"] = True

    def _handle_sequence(
        self, flow: http.HTTPFlow, key: str, endpoint: dict,
    ):
        """Handle stateful response sequences.

        Each entry in the "sequence" list has an optional "repeat"
        count. The addon tracks how many times each endpoint has
        been called and advances through the sequence.
        """
        sequence = endpoint["sequence"]
        call_num = self._sequence_state[key]

        # Find which step we're on
        position = 0
        for step in sequence:
            repeat = step.get("repeat", float("inf"))
            if call_num < position + repeat:
                self._send_response(flow, step)
                self._sequence_state[key] += 1
                return
            position += repeat

        # Past the end of the sequence: use last entry
        self._send_response(flow, sequence[-1])
        self._sequence_state[key] += 1

    def _handle_addon(self, flow: http.HTTPFlow, endpoint: dict):
        """Delegate request handling to a custom addon script."""
        addon_name = endpoint["addon"]
        addon_config = endpoint.get("addon_config", {})

        if self.addon_registry is None:
            ctx.log.error("Addon registry not initialized")
            return

        handler = self.addon_registry.get_handler(addon_name)
        if handler is None:
            flow.response = http.Response.make(
                500,
                json.dumps({
                    "error": f"Addon not found: {addon_name}"
                }).encode(),
                {"Content-Type": "application/json"},
            )
            return

        try:
            handled = handler.handle(flow, addon_config)
            if handled:
                flow.metadata["_jmp_mocked"] = True
            else:
                ctx.log.warn(
                    f"Addon {addon_name} did not handle request"
                )
        except Exception as e:
            ctx.log.error(f"Addon {addon_name} error: {e}")
            flow.response = http.Response.make(
                500,
                json.dumps({
                    "error": f"Addon error: {e}"
                }).encode(),
                {"Content-Type": "application/json"},
            )

    # ── File serving ────────────────────────────────────────

    def _read_file(self, relative_path: str) -> bytes | None:
        """Read a file from the files directory.

        Args:
            relative_path: Path relative to files_dir.

        Returns:
            File contents as bytes, or None if not found.
        """
        file_path = self.files_dir / relative_path

        # Security: prevent path traversal
        try:
            file_path = file_path.resolve()
            files_dir_resolved = self.files_dir.resolve()
            if not str(file_path).startswith(str(files_dir_resolved)):
                ctx.log.error(f"Path traversal blocked: {relative_path}")
                return None
        except (OSError, ValueError):
            return None

        if not file_path.exists():
            ctx.log.error(f"Mock file not found: {file_path}")
            return None

        try:
            return file_path.read_bytes()
        except OSError as e:
            ctx.log.error(f"Failed to read {file_path}: {e}")
            return None

    # ── WebSocket handling ──────────────────────────────────

    def websocket_message(self, flow: http.HTTPFlow):
        """Route WebSocket messages to custom addons if configured."""
        result = self._find_endpoint(
            "WEBSOCKET", flow.request.path, flow,
        )
        if result is None:
            return

        _, endpoint = result
        if "addon" not in endpoint:
            return

        addon_name = endpoint["addon"]
        if self.addon_registry is None:
            return

        handler = self.addon_registry.get_handler(addon_name)
        if handler and hasattr(handler, "websocket_message"):
            try:
                handler.websocket_message(flow, endpoint.get("addon_config", {}))
            except Exception as e:
                ctx.log.error(
                    f"Addon {addon_name} websocket error: {e}"
                )

    def _build_capture_event(
        self, flow: http.HTTPFlow, response_status: int,
    ) -> dict:
        """Build a capture event dict from a flow."""
        parsed = urlparse(flow.request.pretty_url)
        return {
            "timestamp": time.time(),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "path": flow.request.path,
            "headers": dict(flow.request.headers),
            "query": parse_qs(parsed.query),
            "body": flow.request.get_text() or "",
            "response_status": response_status,
            "was_mocked": bool(flow.metadata.get("_jmp_mocked")),
        }

    def response(self, flow: http.HTTPFlow):
        """Log all responses and emit capture events."""
        if flow.response:
            ctx.log.debug(
                f"{flow.request.method} {flow.request.pretty_url} "
                f"→ {flow.response.status_code}"
            )
            event = self._build_capture_event(
                flow, flow.response.status_code,
            )
            self._capture_client.send_event(event)

    def error(self, flow: http.HTTPFlow):
        """Emit a capture event for upstream connection failures."""
        event = self._build_capture_event(flow, 0)
        self._capture_client.send_event(event)


# ── Entry point ─────────────────────────────────────────────

addons = [MitmproxyMockAddon()]
