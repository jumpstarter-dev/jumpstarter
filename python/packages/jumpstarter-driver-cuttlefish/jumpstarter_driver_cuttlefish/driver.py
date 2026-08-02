import json
import subprocess
import time
from collections.abc import Generator
from dataclasses import dataclass, field

import requests
from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface

from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import FlasherInterface


class CuttlefishError(Exception):
    """Raised when a Host Orchestrator API call fails."""


class CuttlefishTimeout(CuttlefishError):
    """Raised when an operation doesn't complete in time."""


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
    _cvd_group: str | None = field(default=None, init=False, repr=False)
    _cvd_name: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.children["power"] = CvdPower(parent=self)
        self.children["storage"] = CvdFlasher(parent=self)
        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CuttlefishClient"

    @property
    def _base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def _expected_adb_port(self) -> int:
        return 6520 + (self.instance_num - 1)

    @property
    def _cvd_path(self) -> str:
        return f"/cvds/{self._cvd_group or self.group}/{self._cvd_name or self.name}"

    def _fmt(self, result) -> str:
        return json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)

    def _request(self, method: str, path: str, data: dict | None = None, timeout: float = 10) -> dict | list | str:
        try:
            r = requests.request(method, f"{self._base_url}{path}", json=data, timeout=timeout)
            r.raise_for_status()
            try:
                return r.json()
            except requests.JSONDecodeError:
                return r.text
        except requests.ConnectionError as e:
            raise CuttlefishError(f"not connected to Host Orchestrator at {self.host}:{self.port}") from e
        except requests.Timeout as e:
            raise CuttlefishError(f"{method} {path} timed out after {timeout}s") from e
        except requests.HTTPError as e:
            raise CuttlefishError(f"{method} {path} failed: {e}") from e

    def _wait_for_operation(self, op_name: str, timeout: float = 300) -> dict:
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
                    f"{self._base_url}/operations/{op_name}/:wait",
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

    def _do_operation(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        timeout: float = 300,
    ) -> dict | list | str:
        result = self._request(method, path, data)
        if isinstance(result, dict) and "done" in result:
            op_name = result.get("name")
            if not op_name:
                raise CuttlefishError(f"operation response missing 'name': {result}")
            self.logger.info(f"Waiting for operation {op_name}")
            return self._wait_for_operation(str(op_name), timeout)
        return result

    def _get_existing_cvds(self) -> list[dict]:
        """Return CVDs belonging to this driver's group.

        Raises CuttlefishError on connection/timeout/server failures so callers
        don't mistake a failed query for "no CVDs exist".
        """
        result = self._request("GET", "/cvds")
        if not isinstance(result, dict):
            return []
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
        adb_env = adb._adb_env()

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
        return self._fmt(self._request("GET", "/cvds"))

    @export
    def get_cvd(self) -> str:
        return self._fmt(self._request("GET", self._cvd_path))

    @export
    def restart_cvd(self) -> str:
        self.logger.info(f"Restarting CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("POST", f"{self._cvd_path}/:restart"))

    @export
    def powerwash_cvd(self) -> str:
        self.logger.info(f"Powerwashing CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("POST", f"{self._cvd_path}/:powerwash"))

    @export
    def powerbtn_cvd(self) -> str:
        self.logger.info(f"Power button on CVD {self.group}/{self.name}")
        return self._fmt(self._do_operation("POST", f"{self._cvd_path}/:powerbtn"))

    @export
    def status(self) -> str:
        """Check that nginx and Host Orchestrator are both reachable."""
        self._request("GET", "/_debug/statusz")
        return "OK"

    @export
    def create_cvd(self, config_json: str) -> str:
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError as e:
            raise CuttlefishError(f"invalid JSON: {e}") from e
        return self._fmt(self._do_operation("POST", "/cvds", config, timeout=600))

    @export
    def start_cvd(self) -> str:
        return self._fmt(self._do_operation("POST", f"{self._cvd_path}/:start"))

    @export
    def stop_cvd(self) -> str:
        return self._fmt(self._do_operation("POST", f"{self._cvd_path}/:stop"))

    @export
    def delete_cvd(self) -> str:
        return self._fmt(self._do_operation("DELETE", self._cvd_path))

    @export
    def get_adb_port(self) -> str:
        result = self._request("GET", self._cvd_path)
        if isinstance(result, dict):
            for cvd in result.get("cvds", []):
                port = cvd.get("adb_port")
                if port is not None:
                    return str(port)
        raise CuttlefishError(f"no ADB port found for {self.group}/{self.name}")

    @export
    def list_operations(self) -> str:
        return self._fmt(self._request("GET", "/operations"))

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
        result = self._do_operation("POST", "/reset", timeout=60)
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
    def on(self) -> None:  # noqa: C901
        existing = self.parent._get_existing_cvds()

        if len(existing) > 1:
            self.logger.warning(
                "Found %d stale CVDs in group %s, deleting", len(existing), self.parent._cvd_group or self.parent.group
            )
            failed = []
            for cvd in existing:
                group = cvd.get("group", self.parent.group)
                name = cvd.get("name", self.parent.name)
                try:
                    self.parent._do_operation("DELETE", f"/cvds/{group}/{name}")
                except CuttlefishError:
                    self.logger.warning("Failed to delete stale CVD %s/%s", group, name)
                    failed.append(f"{group}/{name}")
            if failed:
                raise CuttlefishError(
                    f"cannot create CVD - failed to delete stale CVDs: {', '.join(failed)}. "
                    f"Run 'j cuttlefish reset' then retry."
                )
            existing = []

        if existing:
            cvd = existing[0]
            self.parent._cvd_group = cvd.get("group")
            self.parent._cvd_name = cvd.get("name")
            self.logger.info(
                "Found existing CVD %s/%s (status: %s)",
                self.parent._cvd_group,
                self.parent._cvd_name,
                cvd.get("status"),
            )
            if cvd.get("status") != "Running":
                self.parent._do_operation("POST", f"{self.parent._cvd_path}/:start")
        else:
            self.logger.info("Creating CVD from env_config")
            try:
                result = self.parent._do_operation(
                    "POST", "/cvds", {"env_config": self.parent.env_config}, timeout=600,
                )
            except CuttlefishError as e:
                msg = str(e)
                if "in use" in msg or "already running" in msg or "ValidateTapDevices" in msg:
                    raise CuttlefishError(
                        f"CVD creation failed - orphaned processes from a previous session. "
                        f"Run 'j cuttlefish reset' then retry. Original error: {msg}"
                    ) from e
                raise
            if isinstance(result, dict):
                for cvd in result.get("cvds", []):
                    self.parent._cvd_group = cvd.get("group")
                    self.parent._cvd_name = cvd.get("name")
                    actual_port = cvd.get("adb_port")
                    if actual_port and actual_port != self.parent._expected_adb_port:
                        try:
                            self.parent._do_operation("DELETE", self.parent._cvd_path)
                        except CuttlefishError:
                            self.logger.warning("Failed to clean up CVD after port mismatch")
                        self.parent._cvd_group = None
                        self.parent._cvd_name = None
                        raise CuttlefishError(
                            f"HO assigned adb_port {actual_port} but expected "
                            f"{self.parent._expected_adb_port} — stale state may have leaked. "
                            f"Run 'j cuttlefish reset' then retry."
                        )
                    break

        self.parent._auto_connect_adb()
        if self.parent.boot_timeout:
            self.parent._wait_boot(self.parent.boot_timeout)

    @export
    def off(self, destroy: bool = False) -> None:
        p = self.parent
        cvd_id = f"{p._cvd_group or p.group}/{p._cvd_name or p.name}"
        if destroy:
            p._auto_disconnect_adb()
            self.logger.info(f"Deleting CVD {cvd_id}")
            p._do_operation("DELETE", p._cvd_path)
            p._cvd_group = None
            p._cvd_name = None
        else:
            self.logger.info(f"Stopping CVD {cvd_id}")
            p._do_operation("POST", f"{p._cvd_path}/:stop")

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
