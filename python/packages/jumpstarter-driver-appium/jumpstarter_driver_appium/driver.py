import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from anyio import CancelScope, to_thread

from .common import APP_TOKEN_PREFIX, check_app_name
from .gateway import serve_http
from .https import HttpsIdentity, https_endpoint
from .local import _ready as local_ready
from .policy import _HOST_CAPABILITY, AppiumPolicy, PolicyError
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream

MAX_CONNECTIONS = 128
# The `appium:` options a target driver accepts from exporter configuration.
APPIUM_OPTIONS = frozenset({"executable", "home", "capabilities", "env", "startup_timeout", "max_upload_bytes"})


def validate_appium_config(config: Any, extra: frozenset[str] = frozenset()) -> None:
    """Check a target driver's ``appium`` option before building its child."""
    if config is None:
        return
    allowed = APPIUM_OPTIONS | extra
    if not isinstance(config, dict) or set(config) - allowed:
        raise ConfigurationError(f"appium accepts only {', '.join(sorted(allowed))}")


class AppiumInterface(metaclass=ABCMeta):
    """An Appium server for one leased UI-automation target.

    Implementations decide where Appium runs and which capabilities they fix,
    such as the target's udid. Clients only see a WebDriver endpoint.
    """

    driver_type = "appium"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_appium.client.AppiumClient"

    @abstractmethod
    async def info(self) -> dict:
        """Start the server if needed; return its public ``ca_certificate`` and ``capabilities``."""

    @abstractmethod
    async def connect(self):
        """Stream verified HTTPS to the server."""

    @abstractmethod
    async def upload_app(self, source: str, name: str) -> str:
        """Store an uploaded application for this lease and return its token."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the server; the next ``info()`` starts a fresh one."""


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def _log_tail(path: Path, limit: int = 4096) -> str:
    try:
        with path.open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - limit))
            return log.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _ready(port: int) -> bool:
    return local_ready(f"http://127.0.0.1:{port}")


@dataclass
class _Server:
    process: subprocess.Popen
    port: int
    directory: Path
    identity: HttpsIdentity
    target: Any = None


@dataclass(kw_only=True)
class AppiumDriver(AppiumInterface, Driver):
    """Run an operator-installed Appium on the exporter for one lease.

    Appium listens on exporter loopback only and is reached through the lease
    over verified HTTPS. Target drivers subclass it to add the capabilities
    they fix at runtime, or configure ``capabilities`` directly.
    """

    executable: str = "appium"
    """Appium launcher; resolved on the exporter's PATH."""
    home: str | None = None
    """APPIUM_HOME holding the installed Appium drivers."""
    drivers: list[str] = field(default_factory=list)
    """Appium drivers to load, such as ``xcuitest`` or ``uiautomator2``; all installed ones when empty."""
    capabilities: dict[str, Any] = field(default_factory=dict)
    """Capabilities fixed on every session; clients cannot override them."""
    env: dict[str, str] = field(default_factory=dict)
    """Extra environment for the Appium process, such as ANDROID_ADB_SERVER_PORT."""
    state_dir: str | None = None
    """Existing directory for the lease's private state; the system temporary directory by default."""
    startup_timeout: float = 60.0
    max_upload_bytes: int = 4 * 1024**3

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        if not isinstance(self.executable, str) or not self.executable.strip():
            raise ConfigurationError("executable must name the Appium launcher")
        if self.startup_timeout <= 0 or self.max_upload_bytes <= 0:
            raise ConfigurationError("startup_timeout and max_upload_bytes must be positive")
        if self.state_dir is not None and not Path(self.state_dir).is_dir():
            raise ConfigurationError(f"state_dir {self.state_dir} is not an existing directory")
        self._lock = threading.RLock()
        self._server: _Server | None = None
        self._directory: Path | None = None
        self._apps: dict[str, Path] = {}
        self._connections: dict[CancelScope, asyncio.AbstractEventLoop] = {}

    # Hooks for target drivers.

    def fixed_capabilities(self) -> dict[str, Any]:
        """Capabilities set on every session; target drivers add their runtime identity here."""
        return dict(self.capabilities)

    def public_capabilities(self) -> dict[str, Any]:
        """The fixed capabilities shown to clients; host paths and URLs stay on the exporter."""
        return {
            key: value
            for key, value in self.fixed_capabilities().items()
            if not (_HOST_CAPABILITY.search(key.split(":", 1)[-1]) and isinstance(value, str))
        }

    def denied_capabilities(self) -> frozenset[str]:
        """Capability names, without their vendor prefix, that this target rejects."""
        return frozenset()

    def session_capabilities(self) -> dict[str, Any]:
        """Capabilities chosen afresh for each new session, such as reserved ports."""
        return {}

    def server_environment(self) -> dict[str, str]:
        """Extra environment for the Appium process."""
        return dict(self.env)

    def prepare(self) -> None:
        """Check that the target can be automated before Appium starts; raise if not.

        Runs without this driver's lock held. Hooks called under it, such as
        ``state_root()``, must not wait for locks the target holds while
        stopping Appium.
        """

    def target_key(self) -> Any:
        """Identify the device ``prepare()`` found; Appium restarts when it changes."""
        return None

    def state_root(self) -> str | None:
        """Existing directory that holds the lease's private Appium state."""
        return self.state_dir

    # Lease state.

    def _lease_directory(self) -> Path:
        with self._lock:
            if self._directory is None:
                self._directory = Path(tempfile.mkdtemp(prefix="jumpstarter-appium-", dir=self.state_root()))
            return self._directory

    def _resolve_app(self, token: str) -> str:
        with self._lock:
            path = self._apps.get(token)
        if path is None or not path.is_file():
            raise PolicyError(f"Unknown application upload {token}; upload it again in this lease")
        return str(path)

    def _check_target(self) -> Any:
        """Run ``prepare()``; stop Appium if its device is gone or another took its place.

        Fixed capabilities pin new sessions only, so an existing session must not
        outlive the device it was started for.
        """
        # Outside the lock: target drivers may take their own locks here.
        try:
            self.prepare()
        except BaseException:
            self.stop_server()
            raise
        key = self.target_key()
        with self._lock:
            server = self._server
        if server is not None and server.target != key:
            self.logger.warning("The target device changed; stopping the Appium started for the previous one")
            self.stop_server()
        return key

    def _start(self) -> _Server:
        key = self._check_target()
        with self._lock:
            if self._server is not None and self._server.process.poll() is None:
                return self._server
            if self._server is not None:
                self.logger.warning("Appium exited; log tail:\n%s", _log_tail(self._server.directory / "appium.log"))
                self.stop_server()
            executable = shutil.which(self.executable)
            if executable is None:
                raise RuntimeError(f"Appium launcher {self.executable!r} is not installed on the exporter")
            directory = Path(tempfile.mkdtemp(prefix="server-", dir=self._lease_directory()))
            home = directory / "home"
            (home / "Library/Preferences").mkdir(mode=0o700, parents=True)
            config = directory / "config.json"
            server: dict[str, Any] = {
                "use-plugins": [],
                "allow-insecure": [],
                "deny-insecure": ["*:*"],
                "relaxed-security": False,
            }
            if self.drivers:
                server["use-drivers"] = list(self.drivers)
            config.write_text(json.dumps({"server": server}))
            env = {key: value for key, value in os.environ.items() if not key.startswith("APPIUM_")}
            env["HOME"] = str(home)
            if self.home is not None:
                env["APPIUM_HOME"] = str(Path(self.home).expanduser())
            env.update(self.server_environment())
            port = _reserve_port()
            command = [
                executable,
                "server",
                "--config",
                str(config),
                "--address",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warn",
                "--log-no-colors",
                "--shutdown-timeout",
                "10000",
            ]
            with (directory / "appium.log").open("wb") as log:
                process = subprocess.Popen(
                    command,
                    env=env,
                    cwd=directory,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            self._server = _Server(
                process=process, port=port, directory=directory, identity=HttpsIdentity.generate(), target=key
            )
            deadline = time.monotonic() + self.startup_timeout
            while not _ready(port):
                if process.poll() is not None or time.monotonic() >= deadline:
                    problem = "exited before it was ready" if process.poll() is not None else "was not ready in time"
                    self.logger.warning("Appium %s; log tail:\n%s", problem, _log_tail(directory / "appium.log"))
                    self.stop_server()
                    raise RuntimeError(f"The exporter's Appium {problem}; see the exporter log")
                time.sleep(0.1)
            self.logger.info("Appium is ready on 127.0.0.1:%d", port)
            return self._server

    def stop_server(self) -> None:
        """Stop Appium and revoke its streams; safe to call from synchronous target code."""
        with self._lock:
            server, self._server = self._server, None
            for scope, loop in tuple(self._connections.items()):
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(scope.cancel)
        if server is None:
            return
        process = server.process
        if process.poll() is None:
            # Appium starts the session's helpers in its own process group.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        shutil.rmtree(server.directory, ignore_errors=True)

    def release(self) -> None:
        """Stop Appium and remove the lease's uploads and private state."""
        self.stop_server()
        with self._lock:
            directory, self._directory = self._directory, None
            self._apps.clear()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    def reset(self):
        self.release()
        super().reset()

    def close(self):
        self.release()
        super().close()

    # Exports.

    @export
    async def info(self) -> dict:
        server = await to_thread.run_sync(self._start)
        return {"ca_certificate": server.identity.certificate, "capabilities": self.public_capabilities()}

    @exportstream
    @asynccontextmanager
    async def connect(self):
        await to_thread.run_sync(self._check_target)
        with self._lock:
            server = self._server
            if server is None or server.process.poll() is not None:
                raise RuntimeError("Appium is not running; call info() first")
            if len(self._connections) >= MAX_CONNECTIONS:
                raise RuntimeError("Too many active Appium connections")
        policy = AppiumPolicy(
            self.fixed_capabilities(), self._resolve_app, self.session_capabilities, self.denied_capabilities()
        )
        with CancelScope() as scope:
            with self._lock:
                self._connections[scope] = asyncio.get_running_loop()
            try:
                handler = partial(serve_http, backend=("127.0.0.1", server.port), policy=policy)
                async with https_endpoint(server.identity, handler) as stream:
                    yield stream
            finally:
                with self._lock:
                    self._connections.pop(scope, None)

    @export
    async def upload_app(self, source: str, name: str) -> str:
        check_app_name(name)
        directory = Path(tempfile.mkdtemp(prefix="app-", dir=self._lease_directory()))
        path = directory / name
        size = 0
        try:
            async with self.resource(source) as stream:
                with path.open("xb") as output:
                    async for chunk in stream:
                        size += len(chunk)
                        if size > self.max_upload_bytes:
                            raise ValueError(f"Application exceeds the {self.max_upload_bytes} byte upload limit")
                        output.write(chunk)
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        token = f"{APP_TOKEN_PREFIX}{uuid.uuid4().hex}/{name}"
        with self._lock:
            self._apps[token] = path
        self.logger.info("Stored uploaded application %s (%d bytes)", name, size)
        return token

    @export
    async def stop(self) -> None:
        await to_thread.run_sync(self.stop_server)
