"""A scoped native WebDriver gateway to an external Appium HTTPS server."""

from __future__ import annotations

import asyncio
import http.client
import json
import logging
import math
import os
import re
import shutil
import signal
import socket
import ssl
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path

import h11
from anyio import CancelScope, EndOfStream, fail_after, to_thread
from jumpstarter_driver_iosdevice.https import HttpsIdentity, https_endpoint

from .http_service import HttpServiceProvider, SimulatorHttpContext

logger = logging.getLogger(__name__)

MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 32 * 1024 * 1024
BUNDLE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,254}\Z")
TOKEN = r"[A-Za-z0-9_-]{1,128}"
# W3C WebDriver error codes are a fixed vocabulary; clients map them to exceptions.
W3C_ERRORS = frozenset(
    {
        "detached shadow root",
        "element click intercepted",
        "element not interactable",
        "insecure certificate",
        "invalid argument",
        "invalid cookie domain",
        "invalid element state",
        "invalid selector",
        "invalid session id",
        "javascript error",
        "move target out of bounds",
        "no such alert",
        "no such cookie",
        "no such element",
        "no such frame",
        "no such shadow root",
        "no such window",
        "script timeout",
        "session not created",
        "stale element reference",
        "timeout",
        "unable to capture screen",
        "unable to set cookie",
        "unexpected alert open",
        "unknown command",
        "unknown error",
        "unknown method",
        "unsupported operation",
    }
)


class PolicyError(ValueError):
    """A client request is outside the selected simulator's native API."""


class ServiceError(RuntimeError):
    """A service failure whose message is safe for clients; details stay in the exporter log."""


def _error(status, code, message):
    return status, {"value": {"error": code, "message": message, "stacktrace": ""}}


def _sanitized(status, result):
    """Keep stack traces, command lines and host paths exporter-local.

    Standard error codes stay, so clients raise their usual exception types.
    """
    if status < 400:
        return status, result
    value = result.get("value")
    code = value.get("error") if isinstance(value, dict) else None
    code = code if code in W3C_ERRORS else "unknown error"
    return _error(status, code, f"Appium returned {code}")


def _log_tail(path, limit=4096):
    try:
        with open(path, "rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - limit))
            return log.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _node_version(env):
    """Best-effort version of the Node.js that the Appium launcher resolves."""
    node = shutil.which("node", path=env.get("PATH"))
    if node is None:
        return None
    try:
        result = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() or None
    except Exception:  # noqa: BLE001 - diagnostics must never mask the startup failure
        return None


def _object(value, name="body"):
    if not isinstance(value, dict):
        raise PolicyError(f"{name} must be an object")
    return value


def _keys(value, allowed, required=()):
    _object(value)
    if set(value) - set(allowed) or not set(required) <= set(value):
        raise PolicyError("Unsupported or missing request fields")


def _string(value, maximum=4096):
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise PolicyError("Expected a bounded string")
    return value


def _number(value, minimum=0, maximum=60000):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise PolicyError("Expected a finite number")
    if not minimum <= value <= maximum:
        raise PolicyError("Number is outside the supported range")
    return value


def _bundle(value):
    if not isinstance(value, str) or not BUNDLE_ID.fullmatch(value):
        raise PolicyError("bundleId must be an application bundle identifier")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError("Duplicate JSON keys are not supported")
        result[key] = value
    return result


def decode_json(body):
    try:
        return _object(json.loads(body, object_pairs_hook=_unique_object, parse_constant=lambda _: None))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise PolicyError("Expected a valid JSON object with unique keys") from exc


class AppiumPolicy:
    """Inject operator-owned identity and admit only native simulator operations."""

    def __init__(self, fixed):
        self.fixed = dict(fixed)
        self.public = {
            key: self.fixed[key]
            for key in ("platformName", "appium:automationName", "appium:udid", "appium:platformVersion")
        }
        self.public["appium:noReset"] = True

    def capabilities(self, body):
        _keys(body, {"capabilities"}, {"capabilities"})
        caps = _object(body["capabilities"], "capabilities")
        _keys(caps, {"alwaysMatch", "firstMatch"})
        always = self._normalize(_object(caps.get("alwaysMatch", {})))
        candidates = caps.get("firstMatch", [{}])
        # A single capability choice prevents ambiguous device selection.
        if not isinstance(candidates, list) or len(candidates) != 1:
            raise PolicyError("Exactly one firstMatch capability choice is supported")
        first = self._normalize(_object(candidates[0]))
        if set(always) & set(first):
            raise PolicyError("Capability appears in both alwaysMatch and firstMatch")
        selected = always | first
        if "appium:bundleId" not in selected:
            raise PolicyError("Install the app with idb, then supply appium:bundleId")
        selected = self.fixed | selected
        return {"capabilities": {"alwaysMatch": selected, "firstMatch": [{}]}}

    def _normalize(self, caps):
        caps = dict(caps)
        options = caps.pop("appium:options", {})
        _object(options, "appium:options")
        for key, value in options.items():
            if ":" in key:
                raise PolicyError("Use unprefixed keys inside appium:options")
            full_key = "appium:" + key
            if full_key in caps:
                raise PolicyError("Duplicate capability in appium:options")
            caps[full_key] = value
        for key, value in caps.items():
            self._capability(key, value)
        return caps

    def _capability(self, key, value):
        if key in self.fixed:
            if type(value) is not type(self.fixed[key]) or value != self.fixed[key]:
                raise PolicyError(f"Capability {key} is controlled by the exporter")
        elif key == "appium:bundleId":
            _bundle(value)
        elif key in {
            "appium:forceAppLaunch",
            "appium:shouldTerminateApp",
            "appium:autoAcceptAlerts",
            "appium:autoDismissAlerts",
        }:
            if type(value) is not bool:
                raise PolicyError(f"Capability {key} must be boolean")
        elif key == "appium:newCommandTimeout":
            _number(value, 1, 600)
        elif key == "appium:waitForIdleTimeout":
            _number(value, 0, 30)
        else:
            raise PolicyError(f"Unsupported capability: {key}")

    def request(self, method, path, body, session):
        if method == "GET" and path == "/status":
            if body:
                raise PolicyError("GET requests cannot have a body")
            return None
        if method == "POST" and path == "/session":
            if session is not None:
                raise PolicyError("This lease already has an Appium session")
            return self.capabilities(body)
        prefix = f"/session/{session}" if session else None
        if prefix is None or not (path == prefix or path.startswith(prefix + "/")):
            raise PolicyError("The request does not address this lease's session")
        suffix = path[len(prefix) :]
        if method == "DELETE" and suffix in {"", "/actions"}:
            _keys(body, ())
            return body
        if method == "GET" and (
            suffix in {"/source", "/screenshot", "/window/rect", "/timeouts", "/alert/text", "/orientation"}
            or re.fullmatch(rf"/element/{TOKEN}/(?:text|name|rect|enabled|displayed|selected|screenshot)", suffix)
            or re.fullmatch(rf"/element/{TOKEN}/attribute/[A-Za-z][A-Za-z0-9_-]{{0,127}}", suffix)
        ):
            _keys(body, ())
            return body
        if method != "POST":
            raise PolicyError("Unsupported native Appium route")
        return self._post(suffix, body)

    def _post(self, suffix, body):
        if suffix == "/execute/sync":
            return self._mobile(body)
        if suffix in {"/element", "/elements"} or re.fullmatch(rf"/element/{TOKEN}/elements?", suffix):
            self._locator(body)
        elif re.fullmatch(rf"/element/{TOKEN}/(?:click|clear)", suffix) or suffix in {
            "/alert/accept",
            "/alert/dismiss",
        }:
            _keys(body, ())
        elif re.fullmatch(rf"/element/{TOKEN}/value", suffix):
            self._element_value(body)
        elif suffix == "/alert/text":
            _keys(body, {"text"}, {"text"})
            _string(body["text"])
        elif suffix == "/timeouts":
            _keys(body, {"implicit"})
            for value in body.values():
                _number(value)
        elif suffix == "/orientation":
            self._orientation(body)
        elif suffix == "/actions":
            self._actions(body)
        else:
            raise PolicyError("Unsupported native Appium route")
        return body

    @staticmethod
    def _locator(body):
        _keys(body, {"using", "value"}, {"using", "value"})
        if body["using"] not in {
            "accessibility id",
            "id",
            "name",
            "class name",
            "xpath",
            "-ios predicate string",
            "-ios class chain",
        }:
            raise PolicyError("Only native element locators are supported")
        _string(body["value"])

    @staticmethod
    def _orientation(body):
        _keys(body, {"orientation"}, {"orientation"})
        if body["orientation"] not in {"LANDSCAPE", "PORTRAIT"}:
            raise PolicyError("Unsupported orientation")

    @staticmethod
    def _element_value(body):
        _keys(body, {"text", "value"})
        if not body:
            raise PolicyError("Element input requires text or value")
        if "text" in body:
            _string(body["text"], 65536)
        if "value" in body:
            if not isinstance(body["value"], list) or len(body["value"]) > 65536:
                raise PolicyError("value must be a bounded string array")
            for value in body["value"]:
                _string(value, 65536)

    @staticmethod
    def _actions(body):
        _keys(body, {"actions"}, {"actions"})
        actions = body["actions"]
        if not isinstance(actions, list) or len(actions) > 10:
            raise PolicyError("actions must be a bounded array")
        duration = 0
        for source in actions:
            _keys(source, {"type", "id", "parameters", "actions"}, {"type", "id", "actions"})
            if source["type"] not in {"pointer", "key", "none"}:
                raise PolicyError("Unsupported native input source")
            _string(source["id"], 128)
            params = source.get("parameters", {})
            _keys(params, {"pointerType"})
            if params and params["pointerType"] != "touch":
                raise PolicyError("Only touch pointer input is supported")
            events = source["actions"]
            if not isinstance(events, list) or len(events) > 1000:
                raise PolicyError("Input sequence is too long")
            for event in events:
                duration += AppiumPolicy._action_event(event)
        if duration > 60000:
            raise PolicyError("Input duration exceeds one minute")

    @staticmethod
    def _action_event(event):
        duration = 0
        _keys(event, {"type", "duration", "x", "y", "button", "origin", "value"}, {"type"})
        if event["type"] not in {"pause", "pointerMove", "pointerDown", "pointerUp", "keyDown", "keyUp"}:
            raise PolicyError("Unsupported input action")
        if "duration" in event:
            duration = _number(event["duration"])
        for name in ("x", "y"):
            if name in event:
                _number(event[name], -100000, 100000)
        if "button" in event:
            _number(event["button"], 0, 4)
        if "value" in event:
            _string(event["value"], 16)
        origin = event.get("origin", "viewport")
        if isinstance(origin, dict):
            _keys(origin, {"element-6066-11e4-a52e-4f735466cecf"}, {"element-6066-11e4-a52e-4f735466cecf"})
            _string(next(iter(origin.values())), 128)
        elif origin not in {"viewport", "pointer"}:
            raise PolicyError("Unsupported pointer origin")
        return duration

    @staticmethod
    def _mobile(body):
        _keys(body, {"script", "args"}, {"script", "args"})
        script = body["script"]
        args = body["args"]
        if not isinstance(script, str) or not isinstance(args, list) or len(args) > 1:
            raise PolicyError("Use at most one native mobile command argument object")
        validate = _MOBILE_COMMANDS.get(script)
        if validate is None:
            raise PolicyError("Unsupported mobile command")
        validate(_object(args[0]) if args else {})
        return body


def _app_command(data):
    # Cross-platform clients such as Appium-Python-Client also send appId.
    _keys(data, {"bundleId", "appId"}, {"bundleId"})
    _bundle(data["bundleId"])
    if data.pop("appId", data["bundleId"]) != data["bundleId"]:
        raise PolicyError("appId must match bundleId")


def _tap_command(data):
    _keys(data, {"x", "y", "elementId", "duration"})
    if not {"x", "y"} <= set(data) and "elementId" not in data:
        raise PolicyError("Provide coordinates or an elementId")
    for name in ("x", "y"):
        if name in data:
            _number(data[name], 0, 100000)
    if "duration" in data:
        _number(data["duration"], 0, 30)
    if "elementId" in data:
        _string(data["elementId"], 128)


def _gesture_command(data):
    _keys(data, {"direction", "elementId"}, {"direction"})
    if data["direction"] not in {"up", "down", "left", "right"}:
        raise PolicyError("Unsupported gesture direction")
    if "elementId" in data:
        _string(data["elementId"], 128)


def _hide_keyboard_command(data):
    _keys(data, {"keys"})
    keys = data.get("keys", [])
    if not isinstance(keys, list) or len(keys) > 8:
        raise PolicyError("keys must be a short list of key names")
    for key in keys:
        _string(key, 64)


def _background_command(data):
    _keys(data, {"seconds"}, {"seconds"})
    _number(data["seconds"], -1, 600)


# The native `mobile:` commands a lease may run, each with its argument validator.
_MOBILE_COMMANDS = {
    **dict.fromkeys(
        (
            "mobile: launchApp",
            "mobile: activateApp",
            "mobile: terminateApp",
            "mobile: queryAppState",
            "mobile: isAppInstalled",
        ),
        _app_command,
    ),
    **dict.fromkeys(("mobile: tap", "mobile: doubleTap", "mobile: touchAndHold"), _tap_command),
    **dict.fromkeys(("mobile: swipe", "mobile: scroll"), _gesture_command),
    "mobile: isKeyboardShown": lambda data: _keys(data, ()),
    "mobile: hideKeyboard": _hide_keyboard_command,
    "mobile: backgroundApp": _background_command,
}


def _reserve_port(host="127.0.0.1"):
    reservation = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        reservation.bind((host, 0))
        return reservation
    except BaseException:
        reservation.close()
        raise


def _operator_options(executable, home, wda, startup_timeout, request_timeout):
    for name, value in (("startup_timeout", startup_timeout), ("request_timeout", request_timeout)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be positive and finite")
    if not all(isinstance(value, str) and value.strip() and "\x00" not in value for value in (executable, home, wda)):
        raise ValueError("executable, home and wda must be nonempty paths")
    executable = shutil.which(executable)
    appium_home, wda_path = Path(home).expanduser().resolve(), Path(wda).expanduser().resolve()
    if executable is None or not appium_home.is_dir() or not wda_path.is_dir() or wda_path.suffix != ".app":
        raise RuntimeError("Configured Appium executable, extension home or WDA app is unavailable")
    return executable, appium_home, wda_path


class AppiumProvider(HttpServiceProvider):
    """One process, TLS identity and active W3C session for one simulator lease."""

    def __init__(
        self, *, context: SimulatorHttpContext, executable, home, wda, startup_timeout=60, request_timeout=180
    ):
        executable, appium_home, wda_path = _operator_options(executable, home, wda, startup_timeout, request_timeout)
        directory, device_set = context.directory, context.device_set
        udid, platform_version = context.udid, context.platform_version
        self.context = context
        self.directory = Path(tempfile.mkdtemp(prefix="appium-", dir=directory))
        self._directory_identity = None
        self.process = None
        self._closed = False
        self._reservations = []
        self._lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._request_gate = asyncio.Lock()
        self._connections = {}
        self._session = None
        self._session_caps = None
        self.request_timeout = request_timeout
        try:
            info = self.directory.lstat()
            self._directory_identity = (info.st_dev, info.st_ino)
            self.identity = HttpsIdentity.generate()
            home = self.directory / "home"
            (home / "Library/Preferences").mkdir(mode=0o700, parents=True)
            config = self.directory / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "server": {
                            "use-drivers": ["xcuitest"],
                            "use-plugins": [],
                            "allow-insecure": [],
                            "deny-insecure": ["*:*"],
                            "relaxed-security": False,
                        }
                    }
                )
            )
            cert, key = self.directory / "backend.crt", self.directory / "backend.key"
            identity = HttpsIdentity.generate(certfile=cert, keyfile=key)
            self._context = ssl.create_default_context(cadata=identity.certificate)
            port_guard = _reserve_port()
            self._reservations.append(port_guard)
            self.port = port_guard.getsockname()[1]
            fixed = {
                "platformName": "iOS",
                "appium:automationName": "XCUITest",
                "appium:udid": udid,
                "appium:platformVersion": platform_version,
                "appium:simulatorDevicesSetPath": str(device_set),
                "appium:noReset": True,
                "appium:fullReset": False,
                "appium:isHeadless": False,
                "appium:usePreinstalledWDA": True,
                "appium:prebuiltWDAPath": str(wda_path),
                "appium:wdaLocalPort": 0,
                "appium:mjpegServerPort": 0,
                "appium:wdaBindingIP": "127.0.0.1",
                "appium:wdaStartupRetries": 1,
                "appium:wdaLaunchTimeout": 90000,
                "appium:wdaConnectionTimeout": 20000,
                "appium:skipLogCapture": True,
                "appium:shutdownOtherSimulators": False,
                "appium:enforceFreshSimulatorCreation": False,
            }
            self.policy = AppiumPolicy(fixed)
            env = {key: value for key, value in os.environ.items() if not key.startswith("APPIUM_")}
            env.update(HOME=str(home), APPIUM_HOME=str(appium_home))
            # Prevent discovery of unrelated project configuration.
            port_guard.close()
            self._reservations.remove(port_guard)
            with (self.directory / "appium.log").open("wb") as log:
                self.process = subprocess.Popen(
                    [
                        executable,
                        "server",
                        "--config",
                        str(config),
                        "--address",
                        "127.0.0.1",
                        "--port",
                        str(self.port),
                        "--default-capabilities",
                        "{}",
                        "--ssl-cert-path",
                        str(cert),
                        "--ssl-key-path",
                        str(key),
                        "--log-level",
                        "warn",
                        "--log-no-colors",
                        "--shutdown-timeout",
                        "10000",
                    ],
                    env=env,
                    cwd=self.directory,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            deadline = time.monotonic() + startup_timeout
            while True:
                if self.process.poll() is not None:
                    raise self._startup_error("exited before readiness", env)
                try:
                    status, result = self._backend("GET", "/status", None, timeout=1)
                    if status == 200 and result.get("value", {}).get("ready") is True:
                        break
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                if time.monotonic() >= deadline:
                    raise self._startup_error("did not become ready with its assigned HTTPS identity", env)
                time.sleep(0.1)
            cert.unlink()
            key.unlink()
        except BaseException:
            self.close()
            raise

    @asynccontextmanager
    async def connect(self):
        # Retain the owning loop so synchronous power/reset can revoke streams.
        loop = asyncio.get_running_loop()
        with CancelScope() as scope:
            with self._state_lock:
                self._ensure_alive()
                if len(self._connections) >= 128:
                    raise RuntimeError("Too many active Appium HTTPS connections")
                self._connections[scope] = loop
            try:
                async with https_endpoint(self.identity, partial(self.handle, scope=scope)) as stream:
                    yield stream
            finally:
                with self._state_lock:
                    self._connections.pop(scope, None)

    def metadata(self):
        self._ensure_alive()
        return {
            "ca_certificate": self.identity.certificate,
            "metadata": {"protocol": "webdriver", "device": self.context.device_metadata()},
        }

    def _startup_error(self, problem, env):
        log, node = _log_tail(self.directory / "appium.log"), _node_version(env)
        logger.warning("External Appium %s (Node.js %s); log tail:\n%s", problem, node or "unknown", log or "(empty)")
        hint = "verify its installation"
        if "http_parser" in log:
            # spdy, which serves Appium's HTTPS, needs a binding removed in Node.js 24.
            hint = "its HTTPS server requires Node.js 22"
        return ServiceError(f"External Appium {problem} on Node.js {node or 'unknown'}; {hint}. See the exporter log.")

    def _ensure_alive(self):
        if self._closed or self.process is None or self.process.poll() is not None:
            raise RuntimeError("The lease's Appium process is unavailable; power off/on to start a fresh service")

    def _backend(self, method, path, body, *, timeout=None):
        self._ensure_alive()
        connection = http.client.HTTPSConnection(
            "127.0.0.1", self.port, context=self._context, timeout=timeout or self.request_timeout
        )
        try:
            payload = None if body is None else json.dumps(body, allow_nan=False).encode()
            connection.request(method, path, payload, {"Content-Type": "application/json", "Connection": "close"})
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise RuntimeError("Appium response exceeded the gateway limit")
            return response.status, decode_json(raw)
        finally:
            connection.close()

    def request(self, method, path, body, *, keep=None):
        """Forward one policy-checked request; ``keep`` is the caller's own stream scope."""
        with self._lock:
            self._ensure_alive()
            forwarded = self.policy.request(method, path, body, self._session)
            if method == "GET" and path == "/status":
                return 200, {"value": {"ready": self._session is None, "message": "Lease-scoped native Appium"}}
            creating = method == "POST" and path == "/session"
            deleting = method == "DELETE" and path == f"/session/{self._session}"
            if creating:
                self._assign_ports(forwarded)
            try:
                status, result = self._backend(method, path, forwarded if method == "POST" else None)
            except Exception as exc:
                if not (creating or deleting):
                    raise
                # Session state is uncertain; require an explicit power cycle.
                self._invalidate(f"session request failed: {exc!r}", keep)
                raise ServiceError("The Appium session request failed; power off/on to start a fresh service") from exc
            except BaseException:
                if creating or deleting:
                    self._invalidate("session request interrupted", keep)
                raise
            if creating:
                return self._created(status, result, forwarded, keep)
            if deleting:
                return self._deleted(status, result, keep)
            return _sanitized(status, result)

    def _created(self, status, result, forwarded, keep):
        value = result.get("value")
        if status >= 400:
            # Appium refused outright, so no session exists; the service stays usable.
            message = value.get("message") if isinstance(value, dict) else None
            logger.warning("Appium refused a session (HTTP %s): %s", status, str(message)[:2048])
            self._release_reserved_ports()
            return _error(status, "session not created", "Appium could not create the session; see the exporter log")
        if status != 200:
            return status, result
        if not isinstance(value, dict) or not re.fullmatch(TOKEN, str(value.get("sessionId", ""))):
            self._invalidate("session creation returned an invalid identifier", keep)
            raise ServiceError("Appium returned an invalid session identifier; power off/on to start a fresh service")
        self._session = value["sessionId"]
        self._session_caps = self.policy.public | {
            "appium:bundleId": forwarded["capabilities"]["alwaysMatch"]["appium:bundleId"]
        }
        # Backend capabilities contain private paths and process configuration.
        return status, {"value": {"sessionId": self._session, "capabilities": self._session_caps}}

    def _deleted(self, status, result, keep):
        if status >= 400:
            self._invalidate(f"session deletion returned HTTP {status}", keep)
            raise ServiceError("Appium session deletion failed; power off/on to start a fresh service")
        if status < 300:
            self._session = None
            self._session_caps = None
            self._release_reserved_ports()
        return status, result

    def _invalidate(self, reason, keep):
        logger.warning(
            "Stopping the lease's Appium service: %s; log tail:\n%s", reason, _log_tail(self.directory / "appium.log")
        )
        self.close(keep=keep)

    def _release_reserved_ports(self):
        with self._state_lock:
            for reservation in self._reservations:
                reservation.close()
            self._reservations.clear()

    def _assign_ports(self, forwarded):
        # Fresh ports avoid adopting unrelated listeners after DELETE.
        # WDA's MJPEG server ignores the HTTP binding IP. Non-listening wildcard
        # binds in both families block it; loopback binds allow macOS SO_REUSEADDR.
        # WDA tolerates the failed bind, so HTTP screenshots remain available.
        with self._state_lock:
            self._ensure_alive()
            guards = []
            try:
                wda = _reserve_port()
                guards.append(wda)
                mjpeg = _reserve_port("0.0.0.0")
                guards.append(mjpeg)
                mjpeg_v6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                guards.append(mjpeg_v6)
                mjpeg_v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                mjpeg_v6.bind(("::", mjpeg.getsockname()[1]))
                for name, guard in (("appium:wdaLocalPort", wda), ("appium:mjpegServerPort", mjpeg)):
                    self.policy.fixed[name] = guard.getsockname()[1]
                    forwarded["capabilities"]["alwaysMatch"][name] = guard.getsockname()[1]
                self._reservations.extend([mjpeg, mjpeg_v6])
                wda.close()
            except BaseException:
                for guard in guards:
                    guard.close()
                raise

    @staticmethod
    def _headers(event):
        headers = list(event.headers)
        header_size = len(event.method) + len(event.target) + sum(len(key) + len(value) + 4 for key, value in headers)
        if header_size > 16384:
            raise PolicyError("Request headers exceed the gateway limit")
        if any(name in {b"expect", b"upgrade", b"transfer-encoding"} for name, _ in headers):
            raise PolicyError("Streaming, upgrades and expectations are not supported")
        lengths = [value for name, value in headers if name == b"content-length"]
        if lengths and int(lengths[0]) > MAX_REQUEST:
            raise PolicyError("Request body exceeds the gateway limit")

    @staticmethod
    def _decode_request(request, body):
        method, path = request.method.decode("ascii"), request.target.decode("ascii")
        if not re.fullmatch(r"/[A-Za-z0-9_/-]*", path):
            raise PolicyError("Only literal native endpoint paths are supported")
        document = decode_json(bytes(body)) if body else {}
        if method != "POST" and body:
            raise PolicyError("Only POST requests may contain a body")
        return method, path, document

    async def _read_request(self, stream):
        connection = h11.Connection(h11.SERVER, max_incomplete_event_size=16384)
        request = None
        body = bytearray()
        while True:
            event = connection.next_event()
            if event is h11.NEED_DATA:
                connection.receive_data(await stream.receive(65536))
            elif isinstance(event, h11.Request):
                if request is not None:
                    raise PolicyError("One request per TLS connection is supported")
                request = event
                self._headers(event)
            elif isinstance(event, h11.Data):
                body.extend(event.data)
                if len(body) > MAX_REQUEST:
                    raise PolicyError("Request body exceeds the gateway limit")
            elif isinstance(event, h11.EndOfMessage):
                break
            else:
                raise PolicyError("Incomplete HTTP request")
        if request is None:
            raise PolicyError("Missing HTTP request")
        return self._decode_request(request, body)

    async def handle(self, stream, scope=None):
        status, result = _error(400, "invalid argument", "Invalid HTTP request")
        try:
            with fail_after(15):
                method, path, document = await self._read_request(stream)
            # Queue before entering the shared worker pool so slow requests
            # cannot starve power.off/reset of worker threads.
            with fail_after(5):
                await self._request_gate.acquire()
            try:
                status, result = await to_thread.run_sync(partial(self.request, method, path, document, keep=scope))
            finally:
                self._request_gate.release()
        except (PolicyError, h11.RemoteProtocolError, ValueError, TypeError, UnicodeError, TimeoutError) as exc:
            result["value"]["message"] = str(exc)[:512]
        except ServiceError as exc:
            status, result = _error(502, "unknown error", str(exc)[:512])
        except (OSError, RuntimeError, http.client.HTTPException):
            status, result = _error(502, "unknown error", "Lease Appium service unavailable")
        except EndOfStream:
            return
        payload = json.dumps(result, allow_nan=False).encode()
        # Invalid framing can leave the request's h11 connection in ERROR.
        response = h11.Connection(h11.SERVER)
        await stream.send(
            response.send(
                h11.Response(
                    status_code=status,
                    headers=[
                        ("Content-Type", "application/json; charset=utf-8"),
                        ("Content-Length", str(len(payload))),
                        ("Connection", "close"),
                        ("Cache-Control", "no-store"),
                    ],
                )
            )
        )
        await stream.send(response.send(h11.Data(data=payload)))
        await stream.send(response.send(h11.EndOfMessage()))

    def close(self, *, keep=None):
        """Stop the service and revoke streams, except ``keep``, which ends after its response."""
        with self._close_lock:
            self._close(keep)

    def _close(self, keep=None):
        with self._state_lock:
            self._closed = True
            for scope, loop in tuple(self._connections.items()):
                if scope is keep:
                    continue
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(scope.cancel)
        process = self.process
        if process is not None:
            # Stop only the owned server process group.
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            self.process = None
        self._release_reserved_ports()
        self._session = None
        try:
            info = self.directory.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != self._directory_identity:
            raise RuntimeError("HTTP provider state directory was replaced; refusing to remove it")
        shutil.rmtree(self.directory)
