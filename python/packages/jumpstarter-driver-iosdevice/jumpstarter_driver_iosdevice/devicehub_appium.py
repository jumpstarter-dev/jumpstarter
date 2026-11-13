"""Appium sessions over Device Hub that behave like those on the exporter's Appium.

Appium on this Mac reaches the device only through CoreDevice: WebDriverAgent
over the tunnel, and ``xcrun devicectl``. It has no usbmux connection, which
its XCUITest driver uses to install applications. So the endpoint Jumpstarter
hands out is a gateway in front of that Appium that applies the exporter's
rules (``AppiumPolicy``) and installs uploaded applications itself:

- ``appium:app`` and ``appium:otherApps`` take tokens from ``upload_app()``;
  the gateway installs them with devicectl before the session starts and asks
  Appium for the installed bundle instead.
- ``mobile: installApp`` and Appium-Python-Client's ``install_app()`` do the
  same during a session, and ``mobile: removeApp`` and ``remove_app()``
  uninstall with devicectl. ``mobile: killApp`` stops the application through
  WebDriverAgent, as ``mobile: terminateApp`` does.
- XCUITest is told to check for installed applications with devicectl, so
  ``appium:bundleId`` works. WebDriverAgent launches and stops applications.

Capabilities that would need usbmux are either ignored, where the simulator
fixes them too (``fullReset``, ``usePreinstalledWDA``, ``mjpegServerPort``), or
rejected. Web views, MJPEG streaming and ``mobile: listApps`` need usbmux too
and are not available.
"""

from __future__ import annotations

import json
import logging
import plistlib
import shutil
import tempfile
import threading
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from xml.parsers.expat import ExpatError

import anyio
from anyio import to_thread
from anyio.abc import SocketAttribute
from jumpstarter_driver_appium.common import APP_TOKEN_PREFIX
from jumpstarter_driver_appium.gateway import serve_http
from jumpstarter_driver_appium.policy import AppiumPolicy, PolicyError, Reply, _route

INSTALL_TIMEOUT = 600
IOS_SUFFIXES = (".app", ".ipa", ".zip")
# XCUITest checks, launches and stops applications on real devices through
# devicectl, which reaches the device through CoreDevice, instead of usbmux.
DEVICECTL_ENVIRONMENT = {"APPIUM_XCUITEST_PREFER_DEVICECTL": "1"}
# Fixed on the simulator too, where the target replaces them; here they would
# make XCUITest reach the device through usbmux.
_IGNORED = frozenset({"fullReset", "usePreinstalledWDA", "mjpegServerPort"})
# Services XCUITest reaches only through usbmux on a real device.
_DENIED = frozenset({"resetLocationService", "customSSLCert"})

logger = logging.getLogger(__name__)


class Applications:
    """Applications offered to one Device Hub target, by token."""

    def __init__(self):
        self._lock = threading.Lock()
        self._paths: dict[str, Path] = {}

    def add(self, path: Path) -> str:
        """Offer a local ``.app`` bundle, ``.ipa`` or zipped ``.app``; return its token."""
        path = Path(path).expanduser().absolute()
        if not path.exists():
            raise ValueError(f"{path} does not exist")
        if path.suffix.lower() not in IOS_SUFFIXES or (path.suffix.lower() == ".app") != path.is_dir():
            raise ValueError(f"{path} is not an iOS application: give an .app bundle, an .ipa or a zipped .app")
        token = f"{APP_TOKEN_PREFIX}{uuid.uuid4().hex}/{path.name}"
        with self._lock:
            self._paths[token] = path
        return token

    def path(self, token: str) -> Path:
        with self._lock:
            path = self._paths.get(token)
        if path is None or not path.exists():
            raise PolicyError(f"Unknown application upload {token}; upload it again for this target")
        return path


def _bundle(directory: Path) -> Path:
    """The one ``.app`` bundle of an unpacked ``.ipa`` (under ``Payload/``) or zip."""
    found = [path for path in {*directory.glob("Payload/*.app"), *directory.glob("*.app")} if path.is_dir()]
    if len(found) != 1:
        raise RuntimeError(f"Expected one .app bundle in the archive, found {len(found)}")
    return found[0]


def bundle_id(app: Path) -> str:
    """The ``CFBundleIdentifier`` of an ``.app`` bundle."""
    try:
        with (app / "Info.plist").open("rb") as source:
            identifier = plistlib.load(source).get("CFBundleIdentifier")
    except (OSError, ValueError, ExpatError, AttributeError) as exc:
        raise RuntimeError(f"Could not read the Info.plist of {app.name}: {exc}") from exc
    if not isinstance(identifier, str) or not identifier:
        raise RuntimeError(f"{app.name} has no CFBundleIdentifier")
    return identifier


class Devicectl:
    """Applications on one device, through ``xcrun devicectl``."""

    def __init__(self, udid: str):
        self.udid = udid

    async def _run(self, *command: str, json_output: bool = False) -> Any:
        arguments = ["xcrun", "devicectl", "device", *command, "--device", self.udid, "--timeout", str(INSTALL_TIMEOUT)]
        try:
            result = await anyio.run_process(
                [*arguments, "--quiet", *(["--json-output", "-"] if json_output else [])], check=False
            )
        except OSError as exc:
            raise RuntimeError(f"Could not run devicectl: {exc}") from exc
        if result.returncode:
            output = (result.stderr + result.stdout).decode(errors="replace").strip()
            raise RuntimeError(f"devicectl {' '.join(command[:2])} failed: {output or result.returncode}")
        if not json_output:
            return None
        try:
            return json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"devicectl {' '.join(command[:2])} returned an unexpected answer") from exc

    @asynccontextmanager
    async def unpacked(self, path: Path) -> AsyncGenerator[Path, None]:
        """Yield the ``.app`` bundle of an ``.app``, ``.ipa`` or zipped ``.app``."""
        if path.is_dir():
            yield path
            return
        directory = tempfile.mkdtemp(prefix="jumpstarter-devicehub-app-")
        try:
            # ditto keeps the modes and symlinks a signed bundle needs; zipfile drops them.
            command = ["ditto", "-x", "-k", str(path), directory]
            try:
                result = await anyio.run_process(command, check=False)
            except OSError as exc:
                raise RuntimeError(f"Could not unpack {path.name}: {exc}") from exc
            if result.returncode:
                output = (result.stderr + result.stdout).decode(errors="replace").strip()
                raise RuntimeError(f"Could not unpack {path.name}: {output or result.returncode}")
            yield _bundle(Path(directory))
        finally:
            with anyio.CancelScope(shield=True):
                await to_thread.run_sync(shutil.rmtree, directory, True)

    async def installed(self, identifier: str) -> bool:
        listed = await self._run("info", "apps", "--include-all-apps", "--bundle-id", identifier, json_output=True)
        try:
            apps = listed["result"]["apps"]
        except (KeyError, TypeError) as exc:
            raise RuntimeError("devicectl info apps returned an unexpected answer") from exc
        return any(isinstance(app, dict) and app.get("bundleIdentifier") == identifier for app in apps)

    async def install(self, app: Path) -> None:
        await self._run("install", "app", str(app))

    async def uninstall(self, identifier: str) -> None:
        await self._run("uninstall", "app", identifier)


def _error(status: int, code: str, message: str) -> Reply:
    return Reply(status, {"value": {"error": code, "message": message[:2048], "stacktrace": ""}})


def _app_command(route: str, document: dict[str, Any]) -> tuple[str, Any] | None:
    """``(command, argument)`` for application commands the gateway answers, else None."""
    parts = route.split("/")
    if len(parts) == 6 and parts[1] == "session" and parts[3:5] == ["appium", "device"]:
        if parts[5] == "install_app":
            return "installapp", document.get("appPath", document.get("app"))
        if parts[5] == "remove_app":
            return "removeapp", document.get("appId", document.get("bundleId"))
    if len(parts) in (4, 5) and parts[1] == "session" and parts[3] == "execute" and parts[4:] in ([], ["sync"]):
        script = document.get("script")
        command = script.strip().lower().removeprefix("mobile:").strip() if isinstance(script, str) else ""
        if command in ("installapp", "removeapp", "killapp"):
            # XCUITest takes the first argument of a list, and none as no options.
            args = document.get("args")
            if isinstance(args, list):
                args = args[0] if args else {}
            if args is None:
                args = {}
            if not isinstance(args, dict):
                raise PolicyError(f"{script} needs an object argument")
            key = "app" if command == "installapp" else "bundleId"
            return command, args.get(key, args.get("appPath") if command == "installapp" else None)
    return None


def _flag(capabilities: dict[str, Any], name: str) -> Any:
    """A boolean capability as Appium reads it, which also accepts "true" and "false"."""
    value = capabilities.get(f"appium:{name}")
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower() == "true"
    return value


class DeviceHubPolicy(AppiumPolicy):
    """The exporter's rules, plus installing uploaded applications with devicectl.

    Tokens pass the exporter's checks unchanged; the gateway installs what they
    name and asks Appium for the installed bundle.
    """

    def __init__(self, fixed: dict[str, Any], applications: Applications, device: Devicectl):
        super().__init__(fixed, self._known, denied=_DENIED)
        self.applications = applications
        self.device = device

    def _known(self, token: str) -> str:
        self.applications.path(token)
        return token

    async def _deploy(
        self, token: Any, *, keep: bool = False, replace: bool = False, check: Callable[[str], None] | None = None
    ) -> str:
        """Install an uploaded application as XCUITest would; return its bundle identifier.

        ``keep`` leaves an installed copy alone (``noReset``); ``replace``
        uninstalls it first, so the application starts with fresh data.
        ``check`` may reject the bundle identifier before anything is installed.
        """
        if not isinstance(token, str) or not token.startswith(APP_TOKEN_PREFIX):
            raise PolicyError("Applications must be uploaded first; pass the token upload_app() returns")
        async with self.device.unpacked(self.applications.path(token)) as app:
            identifier = bundle_id(app)
            if check is not None:
                check(identifier)
            installed = (keep or replace) and await self.device.installed(identifier)
            if installed and keep:
                return identifier
            if installed:
                await self.device.uninstall(identifier)
            await self.device.install(app)
        return identifier

    async def _new_session(self, method: str, path: str, body: bytes) -> bytes:
        # The exporter's checks first, so a rejected request changes nothing on the device.
        checked = json.loads(self.request(method, path, body))
        always = checked["capabilities"]["alwaysMatch"]
        first = checked["capabilities"]["firstMatch"]
        if len(first) == 1:
            # One alternative is the same session as everything in alwaysMatch.
            always.update(first.pop())
            first.append({})
        elif any("appium:app" in choice or "appium:otherApps" in choice for choice in first):
            raise PolicyError(
                "With several firstMatch alternatives, put appium:app and appium:otherApps in alwaysMatch"
            )
        # Appium normally settles on the first alternative.
        merged = {**always, **first[0]}
        keep = _flag(merged, "noReset") is True
        replace = not keep and _flag(merged, "enforceAppInstall") is not False
        if (app := always.pop("appium:app", None)) is not None:
            given = [value for choice in (always, *first) if (value := choice.pop("appium:bundleId", None)) is not None]

            def matches(identifier: str) -> None:
                for value in given:
                    if value != identifier:
                        raise PolicyError(f"appium:bundleId {value!r} is not the bundle of appium:app ({identifier})")

            always["appium:bundleId"] = await self._deploy(app, keep=keep, replace=replace, check=matches)
        # XCUITest installs other applications afresh whatever the session's reset options.
        others = always.pop("appium:otherApps", None)
        for other in others if isinstance(others, list) else [] if others is None else [others]:
            await self._deploy(other, replace=True)
        for capabilities in (always, *first):
            for name in _IGNORED:
                capabilities.pop(f"appium:{name}", None)
        return json.dumps(checked).encode()

    async def _app_command(self, command: str, argument: Any, method: str, path: str, body: bytes) -> bytes | Reply:
        self.request(method, path, body)  # The exporter's checks on the arguments.
        if command == "installapp":
            await self._deploy(argument)
            return Reply(200, {"value": None})
        if not isinstance(argument, str) or not argument:
            raise PolicyError("Name the application by its bundle identifier")
        if command == "removeapp":
            # XCUITest answers false, not an error, when it cannot remove the application.
            try:
                if not await self.device.installed(argument):
                    return Reply(200, {"value": False})
                await self.device.uninstall(argument)
            except RuntimeError as exc:
                logger.warning("Could not remove %s: %s", argument, exc)
                return Reply(200, {"value": False})
            return Reply(200, {"value": True})
        # XCUITest kills applications through usbmux; WebDriverAgent can stop them.
        stop = {"script": "mobile: terminateApp", "args": [{"bundleId": argument}]}
        return self.request(method, path, json.dumps(stop).encode())

    async def handle(self, method: str, path: str, body: bytes) -> bytes | Reply:
        try:
            document = json.loads(body) if method == "POST" and body else None
        except (ValueError, RecursionError):
            document = None  # Rejected by request(), with the exporter's message.
        if not isinstance(document, dict):
            return self.request(method, path, body)
        route = _route(path)
        try:
            if route == "/session":
                return await self._new_session(method, path, body)
            if (found := _app_command(route, document)) is not None:
                return await self._app_command(*found, method, path, body)
        except PolicyError:
            raise
        except Exception as exc:  # noqa: BLE001 - one failed command must not stop the gateway
            logger.warning("Device Hub Appium request failed: %s", exc)
            return _error(500, "unknown error", str(exc))
        return self.request(method, path, body)


@asynccontextmanager
async def gateway(backend: str, policy: AppiumPolicy) -> AsyncGenerator[str, None]:
    """Serve ``policy`` in front of the Appium at ``backend`` on a loopback port; yield its URL."""
    host, port = backend.removeprefix("http://").rsplit(":", 1)
    upstream = (host, int(port))
    async with await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0) as listener:

        async def connected(stream):
            # Each connection fails on its own, as each lease stream does on the exporter.
            async with stream:
                try:
                    await serve_http(stream, backend=upstream, policy=policy)
                except Exception:
                    logger.exception("Device Hub Appium connection failed")

        async def accept(socket_listener):
            # A client that resets before it is accepted fails accept(); keep listening.
            while True:
                try:
                    stream = await socket_listener.accept()
                except (OSError, anyio.BrokenResourceError) as exc:
                    logger.debug("Device Hub Appium accept failed: %s", exc)
                    await anyio.sleep(0.01)
                    continue
                tasks.start_soon(connected, stream)

        bound = listener.extra(SocketAttribute.local_address)
        async with anyio.create_task_group() as tasks:
            for socket_listener in listener.listeners:
                tasks.start_soon(accept, socket_listener)
            try:
                yield f"http://127.0.0.1:{bound[1]}"
            finally:
                tasks.cancel_scope.cancel()
