import json
import os
import subprocess
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import requests
from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface

from .cvdcli import cvd_argv, exec_binary, fleet_to_cvds, group_to_cvds, stderr_tail
from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import FlasherInterface


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


class HostOrchestratorBackend:
    """Drive CVDs through the Host Orchestrator REST API."""

    _paths = {
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

    _subcommands = {
        "start": ["start", "--report_anonymous_usage_stats=n"],
        "stop": ["stop"],
        "restart": ["restart"],
        "powerwash": ["powerwash"],
        "powerbtn": ["powerbtn"],
    }
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
        argv = cvd_argv(self.socket, self.user, args)
        self.driver.logger.debug("running %s", " ".join(argv))
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
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
        except ValueError as e:
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
        output = self._cvd(["load", str(config_path)], timeout)
        try:
            cvds = group_to_cvds(json.loads(output))
        except ValueError as e:
            raise CuttlefishError(f"cvd load returned an unexpected document: {output[:200]!r}") from e
        return {"done": True, "cvds": cvds}


@dataclass(kw_only=True)
class Cuttlefish(Driver):
    """Cuttlefish Host Orchestrator driver for managing Android virtual devices.

    Composite driver with children: power, storage, adb.
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
    managed: bool = False
    # Exec backend: jumpstarter-exec launcher socket shared with the runtime
    # container. When set, every lifecycle action runs ``cvd`` there instead of
    # calling Host Orchestrator. ``cvd_user`` selects the uid whose cvd instance
    # database is used; it must match other cvd callers in the container.
    launcher_socket: str = ""
    cvd_user: str = ""
    health_state_path: str = ""
    runtime_id_path: str = ""
    health_ports: list[int] = field(default_factory=list)
    _operation_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _health: dict = field(default_factory=dict, init=False, repr=False)
    _backend: "HostOrchestratorBackend | CvdCliBackend" = field(init=False, repr=False)
    _cvd_group: str | None = field(default=None, init=False, repr=False)
    _cvd_name: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
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

        self.children["power"] = CvdPower(parent=self)
        self.children["storage"] = CvdFlasher(parent=self)
        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)

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
        group = group or self._cvd_group or self.group
        name = name or self._cvd_name or self.name
        if not self.managed:
            return self._backend.operate(op, group, name, data, timeout)
        with self._operation_lock:
            if op == "create":
                self._validate_creation(data)
            target_state = TARGET_STATE[op] or self._health.get("state", "off")
            # Probes allow bounded transitions, then fail closed if the operation
            # hangs or leaves the target's state unknown.
            self._health.update(state="transition", deadline=time.monotonic() + timeout + 30)
            self._write_health()
            try:
                result = self._backend.operate(op, group, name, data, timeout)
            except Exception:
                self._health["state"] = "failed"
                self._write_health()
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
            adb.connect_device(device)
        except Exception:
            self.logger.warning("ADB connect to %s failed, will retry during boot wait", device)
        return device

    def _auto_disconnect_adb(self):
        adb = self.children.get("adb")
        if not adb:
            return
        device = self._cvd_device
        self.logger.info(f"Disconnecting ADB from {device}")
        try:
            adb.disconnect_device(device)
        except Exception:
            pass

    def _wait_boot(self, timeout: float = 300):
        """Wait for CVD to be ADB-reachable and fully booted."""
        adb = self.children.get("adb")
        if not adb:
            return

        device = self._cvd_device

        deadline = time.monotonic() + timeout
        adb_path = adb.adb_path
        adb_env = adb.adb_env()

        self.logger.info("Waiting for %s to come online", device)
        while time.monotonic() < deadline:
            try:
                subprocess.run(
                    [adb_path, "connect", device],
                    capture_output=True,
                    text=True,
                    timeout=5,
                    env=adb_env,
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
        return self._fmt(self._do_operation("create", config, timeout=600))

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
        failed = []
        for cvd in existing:
            group = cvd.get("group", self.parent.group)
            name = cvd.get("name", self.parent.name)
            try:
                self.parent._do_operation("delete", None, 300, group, name)
            except CuttlefishError:
                self.logger.warning("Failed to delete stale CVD %s/%s", group, name)
                failed.append(f"{group}/{name}")
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
            result = self.parent._do_operation("create", {"env_config": self.parent.env_config}, timeout=600)
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


@dataclass(kw_only=True)
class CvdFlasher(FlasherInterface, Driver):
    """Flasher for Cuttlefish devices (not yet implemented).

    Planned: upload artifacts to Host Orchestrator via its upload API.
    """

    parent: Cuttlefish

    @export
    def flash(self, source, target: str | None = None) -> None:
        raise NotImplementedError("CvdFlasher.flash() not yet implemented")

    @export
    def dump(self, target, partition: str | None = None) -> None:
        raise NotImplementedError("dump not supported for Cuttlefish devices")
