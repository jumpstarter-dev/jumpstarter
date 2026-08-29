import copy
import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from functools import partial
from typing import ClassVar

import anyio
import requests
from anyio import to_thread
from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface

from jumpstarter.driver import Driver, export
from jumpstarter.driver.flasher import FlasherInterface

# Upload chunk size for the HO user-artifacts API; matches upstream
# libhoclient's DefaultUploadOptions (16 MB).
UPLOAD_CHUNK_SIZE = 16 * 1024 * 1024

# Prefix for referencing image directories from env_config; substituted
# server-side by the HO (upstream api/v1/messages.go
# EnvConfigImageDirectoriesVar, applied in createcvdaction.go).
ENV_CONFIG_IMAGE_DIRS_VAR = "@image_dirs"


class CuttlefishError(Exception):
    """Raised when a Host Orchestrator API call fails.

    ``status_code`` carries the HTTP status when the failure came from an
    HTTP error response, else ``None`` — callers classify errors by it,
    never by substring-matching the message (which embeds URLs containing
    hex checksums that can accidentally contain digit runs like "404").
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class CuttlefishTimeout(CuttlefishError):
    """Raised when an operation doesn't complete in time."""


@dataclass(kw_only=True)
class Cuttlefish(Driver):
    """Cuttlefish Host Orchestrator driver for managing Android virtual devices.

    Composite driver with children: power, storage, adb, ui, ui-tls.
    """

    driver_type = "composite"

    scheme: str = "http"
    host: str = "localhost"
    port: int = 2080
    group: str = "cvd"
    name: str = "1"
    instance_num: int = 1
    adb_server_port: int = 15037
    operator_port: int = 1080
    operator_tls_port: int = 1443
    boot_timeout: int = 300
    env_config: dict = field(default_factory=dict)
    prewarm: bool = False
    webrtc_url: str = ""
    _cvd_group: str | None = field(default=None, init=False, repr=False)
    _cvd_name: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self.children["power"] = CvdPower(parent=self)
        self.children["storage"] = CvdFlasher(parent=self)
        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)
        # Operator web UI listeners; host=self.host (not hardcoded loopback) so
        # hand-managed remote Host Orchestrator hosts work too. In-Pod
        # deployments still target loopback because provisioner enrichment
        # injects host=127.0.0.1 into the driver config.
        self.children["ui"] = TcpNetwork(host=self.host, port=self.operator_port)
        self.children["ui-tls"] = TcpNetwork(host=self.host, port=self.operator_tls_port)
        # Prewarm boots the pinned env_config as the exporter starts so the
        # pool holds booted devices (JEP-0016 DD-6). Background thread:
        # registration must not block on a minutes-long Android boot; a
        # lease acquired mid-boot waits in wait_boot as usual.
        self._prewarm_thread = None
        if self.prewarm:
            self._prewarm_thread = threading.Thread(target=self._prewarm_boot, daemon=True)
            self._prewarm_thread.start()

    def _prewarm_boot(self):
        try:
            self.children["power"].on()
        except Exception:
            self.logger.exception("prewarm boot failed; device remains lessee-bootable")

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

    def _request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        timeout: float = 10,
        files: dict | None = None,
    ) -> dict | list | str:
        """Perform an HO API request.

        ``data`` is sent as JSON, unless ``files`` is given, in which case a
        multipart form is sent with ``data`` as the plain form fields (the
        shape the HO user-artifacts upload endpoint expects).
        """
        try:
            if files is not None:
                r = requests.request(method, f"{self._base_url}{path}", data=data, files=files, timeout=timeout)
            else:
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
            status = e.response.status_code if e.response is not None else None
            raise CuttlefishError(f"{method} {path} failed: {e}", status_code=status) from e

    @staticmethod
    def _raise_operation_failed(r) -> None:
        """Raise the driver-terminal error for a 500 :wait response."""
        body = None
        try:
            body = r.json()
        except (ValueError, requests.JSONDecodeError):
            pass
        if body and isinstance(body, dict):
            msg = body.get("error", "unknown error")
            details = body.get("details", "")
            raise CuttlefishError(f"operation failed: {msg}\n{details}", status_code=500)
        raise CuttlefishError(f"operation failed with status 500: {r.text}", status_code=500)

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
                self._raise_operation_failed(r)
            try:
                r.raise_for_status()
            except requests.HTTPError as e:
                raise CuttlefishError(f"operation {op_name} failed: {e}", status_code=r.status_code) from e
            try:
                return r.json()
            except requests.JSONDecodeError:
                # Upstream answers :wait for a nil-result operation (e.g.
                # :extract, image-directory updates) with a bare 200 and an
                # EMPTY body (waitOperationHandler -> httpHandler res == nil,
                # controller.go); its own client decodes nothing there either.
                return {}
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
            raise CuttlefishError(f"unexpected response from GET /cvds: {result!r}")
        all_cvds = result.get("cvds", [])
        own_group = self._cvd_group or self.group
        return [c for c in all_cvds if c.get("group") == own_group]

    def _adopt_created_cvd(self, result) -> None:
        """Adopt group/name from a create-CVD result, guarding the ADB port.

        If the HO assigned an adb_port other than the config-pinned
        expectation, stale state may have leaked (e.g. orphaned launchers
        holding the instance's ports): delete the just-created CVD and fail
        fast with an actionable error instead of hanging for boot_timeout
        against the wrong port.
        """
        if not isinstance(result, dict):
            return
        for cvd in result.get("cvds", []):
            self._cvd_group = cvd.get("group")
            self._cvd_name = cvd.get("name")
            actual_port = cvd.get("adb_port")
            if actual_port and actual_port != self._expected_adb_port:
                try:
                    self._do_operation("DELETE", self._cvd_path)
                except CuttlefishError:
                    self.logger.warning("Failed to clean up CVD after port mismatch")
                self._cvd_group = None
                self._cvd_name = None
                raise CuttlefishError(
                    f"HO assigned adb_port {actual_port} but expected "
                    f"{self._expected_adb_port} — stale state may have leaked. "
                    f"Run 'j cuttlefish reset' then retry."
                )
            break

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
        return f"{self.scheme}://{self.host}:{self.operator_port}"

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
                    "POST",
                    "/cvds",
                    {"env_config": self.parent.env_config},
                    timeout=600,
                )
            except CuttlefishError as e:
                msg = str(e)
                if "in use" in msg or "already running" in msg or "ValidateTapDevices" in msg:
                    raise CuttlefishError(
                        f"CVD creation failed - orphaned processes from a previous session. "
                        f"Run 'j cuttlefish reset' then retry. Original error: {msg}"
                    ) from e
                raise
            self.parent._adopt_created_cvd(result)

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
    """Flasher for Cuttlefish devices via the HO user-artifacts machinery.

    ``flash(source, target)`` uploads the image artifact through the
    SHA256-content-addressed user-artifacts API (an artifact the HO already
    holds is not re-uploaded, which is what makes tight rebuild loops fast),
    extracts archives, registers or updates an image directory
    (``/cvd_imgs_dirs``), then recreates the CVD with the pool env_config
    referencing the image directory through upstream's ``@image_dirs``
    substitution and waits for boot (JEP-0016 "Flashing for rapid
    iteration").

    ``target`` selects where the image directory is wired into env_config:
    ``"default_build"`` (the default, ``instances[*].disk.default_build``)
    or ``"host_package"`` (``common.host_package``) — the two attachment
    points upstream's e2e tests use.
    """

    parent: Cuttlefish
    _image_dirs: dict = field(default_factory=dict, init=False, repr=False)

    TARGETS: ClassVar[tuple[str, ...]] = ("default_build", "host_package")

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CvdFlasherClient"

    @export
    async def flash(self, source, target: str | None = None, recreate: bool = True) -> None:
        """Flash an image artifact and (by default) recreate the CVD from it.

        ``recreate=False`` uploads and registers the artifact without
        recreating the CVD, so multiple artifacts can be flashed with a
        single recreation at the end (the client does this for dict
        flashes).
        """
        role = self._resolve_target(target)
        path, checksum = await self._stage_source(source)
        try:
            await to_thread.run_sync(partial(self._flash_staged, path, checksum, role, recreate))
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    @export
    def dump(self, target, partition: str | None = None) -> None:
        raise NotImplementedError("dump not supported for Cuttlefish devices")

    def _resolve_target(self, target: str | None) -> str:
        if target is None:
            return "default_build"
        if target not in self.TARGETS:
            raise CuttlefishError(f"unknown flash target {target!r}; expected one of {', '.join(self.TARGETS)}")
        return target

    async def _stage_source(self, source) -> tuple[str, str]:
        """Stream the source resource to a temp file, returning (path, sha256)."""
        sha = hashlib.sha256()
        tmp = tempfile.NamedTemporaryFile(prefix="cvd-flash-", delete=False)
        tmp.close()
        try:
            async with await anyio.open_file(tmp.name, "wb") as f:
                async with self.resource(source) as res:
                    async for chunk in res:
                        sha.update(chunk)
                        await f.write(chunk)
        except BaseException:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise
        return tmp.name, sha.hexdigest()

    @staticmethod
    def _detect_extension(path: str) -> str:
        """Pick the stored filename extension from the file's magic bytes.

        The HO extracts by filename suffix (upstream userartifacts.go
        extractFile supports only ``.zip`` and ``.tar.gz``); anything else
        is stored as-is and symlinked unextracted into the image dir.
        """
        with open(path, "rb") as f:
            magic = f.read(4)
        if magic.startswith(b"PK\x03\x04"):
            return ".zip"
        if magic.startswith(b"\x1f\x8b"):
            return ".tar.gz"
        return ".img"

    def _flash_staged(self, path: str, checksum: str, role: str, recreate: bool) -> None:
        ext = self._detect_extension(path)
        # The stored filename is CONSTANT for a given content type — never
        # role- or build-dependent. Content addressing is by the checksum
        # directory, and two invariants depend on the name being stable:
        # (a) upstream UpdateImageDirectory only replaces an existing symlink
        # of the SAME name, so a raw-image re-flash into the reused image dir
        # must reuse the name or stale images accumulate; (b) upstream stores
        # upload chunks at WorkDir/{checksum}/{filename} and extract requires
        # the moved artifact dir to hold a single file, so the same content
        # uploaded under different names (partial + complete) would wedge the
        # checksum permanently.
        filename = f"artifact{ext}"
        if self._artifact_exists(checksum):
            self.logger.info("Artifact %s already on HO, skipping upload", checksum)
        else:
            self._upload_artifact(path, checksum, filename)
        if ext in (".zip", ".tar.gz"):
            self._extract_artifact(checksum)
        dir_id = self._ensure_image_dir(role)
        self.logger.info("Updating image directory %s with artifact %s", dir_id, checksum)
        self.parent._do_operation("PUT", f"/cvd_imgs_dirs/{dir_id}", {"user_artifact_checksum": checksum}, timeout=300)
        if recreate:
            self._recreate_cvd()

    def _artifact_exists(self, checksum: str) -> bool:
        try:
            self.parent._request("GET", f"/v1/userartifacts/{checksum}")
        except CuttlefishError as e:
            if e.status_code == 404:
                return False
            raise
        return True

    def _upload_artifact(self, path: str, checksum: str, filename: str) -> None:
        size = os.path.getsize(path)
        self.logger.info("Uploading %s (%d bytes) as artifact %s", filename, size, checksum)
        with open(path, "rb") as f:
            offset = 0
            while True:
                chunk = f.read(UPLOAD_CHUNK_SIZE)
                self.parent._request(
                    "PUT",
                    f"/v1/userartifacts/{checksum}",
                    data={"chunk_offset_bytes": str(offset), "file_size_bytes": str(size)},
                    files={"file": (filename, chunk)},
                    timeout=600,
                )
                offset += len(chunk)
                if offset >= size:
                    break

    def _extract_artifact(self, checksum: str) -> None:
        try:
            self.parent._do_operation("POST", f"/v1/userartifacts/{checksum}/:extract", timeout=300)
        except CuttlefishError as e:
            # The HO answers 409 Conflict when the artifact is already
            # extracted; upstream's own client treats that as success.
            if e.status_code == 409:
                self.logger.info("Artifact %s already extracted", checksum)
                return
            raise

    def _ensure_image_dir(self, role: str) -> str:
        dir_id = self._image_dirs.get(role)
        if dir_id:
            return dir_id
        result = self.parent._do_operation("POST", "/cvd_imgs_dirs", timeout=60)
        if not isinstance(result, dict) or not result.get("id"):
            raise CuttlefishError(f"unexpected response creating image directory: {result!r}")
        dir_id = str(result["id"])
        self._image_dirs[role] = dir_id
        return dir_id

    def _render_env_config(self) -> dict:
        """Pool env_config with flashed image dirs injected, non-destructively."""
        cfg = copy.deepcopy(self.parent.env_config)
        build_dir = self._image_dirs.get("default_build")
        if build_dir:
            instances = cfg.get("instances")
            if not instances:
                instances = [{}]
                cfg["instances"] = instances
            for inst in instances:
                inst.setdefault("disk", {})["default_build"] = f"{ENV_CONFIG_IMAGE_DIRS_VAR}/{build_dir}"
        pkg_dir = self._image_dirs.get("host_package")
        if pkg_dir:
            cfg.setdefault("common", {})["host_package"] = f"{ENV_CONFIG_IMAGE_DIRS_VAR}/{pkg_dir}"
        return cfg

    def _recreate_cvd(self) -> None:
        p = self.parent
        p._auto_disconnect_adb()
        for cvd in p._get_existing_cvds():
            group = cvd.get("group", p.group)
            name = cvd.get("name", p.name)
            self.logger.info("Deleting CVD %s/%s before recreation", group, name)
            p._do_operation("DELETE", f"/cvds/{group}/{name}")
        p._cvd_group = None
        p._cvd_name = None
        self.logger.info("Recreating CVD from flashed image dirs")
        result = p._do_operation("POST", "/cvds", {"env_config": self._render_env_config()}, timeout=600)
        # Same adoption + adb_port guard as CvdPower.on: a port leak after
        # flashing must fail fast with the actionable stale-state error, not
        # hang in the boot wait below against the wrong port.
        p._adopt_created_cvd(result)
        p._auto_connect_adb()
        if p.boot_timeout:
            p._wait_boot(p.boot_timeout)
