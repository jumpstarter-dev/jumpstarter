from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from anyio import connect_unix, to_thread
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_iosdevice.interface import IosDeviceInterface
from jumpstarter_driver_power.common import PowerReading
from jumpstarter_driver_power.driver import VirtualPowerInterface

from .http_service import HttpServiceProvider, SimulatorHttpContext, create_http_service, validate_http_service
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream

logger = logging.getLogger(__name__)

LEASE_PREFIX = "js-ios-"
# Held with flock by the owning exporter for the lease's lifetime; the kernel
# releases it if that process dies, which marks the lease as orphaned.
LEASE_LOCK = "owner.lock"


def _lock_lease(directory: Path) -> int:
    descriptor = os.open(directory / LEASE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _processes_using(directory: Path) -> list[int]:
    """This user's processes whose command line names the unique lease directory."""
    try:
        output = subprocess.run(
            # -ww: never truncate to a terminal width, or the lease path may be cut off.
            ["ps", "-ww", "-axo", "pid=,uid=,command="],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    marker, uid = f"{directory}{os.sep}", str(os.getuid())
    pids = []
    for line in output.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1] == uid and marker in fields[2]:
            pids.append(int(fields[0]))
    return [pid for pid in pids if pid != os.getpid()]


def _stop_processes(pids: list[int], timeout: float = 5.0) -> None:
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            with suppress(ProcessLookupError, PermissionError):
                os.kill(pid, sig)
        deadline = time.monotonic() + timeout
        while pids and time.monotonic() < deadline:
            pids = [pid for pid in pids if _alive(pid)]
            time.sleep(0.1)
        if not pids:
            return


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _version(runtime: str) -> str:
    match = re.fullmatch(r"(?:com\.apple\.CoreSimulator\.SimRuntime\.iOS-|iOS )(\d+(?:[.-]\d+)*)", runtime)
    if match is None:
        raise ConfigurationError("runtime must identify an iOS simulator runtime")
    version = match.group(1).replace("-", ".")
    parts = tuple(map(int, version.split(".")))
    if (parts + (0,))[:2] < (17, 4):
        raise ConfigurationError("iOS 17.4+ required; select a newer simulator runtime")
    return version


def _udid(value: str) -> str:
    try:
        return str(UUID(value.strip())).upper()
    except (ValueError, AttributeError) as exc:
        raise RuntimeError("simctl did not return a valid simulator UDID") from exc


@dataclass(kw_only=True)
class IosSimulator(CompositeInterface, Driver):
    """Manage a local macOS simulator in a private device set.

    Configure ``device_type`` and ``runtime`` to create a simulator, or
    ``golden_device_set`` and ``golden_udid`` to clone a shutdown source.
    Power off preserves lease data; destroy, reset and close remove it.
    Xcode and idb_companion must be installed on the exporter.
    """

    device_type: str | None = None
    runtime: str | None = None
    golden_device_set: str | None = None
    golden_udid: str | None = None
    device_name: str = "Jumpstarter iOS Simulator"
    state_dir: str = "/private/tmp"
    xcrun: str = "xcrun"
    idb_companion: str = "idb_companion"
    command_timeout: float = 30.0
    boot_timeout: float = 180.0
    companion_timeout: float = 30.0
    shutdown_timeout: float = 30.0
    http_service: dict | None = None

    def __post_init__(self):
        super().__post_init__()
        self._validate_config()
        self._lock = threading.RLock()
        self._lease_dir: Path | None = None
        self._lease_lock: int | None = None
        self._lease_identity: tuple[int, int] | None = None
        self._set_identity: tuple[int, int] | None = None
        self._udid: str | None = None
        self._process: subprocess.Popen | None = None
        self._creation_attempted = False
        self._http_service: HttpServiceProvider | None = None
        self.children["ios"] = IosSimulatorDevice(parent=self)
        self.children["power"] = IosSimulatorPower(parent=self)

    def _validate_config(self):
        self._validate_target_config()
        validate_http_service(self.http_service)
        for name in ("device_name", "state_dir", "xcrun", "idb_companion"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                raise ConfigurationError(f"{name} must be a nonempty string")
        for name in (
            "command_timeout",
            "boot_timeout",
            "companion_timeout",
            "shutdown_timeout",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ConfigurationError(f"{name} must be positive and finite")
        if "ios" in self.children or "power" in self.children:
            raise ConfigurationError("ios and power children are reserved by IosSimulator")

    def _validate_target_config(self):
        for name in ("device_type", "runtime", "golden_device_set", "golden_udid"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip() or "\x00" in value):
                raise ConfigurationError(f"{name} must be a nonempty string when configured")
        create = self.device_type is not None or self.runtime is not None
        clone = self.golden_device_set is not None or self.golden_udid is not None
        if create == clone:
            raise ConfigurationError("configure device_type and runtime, or golden_device_set and golden_udid")
        if create:
            if not self.device_type or not self.runtime:
                raise ConfigurationError("device_type and runtime are both required")
            _version(self.runtime)
        else:
            self._validate_golden_config()

    def _validate_golden_config(self):
        if not self.golden_device_set or not self.golden_udid:
            raise ConfigurationError("golden_device_set and golden_udid are both required")
        try:
            self.golden_udid = _udid(self.golden_udid)
        except RuntimeError as exc:
            raise ConfigurationError("golden_udid must be a simulator UUID") from exc
        golden = Path(self.golden_device_set).expanduser().resolve()
        default = Path.home() / "Library/Developer/CoreSimulator/Devices"
        if golden == default.resolve():
            raise ConfigurationError("use a dedicated golden device set, not the user's default simulator set")
        self.golden_device_set = str(golden)

    def _run(self, args: list[str], *, timeout: float | None = None) -> str:
        try:
            result = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.command_timeout if timeout is None else timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not run {args[0]}: {exc}") from exc
        if result.returncode:
            detail = (result.stderr or result.stdout or "no output").strip()[-4096:]
            raise RuntimeError(f"{args[0]} exited {result.returncode}: {detail}")
        return result.stdout

    @staticmethod
    def _identity(path: Path) -> tuple[int, int]:
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("simulator state path is no longer an owned directory")
        return info.st_dev, info.st_ino

    def _owned_set(self) -> Path:
        if self._lease_dir is None:
            raise RuntimeError("simulator has not been created")
        if self._identity(self._lease_dir) != self._lease_identity:
            raise RuntimeError("simulator lease directory was replaced; refusing to access it")
        device_set = self._lease_dir / "devices"
        if self._identity(device_set) != self._set_identity:
            raise RuntimeError("simulator device set was replaced; refusing to access it")
        return device_set

    def _forget_lease(self) -> None:
        if self._lease_lock is not None:
            os.close(self._lease_lock)
            self._lease_lock = None
        self._lease_dir = None
        self._lease_identity = None
        self._set_identity = None
        self._udid = None
        self._creation_attempted = False

    def _simctl(self, *args: str, timeout: float | None = None) -> str:
        return self._run([self.xcrun, "simctl", "--set", str(self._owned_set()), *args], timeout=timeout)

    def _list(self, device_set: Path) -> dict[str, tuple[str, dict]]:
        output = self._run([self.xcrun, "simctl", "--set", str(device_set), "list", "devices", "--json"])
        try:
            groups = json.loads(output)["devices"]
            if not isinstance(groups, dict):
                raise ValueError("devices is not an object")  # noqa: TRY004 - invalid external JSON value
            return {
                _udid(device["udid"]): (runtime, device) for runtime, devices in groups.items() for device in devices
            }
        except (KeyError, TypeError, ValueError, RuntimeError) as exc:
            raise RuntimeError("simctl returned an invalid device list") from exc

    def _device(self) -> tuple[str, dict]:
        found = self._list(self._owned_set()).get(self._udid)
        if found is None:
            raise RuntimeError("the owned simulator is missing from its private device set")
        return found

    def _create(self) -> None:
        golden = None
        if self.golden_device_set is not None:
            golden = Path(self.golden_device_set)
            if not golden.is_dir():
                raise RuntimeError("golden_device_set must be an existing dedicated simulator set")
            source = self._list(golden).get(self.golden_udid)
            if source is None:
                raise RuntimeError("golden_udid is not present in golden_device_set")
            _version(source[0])
            if source[1].get("state") != "Shutdown":
                raise RuntimeError("golden simulator must be Shutdown before cloning; the driver will not stop it")
        root = Path(self.state_dir).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError("state_dir must be an existing writable directory")
        self._sweep_orphans(root)
        self._lease_dir = Path(tempfile.mkdtemp(prefix=LEASE_PREFIX, dir=root))
        try:
            self._lease_identity = self._identity(self._lease_dir)
            self._lease_lock = _lock_lease(self._lease_dir)
            device_set = self._lease_dir / "devices"
            device_set.mkdir(mode=0o700)
            self._set_identity = self._identity(device_set)
        except OSError:
            shutil.rmtree(self._lease_dir)
            self._forget_lease()
            raise
        # macOS sockaddr_un.sun_path is 104 bytes including the trailing NUL.
        if len(os.fsencode(self._socket_path())) >= 104:
            raise RuntimeError("state_dir is too long for a companion Unix socket; use a short path like /private/tmp")
        self._creation_attempted = True
        if golden is None:
            output = self._simctl("create", self.device_name, self.device_type, self.runtime)
        else:
            output = self._run(
                [
                    self.xcrun,
                    "simctl",
                    "--set",
                    str(golden),
                    "clone",
                    self.golden_udid,
                    self.device_name,
                    str(device_set),
                ]
            )
        self._udid = _udid(output)
        self._device()  # Never boot a UDID returned for a different device set.

    def _socket_path(self) -> Path:
        self._owned_set()
        return self._lease_dir / "idb.sock"

    def _companion_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _socket_ready(self) -> bool:
        path = self._socket_path()
        try:
            if not stat.S_ISSOCK(path.lstat().st_mode):
                return False
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.1)
                probe.connect(str(path))
            return True
        except OSError:
            return False

    def _log_tail(self) -> str:
        try:
            with (self._lease_dir / "companion.log").open("rb") as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 4096))
                return log.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return "no companion log available"

    def _start_companion(self) -> None:
        path = self._socket_path()
        path.unlink(missing_ok=True)
        args = [
            self.idb_companion,
            "--udid",
            self._udid,
            "--device-set-path",
            str(self._owned_set()),
            "--grpc-domain-sock",
            str(path),
            "--only",
            "simulator",
            "--log-level",
            "info",
        ]
        with (self._lease_dir / "companion.log").open("wb") as log:
            self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + self.companion_timeout
        while True:
            if not self._companion_alive():
                raise RuntimeError(f"idb_companion exited before becoming ready: {self._log_tail()}")
            if self._socket_ready() and self._companion_alive():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(f"idb_companion did not become ready: {self._log_tail()}")
            time.sleep(min(0.1, remaining))

    def _stop_companion(self) -> None:
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    process.kill()
                process.wait(timeout=self.shutdown_timeout)
        else:
            process.wait(timeout=self.shutdown_timeout)
        self._process = None

    def _on(self) -> None:
        with self._lock:
            creating = self._lease_dir is None
            try:
                if creating:
                    self._create()
                _, device = self._device()
                if device.get("state") == "Booted" and self._companion_alive() and self._socket_ready():
                    return
                self._stop_companion()
                if device.get("state") != "Booted":
                    self._simctl("boot", self._udid, timeout=self.boot_timeout)
                self._simctl("bootstatus", self._udid, "-b", timeout=self.boot_timeout)
                self._start_companion()
            except BaseException:
                # Remove a partially created simulator. A failed restart only
                # stops it, so the lease's data survives for another attempt.
                try:
                    self._off(destroy=creating)
                except Exception as cleanup:  # noqa: BLE001 - preserve original boot failure after cleanup
                    self.logger.warning("Could not clean up failed simulator startup: %s", cleanup)
                raise

    def _off(self, destroy: bool = False) -> None:
        with self._lock:
            if self._http_service is not None:
                self._http_service.close()
                self._http_service = None
            self._stop_companion()
            if self._lease_dir is None:
                return
            self._owned_set()
            # Include devices created before simctl failed to return their UDID.
            devices = self._list(self._owned_set()) if self._creation_attempted else {}
            for udid, (_, device) in devices.items():
                if device.get("state") != "Shutdown":
                    self._simctl("shutdown", udid, timeout=self.shutdown_timeout)
                    remaining = self._list(self._owned_set()).get(udid)
                    if remaining is not None and remaining[1].get("state") != "Shutdown":
                        raise RuntimeError("simulator did not shut down; retaining its owned device set for cleanup")
                if destroy:
                    self._simctl("delete", udid, timeout=self.shutdown_timeout)
            if destroy:
                if self._creation_attempted and self._list(self._owned_set()):
                    raise RuntimeError("simulator deletion did not complete; retaining the device set for cleanup")
                self._owned_set()
                shutil.rmtree(self._lease_dir)
                self._forget_lease()

    def _info(self) -> dict:
        with self._lock:
            result = {
                "present": False,
                "reason": "Simulator is powered off",
                "udid": self._udid,
                "model": self.device_type,
                "os_version": _version(self.runtime) if self.runtime else None,
                "kind": "simulator",
                "protocols": ["idb"],
                "https_services": ["https"] if self.http_service is not None else [],
                "forward_ports": [],
            }
            if self._lease_dir is None:
                return result
            try:
                runtime, device = self._device()
                result.update(
                    model=device.get("deviceTypeIdentifier", self.device_type),
                    os_version=_version(runtime),
                    state=device.get("state"),
                )
                if device.get("state") != "Booted":
                    result["reason"] = f"Simulator is {device.get('state', 'unavailable')}"
                elif not self._companion_alive() or not self._socket_ready():
                    result["reason"] = "idb_companion is not running or its private socket is unavailable"
                else:
                    result.update(present=True, reason=None)
            except (OSError, RuntimeError, ConfigurationError) as exc:
                result["reason"] = str(exc)
            return result

    def _connect_path(self) -> str:
        with self._lock:
            info = self._info()
            if not info["present"]:
                raise RuntimeError(info["reason"])
            return str(self._socket_path())

    def _prepare_http_service(self) -> HttpServiceProvider:
        with self._lock:
            if self.http_service is None:
                raise RuntimeError("An HTTP service is not configured on this simulator exporter")
            info = self._info()
            if not info["present"]:
                raise RuntimeError(info["reason"])
            self._owned_set()
            if self._http_service is None:
                context = SimulatorHttpContext(
                    directory=self._lease_dir,
                    device_set=self._owned_set(),
                    udid=self._udid,
                    platform_version=info["os_version"],
                )
                self._http_service = create_http_service(self.http_service, context)
            self._http_service.metadata()  # Never replace a dead service behind an issued CA.
            return self._http_service

    def _current_http_service(self) -> HttpServiceProvider:
        with self._lock:
            if self._http_service is None:
                raise RuntimeError("Prepare the HTTP service with https_info before connecting")
            self._http_service.metadata()
            return self._http_service

    def _sweep_orphans(self, root: Path) -> None:
        """Clean up leases whose exporter exited without cleanup, e.g. after a crash.

        Only directories this driver created qualify: owned by this user, with
        a lease lock that no live process holds. Live leases are never touched.
        """
        for entry in root.glob(f"{LEASE_PREFIX}*"):
            try:
                info = entry.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                    continue
                descriptor = os.open(entry / LEASE_LOCK, os.O_WRONLY | os.O_NOFOLLOW)
            except OSError:
                continue  # Not ours, or still being created.
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(descriptor)
                continue  # Its owner is alive.
            try:
                self._remove_orphan(entry)
            except (OSError, RuntimeError) as exc:
                logger.warning("Could not clean up orphaned simulator lease %s: %s", entry, exc)
            finally:
                os.close(descriptor)

    def _remove_orphan(self, entry: Path) -> None:
        device_set = entry / "devices"
        owned_set = stat.S_ISDIR(device_set.lstat().st_mode)
        # Shut simulators down gracefully first; then stop whatever still names
        # this lease (companion, Appium), and only then delete.
        if owned_set:
            self._simctl_all(device_set, "shutdown")
        pids = _processes_using(entry)
        logger.warning("Cleaning up orphaned simulator lease %s (stopping processes %s)", entry, pids)
        _stop_processes(pids)
        if owned_set:
            self._simctl_all(device_set, "delete")
        shutil.rmtree(entry)

    def _simctl_all(self, device_set: Path, operation: str) -> None:
        with suppress(RuntimeError):
            self._run([self.xcrun, "simctl", "--set", str(device_set), operation, "all"], timeout=self.shutdown_timeout)

    def reset(self):
        self._off(destroy=True)
        root = Path(self.state_dir).expanduser().resolve()
        if root.is_dir():
            self._sweep_orphans(root)
        super().reset()

    def close(self):
        self._off(destroy=True)
        super().close()


@dataclass(kw_only=True)
class IosSimulatorDevice(IosDeviceInterface, Driver):
    parent: IosSimulator

    @export
    def info(self) -> dict:
        return self.parent._info()

    @export
    def https_info(self) -> dict:
        return self.parent._prepare_http_service().metadata()

    @exportstream
    @asynccontextmanager
    async def connect_https(self):
        service = await to_thread.run_sync(self.parent._current_http_service)
        async with service.connect() as stream:
            yield stream

    @exportstream
    @asynccontextmanager
    async def connect_idb(self):
        path = await to_thread.run_sync(self.parent._connect_path)
        async with await connect_unix(path) as stream:
            yield stream


@dataclass(kw_only=True)
class IosSimulatorPower(VirtualPowerInterface, Driver):
    parent: IosSimulator

    @export
    def on(self) -> None:
        self.parent._on()

    @export
    def off(self, destroy: bool = False) -> None:
        self.parent._off(destroy=destroy)

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        raise NotImplementedError("iOS Simulators do not provide electrical power measurements")
        yield  # pragma: no cover
