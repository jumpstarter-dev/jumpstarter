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

import asyncio
import hashlib
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

# ── Helpers ──────────────────────────────────────────────────


def _resolve_dotted_path(obj, path: str):
    """Traverse a dotted path into a nested dict/list structure.

    Returns None if any segment is missing or the structure
    doesn't support indexing.

    Examples::

        _resolve_dotted_path({"a": {"b": 1}}, "a.b")  # → 1
        _resolve_dotted_path({"x": [10, 20]}, "x.1")  # → 20
    """
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return obj


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
        {{env(VAR_NAME)}}              → Environment variable (allowlisted only)
        {{request_path}}               → The matched request path
        {{request_header(name)}}       → Value of a request header
        {{request_body}}               → Raw request body text
        {{request_body_json(key)}}     → JSON field from request body
        {{request_query(param)}}       → Query parameter value
        {{request_path_segment(idx)}}  → URL path segment by index
        {{state(key)}}                 → Value from shared state store
        {{state(key, default)}}        → Value with fallback default
    """

    _counters: dict[str, int] = defaultdict(int)

    # Only these environment variables may be read via {{env(...)}} templates.
    # This prevents mock configs from leaking secrets such as credentials or
    # API keys.  Extend this set when new env-driven behaviour is needed.
    ALLOWED_ENV_VARS: set[str] = {
        "JUMPSTARTER_ENV",
        "JUMPSTARTER_DEVICE_ID",
        "JUMPSTARTER_MOCK_PROFILE",
        "JUMPSTARTER_REGION",
        "NODE_ENV",
        "MOCK_SCENARIO",
    }
    _pattern = re.compile(r"\{\{(.+?)\}\}")

    @classmethod
    def render(
        cls,
        template: Any,
        flow: http.HTTPFlow | None = None,
        state: dict | None = None,
    ) -> Any:
        """Recursively render template expressions in a value."""
        if isinstance(template, str):
            return cls._render_string(template, flow, state)
        elif isinstance(template, dict):
            return {k: cls.render(v, flow, state) for k, v in template.items()}
        elif isinstance(template, list):
            return [cls.render(v, flow, state) for v in template]
        return template

    @classmethod
    def _render_string(
        cls,
        s: str,
        flow: http.HTTPFlow | None,
        state: dict | None = None,
    ) -> Any:
        """Render a single string, resolving all {{...}} expressions."""
        # If the entire string is one expression, return native type
        match = cls._pattern.fullmatch(s.strip())
        if match:
            return cls._evaluate(match.group(1).strip(), flow, state)

        # Otherwise, substitute within the string
        def replacer(m):
            result = cls._evaluate(m.group(1).strip(), flow, state)
            return str(result)

        return cls._pattern.sub(replacer, s)

    @classmethod
    def _evaluate(
        cls,
        expr: str,
        flow: http.HTTPFlow | None,
        state: dict | None = None,
    ) -> Any:
        """Evaluate a single template expression."""
        result = cls._evaluate_builtin(expr)
        if result is not None:
            return result

        result = cls._evaluate_flow(expr, flow)
        if result is not None:
            return result

        if expr.startswith("state("):
            return cls._evaluate_state(expr, state)

        ctx.log.warn(f"Unknown template expression: {{{{{expr}}}}}")
        return f"{{{{{expr}}}}}"

    @classmethod
    def _evaluate_builtin(cls, expr: str) -> Any | None:
        """Evaluate built-in expressions (no flow needed)."""
        if expr == "now_iso":
            return datetime.now(timezone.utc).isoformat()
        if expr == "now_epoch":
            return int(time.time())
        if expr == "uuid":
            import uuid
            return str(uuid.uuid4())
        if expr.startswith("random_int("):
            args = cls._parse_args(expr)
            return random.randint(int(args[0]), int(args[1]))
        if expr.startswith("random_float("):
            args = cls._parse_args(expr)
            return round(random.uniform(float(args[0]), float(args[1])), 2)
        if expr.startswith("random_choice("):
            args = cls._parse_args(expr)
            return random.choice(args)
        if expr.startswith("counter("):
            args = cls._parse_args(expr)
            name = args[0]
            cls._counters[name] += 1
            return cls._counters[name]
        if expr.startswith("env("):
            args = cls._parse_args(expr)
            var_name = args[0]
            if var_name in cls.ALLOWED_ENV_VARS:
                ctx.log.warn(f"env() template used: allowed variable '{var_name}'")
                return os.environ.get(var_name, "")
            ctx.log.warn(f"env() template blocked: variable '{var_name}' is not in ALLOWED_ENV_VARS")
            return ""
        return None

    @classmethod
    def _evaluate_flow(
        cls, expr: str, flow: http.HTTPFlow | None,
    ) -> Any | None:
        """Evaluate flow-dependent expressions."""
        if flow is None:
            return None
        if expr == "request_path":
            return flow.request.path
        if expr.startswith("request_header("):
            args = cls._parse_args(expr)
            return flow.request.headers.get(args[0], "")
        if expr == "request_body":
            return flow.request.get_text() or ""
        if expr.startswith("request_body_json("):
            args = cls._parse_args(expr)
            try:
                body_obj = json.loads(flow.request.get_text() or "{}")
            except json.JSONDecodeError:
                return None
            return _resolve_dotted_path(body_obj, args[0])
        if expr.startswith("request_query("):
            args = cls._parse_args(expr)
            return flow.request.query.get(args[0], "")
        if expr.startswith("request_path_segment("):
            args = cls._parse_args(expr)
            segments = [s for s in flow.request.path.split("/") if s]
            try:
                return segments[int(args[0])]
            except (IndexError, ValueError):
                return ""
        return None

    @classmethod
    def _evaluate_state(
        cls, expr: str, state: dict | None,
    ) -> Any:
        """Evaluate state() expressions."""
        args = cls._parse_args(expr)
        key = args[0]
        default = args[1] if len(args) > 1 else None
        if state is not None:
            return state.get(key, default)
        return default

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
            if spec is None or spec.loader is None:
                ctx.log.error(
                    f"Failed to create import spec for addon '{name}' "
                    f"at {script_path}"
                )
                return None
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
CAPTURE_SPOOL_DIR = "/opt/jumpstarter/mitmproxy/capture-spool"

# Response bodies at or below this size are sent inline in capture events.
# Larger or binary bodies are spooled to disk and only the file path is sent.
_INLINE_BODY_LIMIT = 256 * 1024  # 256 KB


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
        self._sequence_start: dict[str, float] = {}
        self._capture_client = CaptureClient(CAPTURE_SOCKET)
        self._state: dict = {}
        self._state_mtime: float = 0
        self._state_path = Path(self.MOCK_DIR) / "state.json"
        self._spool_dir = Path(CAPTURE_SPOOL_DIR)
        self._spool_dir.mkdir(parents=True, exist_ok=True)
        self._spool_counter = 0
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

    def _load_state(self):
        """Load or reload shared state if the file has changed on disk."""
        if not self._state_path.exists():
            return

        try:
            mtime = self._state_path.stat().st_mtime
            if mtime <= self._state_mtime:
                return  # No changes

            with open(self._state_path) as f:
                self._state = json.load(f)

            self._state_mtime = mtime
        except Exception as e:
            ctx.log.error(f"Failed to load state: {e}")

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
        self._collect_wildcard_matches(
            method, path, is_websocket, flow, candidates,
        )

        if not candidates:
            return None

        # Sort by priority (highest first), then by specificity
        # (longer patterns = more specific)
        candidates.sort(key=lambda c: (-c[0], -len(c[1])))
        return (candidates[0][1], candidates[0][2])

    def _collect_wildcard_matches(
        self,
        method: str,
        path: str,
        is_websocket: bool,
        flow: http.HTTPFlow,
        candidates: list[tuple[int, str, dict]],
    ):
        """Collect wildcard endpoint matches into candidates list."""
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

    def _matches_conditions(
        self, endpoint: dict, flow: http.HTTPFlow,
    ) -> bool:
        """Check if a request matches the endpoint's conditions."""
        match_rules = endpoint.get("match")
        if not match_rules:
            return True

        return (
            self._check_headers(match_rules, flow)
            and self._check_headers_absent(match_rules, flow)
            and self._check_query(match_rules, flow)
            and self._check_body_contains(match_rules, flow)
            and self._check_body_json(match_rules, flow)
        )

    @staticmethod
    def _check_headers(match_rules: dict, flow: http.HTTPFlow) -> bool:
        """Check required header presence and values."""
        for header, value in match_rules.get("headers", {}).items():
            actual = flow.request.headers.get(header)
            if actual is None:
                return False
            if value and actual != value:
                return False
        return True

    @staticmethod
    def _check_headers_absent(match_rules: dict, flow: http.HTTPFlow) -> bool:
        """Check that certain headers are absent."""
        for header in match_rules.get("headers_absent", []):
            if header in flow.request.headers:
                return False
        return True

    @staticmethod
    def _check_query(match_rules: dict, flow: http.HTTPFlow) -> bool:
        """Check required query parameter presence and values."""
        for param, value in match_rules.get("query", {}).items():
            actual = flow.request.query.get(param)
            if actual is None:
                return False
            if value and actual != value:
                return False
        return True

    @staticmethod
    def _check_body_contains(match_rules: dict, flow: http.HTTPFlow) -> bool:
        """Check that request body contains a substring."""
        body_contains = match_rules.get("body_contains")
        if body_contains:
            body = flow.request.get_text() or ""
            if body_contains not in body:
                return False
        return True

    @staticmethod
    def _check_body_json(match_rules: dict, flow: http.HTTPFlow) -> bool:
        """Check exact match on parsed JSON fields in request body."""
        body_json_match = match_rules.get("body_json", {})
        if body_json_match:
            try:
                body_obj = json.loads(flow.request.get_text() or "{}")
            except json.JSONDecodeError:
                return False
            for field_path, expected in body_json_match.items():
                actual = _resolve_dotted_path(body_obj, field_path)
                if actual != expected:
                    return False
        return True

    # ── Response generation ─────────────────────────────────

    async def request(self, flow: http.HTTPFlow):
        """Main request hook: find and apply mock responses."""
        self._load_state()

        # Strip query string for endpoint key matching; query params
        # remain available in flow for _matches_conditions.
        path = flow.request.path.split("?")[0]

        result = self._find_endpoint(
            flow.request.method, path, flow,
        )

        if result is None:
            return  # No mock, passthrough to real server

        key, endpoint = result

        # Delegate to custom addon
        if "addon" in endpoint:
            self._handle_addon(flow, endpoint)
            return

        # Handle conditional rules (multiple response variants)
        if "rules" in endpoint:
            await self._handle_rules(flow, key, endpoint)
            return

        # Handle response sequences (stateful)
        if "sequence" in endpoint:
            await self._handle_sequence(flow, key, endpoint)
            return

        # Handle regular response
        await self._send_response(flow, endpoint)

    async def _send_response(self, flow: http.HTTPFlow, endpoint: dict):
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
            await asyncio.sleep(latency_ms / 1000.0)

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
                endpoint["body_template"], flow, self._state,
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

    async def _handle_sequence(
        self, flow: http.HTTPFlow, key: str, endpoint: dict,
    ):
        """Handle stateful response sequences.

        Supports two modes:

        **Time-based** (when entries have ``delay_ms``): The addon
        records when the first request arrived and serves the latest
        step whose ``delay_ms`` has elapsed. This lets captured
        traffic replay with realistic timing.

        **Count-based** (legacy, when entries have ``repeat``): The
        addon counts calls and advances through the sequence.
        """
        sequence = endpoint["sequence"]
        has_delays = any("delay_ms" in step for step in sequence)

        if has_delays:
            self._handle_sequence_timed(flow, key, sequence)
        else:
            await self._handle_sequence_counted(flow, key, sequence)

    def _handle_sequence_timed(
        self, flow: http.HTTPFlow, key: str, sequence: list[dict],
    ):
        """Serve the latest step whose delay_ms has elapsed."""
        now = time.time()
        if key not in self._sequence_start:
            self._sequence_start[key] = now

        elapsed_ms = (now - self._sequence_start[key]) * 1000

        # Walk forward through steps; the last one whose delay has
        # elapsed is the active response.
        active_step = sequence[0]
        for step in sequence:
            if step.get("delay_ms", 0) <= elapsed_ms:
                active_step = step
            else:
                break

        # Use _send_response synchronously-safe path: build the
        # response inline (delay_ms is about *when* the step
        # activates, not per-request latency).
        status = int(active_step.get("status", 200))
        self._build_mock_response(flow, active_step, status)

    def _build_mock_response(
        self, flow: http.HTTPFlow, step: dict, status: int,
    ):
        """Build a mock response from a sequence step (sync helper)."""
        content_type = step.get(
            "content_type",
            self.config.get("default_content_type", "application/json"),
        )
        resp_headers = {"Content-Type": content_type}
        resp_headers.update(step.get("headers", {}))

        if "file" in step:
            body = self._read_file(step["file"])
            if body is None:
                body = b""
        elif "body" in step:
            body_val = step["body"]
            if isinstance(body_val, (dict, list)):
                body = json.dumps(body_val).encode()
            elif isinstance(body_val, str):
                body = body_val.encode()
            else:
                body = str(body_val).encode()
        else:
            body = b""

        flow.response = http.Response.make(status, body, resp_headers)
        flow.metadata["_jmp_mocked"] = True

    async def _handle_sequence_counted(
        self, flow: http.HTTPFlow, key: str, sequence: list[dict],
    ):
        """Legacy count-based sequence progression."""
        call_num = self._sequence_state[key]

        position = 0
        for step in sequence:
            repeat = step.get("repeat", float("inf"))
            if call_num < position + repeat:
                await self._send_response(flow, step)
                self._sequence_state[key] += 1
                return
            position += repeat

        # Past the end of the sequence: use last entry
        await self._send_response(flow, sequence[-1])
        self._sequence_state[key] += 1

    async def _handle_rules(
        self, flow: http.HTTPFlow, key: str, endpoint: dict,
    ):
        """Handle conditional mock rules.

        Evaluates rules in order. First rule whose ``match`` conditions
        are satisfied wins. A rule with no ``match`` key is a default
        fallback.
        """
        rules = endpoint["rules"]

        for rule in rules:
            if self._matches_conditions(rule, flow):
                if "sequence" in rule:
                    await self._handle_sequence(flow, key, rule)
                else:
                    await self._send_response(flow, rule)
                return

        # No rule matched — passthrough
        ctx.log.info(
            f"No conditional rule matched for {key}, passing through"
        )

    def _handle_addon(self, flow: http.HTTPFlow, endpoint: dict):
        """Delegate request handling to a custom addon script."""
        addon_name = endpoint["addon"]
        addon_config = endpoint.get("addon_config", {})

        # Inject files_dir so addons can locate data files without hardcoding
        if "files_dir" not in addon_config:
            addon_config["files_dir"] = str(self.files_dir)

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

    def _classify_response_body(
        self, flow: http.HTTPFlow,
    ) -> dict:
        """Classify the response body for capture event inclusion.

        Text/JSON bodies within the inline limit are returned directly.
        Large or binary bodies are spooled to disk and only the file
        path is included in the event.

        Returns a dict with keys: response_body, response_body_file,
        response_content_type, response_headers, response_is_binary.
        """
        resp = flow.response
        if resp is None:
            return {
                "response_body": None,
                "response_body_file": None,
                "response_content_type": None,
                "response_headers": {},
                "response_is_binary": False,
            }

        content_type = resp.headers.get("content-type", "")
        base_ct = content_type.split(";")[0].strip().lower()
        # The stored body is already decompressed by mitmproxy, so strip
        # content-encoding to avoid confusing downstream consumers.
        resp_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() != "content-encoding"
        }

        # Determine if content is text-like
        text_types = (
            "application/json", "text/", "application/xml",
            "application/javascript", "application/x-www-form-urlencoded",
        )
        is_text = any(base_ct.startswith(t) for t in text_types)

        raw_body = resp.get_content()
        body_size = len(raw_body) if raw_body else 0

        if is_text and body_size <= _INLINE_BODY_LIMIT:
            # Inline text body
            try:
                body_text = raw_body.decode("utf-8") if raw_body else ""
            except UnicodeDecodeError:
                body_text = raw_body.decode("latin-1") if raw_body else ""
            return {
                "response_body": body_text,
                "response_body_file": None,
                "response_content_type": base_ct,
                "response_headers": resp_headers,
                "response_is_binary": False,
            }

        if body_size == 0:
            return {
                "response_body": "",
                "response_body_file": None,
                "response_content_type": base_ct,
                "response_headers": resp_headers,
                "response_is_binary": not is_text,
            }

        # Spool large or binary body to disk
        self._spool_counter += 1
        url_hash = hashlib.sha256(
            flow.request.pretty_url.encode()
        ).hexdigest()[:12]
        spool_name = f"{self._spool_counter:06d}_{url_hash}.bin"
        spool_path = self._spool_dir / spool_name
        try:
            spool_path.write_bytes(raw_body)
        except OSError as e:
            ctx.log.error(f"Failed to spool response body: {e}")
            return {
                "response_body": None,
                "response_body_file": None,
                "response_content_type": base_ct,
                "response_headers": resp_headers,
                "response_is_binary": not is_text,
            }

        return {
            "response_body": None,
            "response_body_file": str(spool_path),
            "response_content_type": base_ct,
            "response_headers": resp_headers,
            "response_is_binary": not is_text,
        }

    def _build_capture_event(
        self, flow: http.HTTPFlow, response_status: int,
    ) -> dict:
        """Build a capture event dict from a flow."""
        parsed = urlparse(flow.request.pretty_url)
        body_info = self._classify_response_body(flow)
        # Compute request duration from mitmproxy timestamps
        duration_ms = 0
        if (
            flow.response
            and hasattr(flow.response, "timestamp_end")
            and hasattr(flow.request, "timestamp_start")
            and flow.response.timestamp_end
            and flow.request.timestamp_start
        ):
            duration_ms = round(
                (flow.response.timestamp_end - flow.request.timestamp_start) * 1000,
            )

        # Compute response size
        response_size = 0
        if flow.response:
            raw = flow.response.get_content()
            response_size = len(raw) if raw else 0

        event = {
            "timestamp": time.time(),
            "method": flow.request.method,
            "url": flow.request.pretty_url,
            "path": flow.request.path,
            "headers": dict(flow.request.headers),
            "query": parse_qs(parsed.query),
            "body": flow.request.get_text() or "",
            "response_status": response_status,
            "was_mocked": bool(flow.metadata.get("_jmp_mocked")),
            "duration_ms": duration_ms,
            "response_size": response_size,
        }
        event.update(body_info)
        return event

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
