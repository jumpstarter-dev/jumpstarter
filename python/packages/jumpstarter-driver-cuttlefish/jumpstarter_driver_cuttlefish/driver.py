import contextlib
import json
import os
import re
import subprocess
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from contextlib import aclosing
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import ClassVar

import requests
from anyio import fail_after, to_thread
from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface

from .cvdcli import cvd_argv, exec_binary, fleet_to_cvds, stderr_tail
from .image import Worker, install, space_left, stage
from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import FlasherInterface
from jumpstarter.streams.encoding import AutoDecompressIterator


class CuttlefishError(Exception):
    """Raised when a Host Orchestrator API call fails."""


class CuttlefishTimeout(CuttlefishError):
    """Raised when an operation doesn't complete in time."""


# Lifecycle operations understood by both backends, mapped to the guest state
# the health probe should expect afterwards (None keeps the current state).
# Host Orchestrator exposes each one as a REST action; the cvd CLI as a subcommand.
TARGET_STATE = {
    "create": "running", "start": "running", "restart": "running", "powerwash": "running",
    "stop": "off", "delete": "off", "reset": "off", "powerbtn": None,
}

# env_config properties that load images from somewhere other than
# disk.default_build (cvd's load_config.proto). With any of them set, a flash
# to images_dir would not change what the CVD boots.
IMAGE_OVERRIDES = (
    ("boot", "build"), ("boot", "kernel"), ("boot", "bootloader"), ("boot", "android_efi_loader"), ("disk", "super"),
)


class HostOrchestratorBackend:
    """Drive CVDs through the Host Orchestrator REST API."""

    delete_is_group_scoped = False

    _paths: ClassVar[dict] = {
        "create": ("POST", "/cvds"),
        "start": ("POST", "{cvd}/:start"),
        "stop": ("POST", "{cvd}/:stop"),
        "restart": ("POST", "{cvd}/:restart"),
        "powerwash": ("POST", "{cvd}/:powerwash"),
        "powerbtn": ("POST", "{cvd}/:powerbtn"),
        "delete": ("DELETE", "{cvd}"),
        "reset": ("POST", "/reset"),
    }

    def __init__(self, driver: "Cuttlefish"):
        self.driver = driver
        self.base_url = driver._base_url
        self.logger = driver.logger

    def health_fields(self) -> dict:
        return {"backend": "http", "url": self.base_url}

    def status(self) -> None:
        self.request("GET", "/_debug/statusz")

    def list_cvds(self) -> dict | list | str:
        return self.request("GET", "/cvds")

    def get_cvd(self, group: str, name: str) -> dict | list | str:
        return self.request("GET", f"/cvds/{group}/{name}")

    def list_operations(self) -> dict | list | str:
        return self.request("GET", "/operations")

    def operate(self, op: str, group: str, name: str, data: dict | None, timeout: float) -> dict | list | str:
        method, path = self._paths[op]
        result = self.request(method, path.format(cvd=f"/cvds/{group}/{name}"), data)
        if isinstance(result, dict) and "done" in result:
            op_name = result.get("name")
            if not op_name:
                raise CuttlefishError(f"operation response missing 'name': {result}")
            self.logger.info(f"Waiting for operation {op_name}")
            return self.wait_for_operation(str(op_name), timeout)
        return result

    def request(self, method: str, path: str, data: dict | None = None, timeout: float = 10) -> dict | list | str:
        try:
            r = requests.request(method, f"{self.base_url}{path}", json=data, timeout=timeout)
            r.raise_for_status()
            try:
                return r.json()
            except requests.JSONDecodeError:
                return r.text
        except requests.ConnectionError as e:
            raise CuttlefishError(f"not connected to Host Orchestrator at {self.base_url}") from e
        except requests.Timeout as e:
            raise CuttlefishError(f"{method} {path} timed out after {timeout}s") from e
        except requests.HTTPError as e:
            raise CuttlefishError(f"{method} {path} failed: {e}") from e

    def wait_for_operation(self, op_name: str, timeout: float = 300) -> dict:
        deadline = time.monotonic() + timeout
        start = time.monotonic()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            elapsed = int(time.monotonic() - start)
            self.logger.info("operation %s: waiting (%ds elapsed, %ds remaining)", op_name, elapsed, int(remaining))
            try:
                r = requests.post(
                    f"{self.base_url}/operations/{op_name}/:wait",
                    timeout=min(130, max(1, remaining)),
                )
            except requests.ConnectionError as e:
                raise CuttlefishError(f"lost connection during operation {op_name}") from e
            except requests.Timeout:
                self.logger.info("operation %s: poll timeout after %ds, retrying", op_name, elapsed)
                time.sleep(2)
                continue
            if r.status_code in (503, 504):
                self.logger.info("operation %s: server busy (%d), retrying in 2s", op_name, r.status_code)
                time.sleep(2)
                continue
            if r.status_code == 500:
                body = None
                try:
                    body = r.json()
                except (ValueError, requests.JSONDecodeError):
                    pass
                if body and isinstance(body, dict):
                    msg = body.get("error", "unknown error")
                    details = body.get("details", "")
                    raise CuttlefishError(f"operation failed: {msg}\n{details}")
                raise CuttlefishError(f"operation failed with status 500: {r.text}")
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                raise CuttlefishError(f"operation {op_name} failed: {e}") from e
            return r.json()
        raise CuttlefishTimeout(f"operation {op_name} timed out after {timeout}s")


class CvdCliBackend:
    """Drive CVDs by running ``cvd`` in the runtime container over jumpstarter-exec.

    This is the in-Pod equivalent of Podcvd's ``podman exec ... cvd``: the
    exporter never talks to an HTTP listener, and every action is a synchronous
    ``cvd`` invocation whose exit code is the result. Host Orchestrator is itself
    a thin wrapper over the same subcommands, so payloads and inventory documents
    keep the same shape.
    """

    _subcommands: ClassVar[dict] = {
        "start": ["start", "--report_anonymous_usage_stats=n"],
        "stop": ["stop"],
        "restart": ["restart"],
        "powerwash": ["powerwash"],
        "powerbtn": ["powerbtn"],
    }
    delete_is_group_scoped = True
    fleet_timeout = 30

    def __init__(self, driver: "Cuttlefish"):
        self.driver = driver
        self.socket = driver.launcher_socket
        self.user = driver.cvd_user
        # The shared volume is the only path both containers see; env_config for
        # ``cvd load`` must live there, next to the socket and the exec binary.
        self.work_dir = Path(self.socket).parent

    def health_fields(self) -> dict:
        return {"backend": "exec", "socket": self.socket, "cvd_user": self.user}

    def _cvd(self, args: list[str], timeout: float) -> str:
        argv = cvd_argv(self.socket, args)
        self.driver.logger.debug("running %s", " ".join(argv))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
        except OSError as e:
            raise CuttlefishError(f"cannot run jumpstarter-exec at {exec_binary(self.socket)}: {e}") from e
        except subprocess.TimeoutExpired as e:
            raise CuttlefishTimeout(f"cvd {' '.join(args)} timed out after {timeout}s") from e
        if proc.returncode != 0:
            raise CuttlefishError(
                f"cvd {' '.join(args)} failed with exit code {proc.returncode}: {stderr_tail(proc.stderr)}"
            )
        return proc.stdout

    def fleet(self) -> list[dict]:
        try:
            return fleet_to_cvds(self._cvd(["fleet"], self.fleet_timeout))
        except (ValueError, TypeError) as e:
            raise CuttlefishError(str(e)) from e

    def status(self) -> None:
        self.fleet()

    def list_cvds(self) -> dict:
        return {"cvds": self.fleet()}

    def get_cvd(self, group: str, name: str) -> dict:
        return {"cvds": [cvd for cvd in self.fleet() if cvd["group"] == group and cvd["name"] == name]}

    def list_operations(self) -> dict | list | str:
        raise CuttlefishError("the exec backend runs cvd synchronously and tracks no operations")

    def operate(self, op: str, group: str, name: str, data: dict | None, timeout: float) -> dict:
        if op == "create":
            return self._create(data, timeout)
        if op == "reset":
            self._cvd(["reset", "-y"], timeout)
            return {"done": True}
        if op == "delete":
            self._cvd([f"--group_name={group}", "remove"], timeout)
            return {"done": True}
        self._cvd([f"--group_name={group}", f"--instance_name={name}", *self._subcommands[op]], timeout)
        return {"done": True}

    def _create(self, data: dict | None, timeout: float) -> dict:
        env_config = data.get("env_config") if isinstance(data, dict) else None
        if not isinstance(env_config, dict):
            raise CuttlefishError("create_cvd requires an env_config object")
        config_path = self.work_dir / "env_config.json"
        config_path.write_text(json.dumps(env_config, indent=1))
        config_path.chmod(0o644)  # written by the exporter uid, read by the cvd user
        self._cvd(["load", str(config_path)], timeout)
        return {"done": True, "cvds": self.fleet()}


@dataclass(kw_only=True)
class Cuttlefish(Driver):
    """Cuttlefish Host Orchestrator driver for managing Android virtual devices.

    Composite driver with children: power, storage, adb, and optional webui/turn.
    """

    driver_type = "composite"

    scheme: str = "http"
    host: str = "localhost"
    port: int = 2080
    group: str = "cvd"
    name: str = "1"
    instance_num: int = 1
    adb_server_port: int = 15037
    boot_timeout: int = 300
    env_config: dict = field(default_factory=dict)
    webrtc_url: str = ""
    # WebRTC display over the lease. ``webui_port`` is the in-Pod nginx port
    # serving the client page with a TURN-aware /infra_config, ``turn_port`` the
    # coturn listener relaying media. Both are Pod-local and only reachable
    # through the lease; the ExporterSet provisioner injects them.
    webui_port: int = 0
    turn_port: int = 0
    managed: bool = False
    # Exec backend: jumpstarter-exec launcher socket shared with the runtime
    # container. When set, every lifecycle action runs ``cvd`` there instead of
    # calling Host Orchestrator. The provisioner runs the launcher as
    # ``cvd_user``, whose uid owns the cvd instance database.
    launcher_socket: str = ""
    cvd_user: str = ""
    health_state_path: str = ""
    runtime_id_path: str = ""
    health_ports: list[int] = field(default_factory=list)
    # Flashing: the directory env_config loads the CVD's images from, writable
    # by the exporter. The provisioner injects it with the volume's size limit,
    # which kubelet enforces by evicting the Pod rather than failing a write.
    images_dir: str = ""
    images_volume_limit: int = 0
    # Abort a flash when its source sends nothing for this long; downloads
    # have been seen to stall forever with the connection still open.
    flash_idle_timeout: float = 120
    _operation_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    # The flash in progress, if any. Taken and released under _flash_lock,
    # which guards nothing else and is never held across I/O.
    _flashing: Worker | None = field(default=None, init=False, repr=False)
    _flash_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _images_replaced: bool = field(default=False, init=False, repr=False)
    _health: dict = field(default_factory=dict, init=False, repr=False)
    _backend: "HostOrchestratorBackend | CvdCliBackend" = field(init=False, repr=False)
    _cvd_group: str | None = field(default=None, init=False, repr=False)
    _cvd_name: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if self.launcher_socket and not self.managed:
            raise CuttlefishError("launcher_socket requires managed Cuttlefish")
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._backend = CvdCliBackend(self) if self.launcher_socket else HostOrchestratorBackend(self)
        if self.managed:
            self._validate_managed_config()
            self._health = json.loads(Path(self.health_state_path).read_text())
            if self._health["runtime_id"] != Path(self.runtime_id_path).read_text().strip():
                raise CuttlefishError("Cuttlefish runtime restarted before driver initialization")
            self._health.update(self._backend.health_fields(), ports=self.health_ports)
            self._write_health()
        if self.images_dir:
            self._validate_image_sources()

        self.children["power"] = CvdPower(parent=self)
        self.children["storage"] = CvdFlasher(parent=self)
        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)
        # Forwarding these two is what lets a client see the display without any
        # ingress to the Pod: the UI and the TURN relay both ride the lease.
        if self.webui_port:
            self.children["webui"] = TcpNetwork(host="127.0.0.1", port=self.webui_port)
        if self.turn_port:
            self.children["turn"] = TcpNetwork(host="127.0.0.1", port=self.turn_port)

    def _validate_image_sources(self):
        """Flashing replaces images in images_dir, so every instance must boot from there and only there."""
        for instance in self.env_config.get("instances", []):
            source = instance.get("disk", {}).get("default_build")
            if not source or Path(source) != Path(self.images_dir):
                raise CuttlefishError(
                    f"flashing writes to images_dir {self.images_dir}, but env_config loads images from {source}"
                )
            elsewhere = [".".join(path) for path in IMAGE_OVERRIDES if path[1] in instance.get(path[0], {})]
            if elsewhere:
                raise CuttlefishError(f"flashing writes to images_dir, but env_config sets {', '.join(elsewhere)}")

    def _validate_managed_config(self):
        instances = self.env_config.get("instances", [])
        if len(instances) != 1 or not isinstance(instances[0], dict):
            raise CuttlefishError("managed Cuttlefish requires exactly one instance")
        if not self.health_state_path or not self.runtime_id_path:
            raise CuttlefishError("managed Cuttlefish requires health and runtime ID paths")

    def _write_health(self):
        if not self.managed:
            return
        temporary = Path(self.health_state_path + ".tmp")
        temporary.write_text(json.dumps(self._health))
        os.replace(temporary, self.health_state_path)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CuttlefishClient"

    @property
    def _base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def _expected_adb_port(self) -> int:
        return 6520 + (self.instance_num - 1)

    def _fmt(self, result) -> str:
        return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)

    def _validate_creation(self, data):
        # Accept only the budgeted, provisioner-approved configuration. Alternate
        # HO creation forms and additional groups would bypass the Pod contract.
        if data != {"env_config": self.env_config}:
            raise CuttlefishError("managed create_cvd requires the configured env_config")
        existing = self._backend.list_cvds()
        if not isinstance(existing, dict) or not isinstance(existing.get("cvds"), list):
            raise CuttlefishError("invalid CVD inventory; refusing creation")
        if existing["cvds"]:
            raise CuttlefishError("managed Cuttlefish already has a CVD; destroy it before creating another")

    def _do_operation(
        self,
        op: str,
        data: dict | None = None,
        timeout: float = 300,
        group: str | None = None,
        name: str | None = None,
    ):
        """Run a lifecycle operation on this driver's CVD, or on ``group``/``name`` if given."""
        if op not in TARGET_STATE:
            raise CuttlefishError(f"unknown operation {op!r}")
        if not self.managed:
            self._refuse_while_flashing(op)
            return self._backend.operate(
                op, group or self._cvd_group or self.group, name or self._cvd_name or self.name, data, timeout,
            )
        with self._operation_lock:
            self._refuse_while_flashing(op)
            group = group or self._cvd_group or self.group
            name = name or self._cvd_name or self.name
            if op == "create":
                self._validate_creation(data)
            target_state = TARGET_STATE[op] or self._health.get("state", "off")
            # Probes allow bounded transitions, then fail closed if the operation
            # hangs or leaves the target's state unknown.
            self._health.update(state="transition", deadline=time.monotonic() + timeout + 30)
            self._write_health()
            try:
                result = self._backend.operate(op, group, name, data, timeout)
            except Exception as e:
                recovered = op == "create" and self._remove_failed_boot()
                self._health["state"] = "off" if recovered else "failed"
                self._write_health()
                if recovered:
                    hint = "power on failed after a flash; the CVD was removed, flash again or release the lease"
                    raise CuttlefishError(f"{hint}\n{e}") from e
                raise
            if isinstance(result, dict) and op == "create":
                cvds = result.get("cvds", [])
                if len(cvds) == 1:
                    self._cvd_group = cvds[0].get("group")
                    self._cvd_name = cvds[0].get("name")
            self._health.pop("deadline", None)  # only meaningful while in transition
            self._health.update(
                state=target_state, group=self._cvd_group or self.group, name=self._cvd_name or self.name,
            )
            self._write_health()
            return result

    def _remove_failed_boot(self) -> bool:
        """After a flash, delete the CVD a failed create left; True once the inventory is confirmed empty.

        The health probe fails the Pod after a failed operation, which is right
        when the pool's own build does not boot. After a flash the cause may be
        the lessee's images, and the member is discarded when the lease ends
        anyway, so it stays for another flash, provided the runtime still
        answers and removes the failed CVD; the probe keeps checking that it
        answers. The driver cannot tell a guest that does not boot from a
        runtime that cannot start one. A failed ``cvd load`` leaves its group
        listed as "Boot Failed" under a name the runtime assigned and the driver
        never learned, where it blocks the next create.
        """
        if not self._images_replaced:
            return False
        try:
            return not self._delete_cvds(self._all_cvds()) and self._all_cvds() == []
        except CuttlefishError:
            return False

    def _all_cvds(self) -> list[dict]:
        """The runtime's whole inventory; raises rather than mistake a bad answer for none."""
        listed = self._backend.list_cvds()
        if not isinstance(listed, dict) or not isinstance(listed.get("cvds"), list):
            raise CuttlefishError(f"invalid CVD inventory: {listed!r}")
        return listed["cvds"]

    def _refuse_while_flashing(self, op: str) -> None:
        # A CVD started now would be assembled over images that are about to be replaced.
        if self._flashing and TARGET_STATE[op] == "running":
            raise CuttlefishError(f"cannot {op} the CVD while a flash is in progress; retry when it completes")

    def _start_flash(self) -> Worker:
        """Take the device for a flash; until ``_finish_flash``, nothing may start a CVD."""
        with self._flash_lock:
            if self._flashing:
                raise CuttlefishError("another flash is in progress")
            self._flashing = Worker()
            return self._flashing

    def _finish_flash(self, worker: Worker) -> None:
        """Release the device once every step the flash queued on ``worker`` has run."""

        def release():
            with self._flash_lock:
                if self._flashing is worker:
                    self._flashing = None

        worker.close(release)

    def _destroy_for_flash(self) -> None:
        """Destroy the CVD before its images change.

        ``cvd load`` lays the instance's composite disk and overlay out over the
        base images, so the CVD cannot survive a change to them; destroying it
        also resets userdata.
        """
        with self._operation_lock:
            # Every CVD in a managed Pod boots from this image volume, under
            # whatever group the runtime named it; a restarted exporter has not
            # learned that name. An unmanaged host may hold other tenants' CVDs.
            existing = self._all_cvds() if self.managed else self._get_existing_cvds()
            if existing:
                self.logger.info("Destroying %d CVD(s) before flashing", len(existing))
                self._auto_disconnect_adb()
                failed = self._delete_cvds(existing)
                if failed:
                    raise CuttlefishError(f"cannot flash: failed to destroy {', '.join(failed)}")

    def _delete_cvds(self, cvds: list[dict]) -> list[str]:
        """Delete the given inventory entries and forget the tracked CVD; return those that failed."""
        failed = []
        deleted_groups = set()
        for cvd in cvds:
            group = cvd.get("group", self.group)
            name = cvd.get("name", self.name)
            if self._backend.delete_is_group_scoped:
                if group in deleted_groups:
                    continue
                deleted_groups.add(group)
            try:
                self._do_operation("delete", None, 300, group, name)
            except CuttlefishError:
                self.logger.warning("Failed to delete CVD %s/%s", group, name)
                failed.append(f"{group}/{name}")
        self._cvd_group = None
        self._cvd_name = None
        return failed

    def _get_existing_cvds(self) -> list[dict]:
        """Return CVDs belonging to this driver's group.

        Raises CuttlefishError on connection/timeout/server failures so callers
        don't mistake a failed query for "no CVDs exist".
        """
        result = self._backend.list_cvds()
        if not isinstance(result, dict):
            raise CuttlefishError(f"unexpected response from GET /cvds: {result!r}")
        all_cvds = result.get("cvds", [])
        own_group = self._cvd_group or self.group
        return [c for c in all_cvds if c.get("group") == own_group]

    @property
    def _cvd_device(self) -> str:
        """Pinned ADB address derived from config, never queried from HO."""
        return f"{self.host}:{self._expected_adb_port}"

    def _auto_connect_adb(self) -> str:
        adb = self.children.get("adb")
        if not adb:
            return self._cvd_device
        device = self._cvd_device
        self.logger.info(f"Auto-connecting ADB to {device}")
        try:
            adb.connect_device(device)  # ty: ignore[unresolved-attribute]
        except Exception:  # noqa: BLE001
            self.logger.warning("ADB connect to %s failed, will retry during boot wait", device)
        return device

    def _auto_disconnect_adb(self):
        adb = self.children.get("adb")
        if not adb:
            return
        device = self._cvd_device
        self.logger.info(f"Disconnecting ADB from {device}")
        with contextlib.suppress(Exception):
            adb.disconnect_device(device)  # ty: ignore[unresolved-attribute]

    def _wait_boot(self, timeout: float = 300):
        """Wait for CVD to be ADB-reachable and fully booted."""
        adb = self.children.get("adb")
        if not adb:
            return

        device = self._cvd_device

        deadline = time.monotonic() + timeout
        adb_path = adb.adb_path  # ty: ignore[unresolved-attribute]
        adb_env = adb.adb_env()  # ty: ignore[unresolved-attribute]

        self.logger.info("Waiting for %s to come online", device)
        while time.monotonic() < deadline:
            try:
                subprocess.run(
                    [adb_path, "connect", device],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=adb_env,
                    check=False,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass
            try:
                r = subprocess.run(
                    [adb_path, "devices"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=adb_env,
                    check=False,
                )
                for line in r.stdout.splitlines():
                    if device in line and "\tdevice" in line:
                        self.logger.info("%s is online", device)
                        break
                else:
                    time.sleep(3)
                    continue
                break
            except (subprocess.TimeoutExpired, OSError):
                time.sleep(3)
        else:
            raise CuttlefishTimeout(f"{device} did not come online within {timeout}s")

        self.logger.info("Waiting for boot to complete on %s", device)
        while time.monotonic() < deadline:
            try:
                r = subprocess.run(
                    [adb_path, "-s", device, "shell", "getprop", "sys.boot_completed"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    env=adb_env,
                    check=False,
                )
                if r.stdout.strip() == "1":
                    self.logger.info("Boot completed on %s", device)
                    return
            except (subprocess.TimeoutExpired, OSError):
                pass
            time.sleep(5)

        raise CuttlefishTimeout(f"boot did not complete on {device} within {timeout}s")

    @export
    def get_host(self) -> str:
        return self.host

    @export
    def get_webrtc_url(self) -> str:
        if self.webrtc_url:
            return self.webrtc_url
        return f"{self.scheme}://{self.host}:1080"

    def _webrtc_device_id(self) -> str:
        """Device id the operator registered the CVD under.

        Read from inventory: Host Orchestrator derives it from the
        group it actually assigned, which is not always the configured one.
        """
        group, name = self._cvd_group or self.group, self._cvd_name or self.name
        inventory = self._backend.get_cvd(group, name)
        cvds = inventory.get("cvds") if isinstance(inventory, dict) else None
        if isinstance(cvds, list):
            for cvd in cvds:
                device_id = cvd.get("webrtc_device_id") if isinstance(cvd, dict) else None
                if isinstance(device_id, str) and device_id:
                    return device_id
        raise CuttlefishError(f"no WebRTC device ID in inventory for {group}/{name}; power on the CVD first")

    @export
    def get_webrtc_config(self) -> str:
        """Ports a client forwards to reach the display, plus the device id.

        ``turn_port`` is both the Pod-side listener and the local port the client
        must bind: the ICE server URL baked into /infra_config names that exact
        port, and the browser has no way to learn a different one.
        """
        return json.dumps(
            {
                "webui_port": self.webui_port,
                "turn_port": self.turn_port,
                "device_id": self._webrtc_device_id(),
            }
        )

    @export
    def list_cvds(self) -> str:
        return self._fmt(self._backend.list_cvds())

    @export
    def get_cvd(self) -> str:
        return self._fmt(self._backend.get_cvd(self._cvd_group or self.group, self._cvd_name or self.name))

    @export
    def restart_cvd(self) -> str:
        self.logger.info(f"Restarting CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("restart"))

    @export
    def powerwash_cvd(self) -> str:
        self.logger.info(f"Powerwashing CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("powerwash"))

    @export
    def powerbtn_cvd(self) -> str:
        self.logger.info(f"Power button on CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("powerbtn"))

    @export
    def status(self) -> str:
        """Check that the runtime backend (Host Orchestrator or cvd over jumpstarter-exec) answers."""
        self._backend.status()
        return "OK"

    @export
    def create_cvd(self, config_json: str) -> str:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            raise CuttlefishError(f"invalid JSON: {e}") from e
        return self._fmt(self._do_operation("create", config, timeout=max(600, self.boot_timeout)))

    @export
    def start_cvd(self) -> str:
        return self._fmt(self._do_operation("start", {}))

    @export
    def stop_cvd(self) -> str:
        return self._fmt(self._do_operation("stop"))

    @export
    def delete_cvd(self) -> str:
        return self._fmt(self._do_operation("delete"))

    @export
    def get_adb_port(self) -> str:
        result = self._backend.get_cvd(self._cvd_group or self.group, self._cvd_name or self.name)
        if isinstance(result, dict):
            for cvd in result.get("cvds", []):
                port = cvd.get("adb_port")
                if port is not None:
                    return str(port)
        raise CuttlefishError(f"no ADB port found for {self.group}/{self.name}")

    @export
    def list_operations(self) -> str:
        return self._fmt(self._backend.list_operations())

    @export
    def wait_boot(self, timeout: int = 0) -> str:
        """Wait for CVD to finish booting. Uses boot_timeout config if timeout=0."""
        t = timeout or self.boot_timeout
        if t:
            self._wait_boot(t)
        return "OK"

    @export
    def reset_host(self) -> str:
        """Forcefully delete all CVDs and clean host state via HO reset endpoint.

        Kills orphaned processes, removes stale files, and resets HO tracking.
        """
        self.logger.warning("Resetting host orchestrator")
        self._auto_disconnect_adb()
        result = self._do_operation("reset", timeout=60)
        self._cvd_group = None
        self._cvd_name = None
        return self._fmt(result)


@dataclass(kw_only=True)
class CvdPower(VirtualPowerInterface, Driver):
    """Virtual power control for Cuttlefish devices.

    on() creates a CVD if none exists, or starts an existing one.
    If multiple CVDs exist in the configured group, all are deleted before
    creating a fresh one (assumes single-tenant host orchestrator).
    off() stops the CVD; off(destroy=True) deletes it entirely.
    """

    parent: Cuttlefish

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CvdPowerClient"

    @export
    def on(self) -> None:
        with self.parent._operation_lock:
            self._on()

    def _on(self) -> None:
        existing = self.parent._get_existing_cvds()
        if len(existing) > 1:
            if self.parent.managed:
                raise CuttlefishError("managed Cuttlefish inventory has multiple CVDs")
            self._delete_stale(existing)
            existing = []
        if existing:
            self._start_existing(existing[0])
        else:
            self._create()
        if self.parent.managed:
            with self.parent._operation_lock:
                self.parent._health.update(state="running", group=self.parent._cvd_group or self.parent.group,
                                           name=self.parent._cvd_name or self.parent.name)
                self.parent._write_health()
        self.parent._auto_connect_adb()
        if self.parent.boot_timeout:
            self.parent._wait_boot(self.parent.boot_timeout)

    def _delete_stale(self, existing: list[dict]) -> None:
        """Standalone hosts can accumulate CVDs in our group; clear them before creating."""
        self.logger.warning(
            "Found %d stale CVDs in group %s, deleting", len(existing), self.parent._cvd_group or self.parent.group
        )
        failed = self.parent._delete_cvds(existing)
        if failed:
            raise CuttlefishError(
                f"cannot create CVD - failed to delete stale CVDs: {', '.join(failed)}. "
                f"Run 'j cuttlefish reset' then retry."
            )

    def _start_existing(self, cvd: dict) -> None:
        self.parent._cvd_group = cvd.get("group")
        self.parent._cvd_name = cvd.get("name")
        self.logger.info(
            "Found existing CVD %s/%s (status: %s)", self.parent._cvd_group, self.parent._cvd_name, cvd.get("status"),
        )
        if cvd.get("status") != "Running":
            self.parent.start_cvd()

    def _create(self) -> None:
        self.logger.info("Creating CVD from env_config")
        try:
            # cvd load waits for Android boot as well as creating the instance.
            result = self.parent._do_operation(
                "create", {"env_config": self.parent.env_config}, timeout=max(600, self.parent.boot_timeout),
            )
        except CuttlefishError as e:
            msg = str(e)
            if "in use" in msg or "already running" in msg or "ValidateTapDevices" in msg:
                raise CuttlefishError(
                    f"CVD creation failed - orphaned processes from a previous session. "
                    f"Run 'j cuttlefish reset' then retry. Original error: {msg}"
                ) from e
            raise
        cvds = result.get("cvds", []) if isinstance(result, dict) else []
        if not cvds:
            return
        cvd = cvds[0]
        self.parent._cvd_group = cvd.get("group")
        self.parent._cvd_name = cvd.get("name")
        actual_port = cvd.get("adb_port")
        if actual_port and actual_port != self.parent._expected_adb_port:
            try:
                self.parent._do_operation("delete")
            except CuttlefishError:
                self.logger.warning("Failed to clean up CVD after port mismatch")
            self.parent._cvd_group = None
            self.parent._cvd_name = None
            raise CuttlefishError(
                f"runtime assigned adb_port {actual_port} but expected "
                f"{self.parent._expected_adb_port} — stale state may have leaked. "
                f"Run 'j cuttlefish reset' then retry."
            )

    @export
    def off(self, destroy: bool = False) -> None:
        with self.parent._operation_lock:
            self._off(destroy)

    def _off(self, destroy: bool) -> None:
        p = self.parent
        cvd_id = f"{p._cvd_group or p.group}/{p._cvd_name or p.name}"
        if destroy:
            p._auto_disconnect_adb()
            self.logger.info(f"Deleting CVD {cvd_id}")
            p._do_operation("delete")
            p._cvd_group = None
            p._cvd_name = None
        else:
            self.logger.info(f"Stopping CVD {cvd_id}")
            p._do_operation("stop")

    @export
    def read(self) -> Generator[PowerReading, None, None]:
        raise NotImplementedError("no power telemetry for virtual devices")


PARTITION_NAME = re.compile(r"[a-z0-9_]+")
# Headroom below the image volume's size limit. Kubelet measures usage
# periodically and evicts the whole Pod when it is exceeded; it also covers
# the at most one chunk a flash writes before noticing it is over budget, and
# what cvd load writes there itself (vbmeta padding, host tool links).
IMAGES_VOLUME_MARGIN = 256 * 1024 * 1024


@dataclass(kw_only=True)
class CvdFlasher(FlasherInterface, Driver):
    """Replace partition images the CVD boots from.

    A flash stages the new image next to ``<target>.img`` in ``images_dir``
    while the CVD keeps running, then destroys the CVD, renames the image into
    place and leaves the device off. The next power on creates a fresh CVD from
    the new images with the unchanged env_config, so userdata starts empty.
    """

    parent: Cuttlefish

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CvdFlasherClient"

    @export
    def partitions(self) -> list[str]:
        """Partitions this build has, i.e. the valid flash targets."""
        return sorted(path.stem for path in self._images_dir().glob("*.img"))

    @export
    async def flash(self, source, target: str | None = None) -> None:
        """Flash ``source`` (a stream or URL; gzip, xz, bz2 and zstd are detected) to partition ``target``."""
        image = await to_thread.run_sync(self._image_path, target)
        staged = image.with_name(image.name + ".partial")
        # Every blocking step runs on the flash's own worker, and the flash ends
        # with a step queued behind them all, so it cannot end while one of its
        # steps still runs, however the caller goes away.
        worker = self.parent._start_flash()
        try:
            limit = self.parent.images_volume_limit
            budget = await worker.run(space_left, image.parent, limit, IMAGES_VOLUME_MARGIN) if limit else None
            async with self.resource(source) as res, aclosing(self._chunks(res)) as chunks:
                await stage(chunks, staged, budget, worker)
            # Only a complete image costs the running CVD.
            try:
                await worker.run(self.parent._destroy_for_flash)
                await worker.run(self._install, staged, image)
            except BaseException:
                worker.submit(partial(staged.unlink, missing_ok=True))
                raise
        finally:
            self.parent._finish_flash(worker)
            # Return, even cancelled, only with the device free again.
            await worker.drained()
        self.logger.info("Flashed %s; power on to boot it", image.name)

    @export
    def dump(self, target, partition: str | None = None) -> None:
        raise NotImplementedError("dump not supported for Cuttlefish devices")

    def _images_dir(self) -> Path:
        if not self.parent.images_dir:
            raise CuttlefishError("flashing needs images_dir, the directory the CVD's images are loaded from")
        return Path(self.parent.images_dir)

    def _image_path(self, target: str | None) -> Path:
        images = self._images_dir()
        if target and PARTITION_NAME.fullmatch(target) and (images / f"{target}.img").is_file():
            return images / f"{target}.img"
        raise CuttlefishError(f"unknown partition {target!r}; this build has: {', '.join(self.partitions())}")

    def _install(self, staged: Path, image: Path) -> None:
        install(staged, image)
        # Recorded by the step that replaced the image, not by a caller that may
        # have stopped waiting for it.
        self.parent._images_replaced = True

    async def _chunks(self, res) -> AsyncGenerator[bytes, None]:
        """The decompressed image, abandoning a source that goes quiet."""
        timeout = self.parent.flash_idle_timeout
        chunks: AsyncIterator[bytes] = AutoDecompressIterator(source=res)
        while True:
            try:
                with fail_after(timeout):
                    chunk = await anext(chunks)
            except StopAsyncIteration:
                return
            except TimeoutError as e:
                raise CuttlefishTimeout(f"no image data received for {timeout}s; flash abandoned") from e
            yield chunk
