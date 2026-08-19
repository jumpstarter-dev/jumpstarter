import fnmatch
import json
import subprocess
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Generator
from dataclasses import dataclass, field
from pathlib import Path

import requests
from anyio import to_thread
from anyio.streams.file import FileWriteStream
from jumpstarter_driver_adb.driver import AdbServer
from jumpstarter_driver_power.driver import PowerReading, VirtualPowerInterface
from oras.provider import Registry

from jumpstarter.common.oci import OciCredentials, resolve_oci_credentials
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
    artifacts_dir: str = ""
    # Plain HTTP for the registry `flash oci://...` pulls from. Private-CA
    # trust needs no config here: the pull is a requests session, so the
    # exporter's REQUESTS_CA_BUNDLE / CURL_CA_BUNDLE is honored as-is.
    oci_insecure: bool = False
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


ZIP_MAGIC = b"PK\x03\x04"
GZIP_MAGIC = b"\x1f\x8b"

OCI_SCHEME = "oci://"
FLASH_TARGETS = ("image", "host_package")

# Name patterns used ONLY to break a tie when a bundle carries more than one
# archive of a kind (an AAOS bundle often ships otatools.zip beside the image
# zip). `*-img*.zip` deliberately matches both spellings: a local `cvd fetch`
# build writes `<product>-img.zip`, a published build `<product>-img-<n>.zip`.
ARTIFACT_GLOBS = {
    "image": "*-img*.zip",
    "host_package": "cvd-host_package.tar.gz",
}


def _sniff_kind(path: Path) -> str | None:
    """Classify a CVD artifact by magic bytes: zip = image set, gzip = host package."""
    with path.open("rb") as f:
        magic = f.read(4)
    if magic.startswith(ZIP_MAGIC):
        return "image"
    if magic.startswith(GZIP_MAGIC):
        return "host_package"
    return None


@dataclass(kw_only=True)
class CvdFlasher(FlasherInterface, Driver):
    """Flasher for Cuttlefish devices.

    "Flashing" a CVD is staging its build artifacts: the image zip
    (``<product>-img[-<build>].zip``) and the host package
    (``cvd-host_package.tar.gz``) are extracted into the directory named by
    the parent driver's ``artifacts_dir`` config — the directory its
    ``env_config`` should reference as the build source. The next
    ``power.on()`` creates the CVD from whatever is staged there; flashing
    does not itself stop or restart a running device.

    Sources may be local files (streamed from the client), HTTP(S) URLs
    (downloaded by the exporter) or an ``oci://`` reference to a published
    CVD bundle (pulled by the exporter). The archive kind is detected from its
    magic bytes — zip means image set, gzip means host package tar — and an
    explicit target ("image" or "host_package") only validates that detection.
    A first boot needs both archives::

        j storage flash -t image:aosp_cf_x86_64_auto-img-1234.zip \\
                        -t host_package:cvd-host_package.tar.gz

    A bundle carries both, so one reference is a whole flash::

        j storage flash oci://quay.io/org/aaos-cvd:1234
    """

    parent: Cuttlefish

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_cuttlefish.client.CvdFlasherClient"

    @export
    async def flash(self, source, target: str | None = None) -> None:
        # oci:// is a source SCHEME, not a resource handle: the bundle is
        # pulled exporter-side and its members handed to the same staging
        # path a streamed file takes. CvdFlasherClient routes oci:// straight
        # to flash_oci, so this branch is for programmatic callers.
        if isinstance(source, str) and source.startswith(OCI_SCHEME):
            await self.flash_oci(source, target)
            return
        self._validate_target(target)
        root = self._artifacts_root()
        # Stage next to the destination so the temp file shares its
        # filesystem (and its free-space budget) with the extraction.
        with tempfile.NamedTemporaryFile(dir=root, prefix=".cvd-flash-", delete=False) as tmp:
            staged = Path(tmp.name)
        try:
            async with await FileWriteStream.from_path(staged) as stream:
                async with self.resource(source) as res:
                    async for chunk in res:
                        await stream.send(chunk)
            await to_thread.run_sync(self._extract, staged, root, target)
        finally:
            staged.unlink(missing_ok=True)

    @export
    async def flash_oci(
        self,
        oci_url: str,
        target: str | None = None,
        oci_username: str | None = None,
        oci_password: str | None = None,
    ) -> list[str]:
        """Stage a published CVD bundle, pulled exporter-side.

        A CVD bundle is ONE OCI artifact carrying BOTH build artifacts as
        layers — the image zip and ``cvd-host_package.tar.gz``. So the whole
        bundle is pulled and both artifacts are located inside it, rather than
        the caller naming a layer per target: a published build is already a
        complete flash, and asking for its two halves by name would only be a
        way to get one of them wrong::

            j storage flash oci://quay.io/org/aaos-cvd:1234

        The two-target form keeps the meaning it has for loose files — naming
        one target narrows the bundle to that artifact. ``-t image:oci://…``
        stages only the image zip and leaves the staged host package alone,
        which is the fast path when only the build changed.

        Artifacts are picked out of the bundle by magic bytes, never by
        globbing its file names: a local ``cvd fetch`` build writes
        ``<product>-img.zip`` while a published one writes
        ``<product>-img-<build>.zip``, and a glob that knows only one of those
        silently stages nothing. Names are consulted only to break a tie when
        a bundle carries several archives of a kind.

        A bundle carrying only one of the two is staged and warned about, not
        refused: re-staging just the image over an already-staged host package
        is the normal inner loop. It is only a FIRST boot that needs both, and
        this driver cannot tell a first boot from a re-flash — the warning
        names what is missing so a bare artifacts_dir fails loudly at
        ``power.on()`` rather than mysteriously.

        Args:
            oci_url: bundle reference (must start with ``oci://``)
            target: stage only this kind ("image" or "host_package")
            oci_username: registry username for OCI authentication
            oci_password: registry password for OCI authentication

        Returns:
            The kinds staged, e.g. ``["image", "host_package"]``.
        """
        if not oci_url.startswith(OCI_SCHEME):
            raise CuttlefishError(f"OCI URL must start with oci://, got: {oci_url}")
        self._validate_target(target)
        root = self._artifacts_root()
        creds = resolve_oci_credentials(oci_url, username=oci_username, password=oci_password)

        # Pull inside artifacts_dir, for the same reason the streamed path
        # stages its temp file there: one filesystem, one free-space budget,
        # and the pulled copy is gone before flash returns.
        with tempfile.TemporaryDirectory(dir=root, prefix=".cvd-oci-") as tmp:
            pulled = Path(tmp)
            await to_thread.run_sync(self._pull_bundle, oci_url, creds, pulled)
            found = await to_thread.run_sync(self._select_bundle_artifacts, pulled)
            wanted = FLASH_TARGETS if target is None else (target,)
            staged = [kind for kind in wanted if kind in found]
            if not staged:
                raise CuttlefishError(
                    f"{oci_url} carries no {' or '.join(wanted)} artifact "
                    f"(found: {', '.join(sorted(p.name for p in found.values())) or 'nothing recognizable'})"
                )
            for kind in staged:
                await to_thread.run_sync(self._extract, found[kind], root, kind)

        missing = [kind for kind in wanted if kind not in found]
        if missing:
            self.logger.warning(
                "%s carried no %s — a first boot needs both the image zip and the host package",
                oci_url,
                " or ".join(missing),
            )
        return staged

    @export
    def dump(self, target, partition: str | None = None) -> None:
        raise NotImplementedError("dump not supported for Cuttlefish devices")

    def _validate_target(self, target: str | None) -> None:
        if target not in (None, *FLASH_TARGETS):
            raise CuttlefishError(f"unknown flash target {target!r} (expected 'image' or 'host_package')")

    def _pull_bundle(self, oci_url: str, creds: OciCredentials, outdir: Path) -> None:
        reference = oci_url[len(OCI_SCHEME) :]
        registry = Registry(insecure=self.parent.oci_insecure)
        if creds.is_authenticated:
            # set_basic_auth, not login(): login() drives a docker client and
            # writes the host's auth file, neither of which an exporter has.
            registry.auth.set_basic_auth(creds.username, creds.plain_password)
        self.logger.info("Pulling CVD bundle %s", reference)
        registry.pull(target=reference, outdir=str(outdir))

    def _select_bundle_artifacts(self, pulled: Path) -> dict[str, Path]:
        """Find the image zip and the host package in a pulled bundle.

        The tree is walked rather than reading pull()'s return value: a
        directory-media-type layer is unpacked in place, so what lands on disk
        is not always what was named in the manifest.
        """
        candidates: dict[str, list[Path]] = {kind: [] for kind in FLASH_TARGETS}
        for path in sorted(p for p in pulled.rglob("*") if p.is_file() and not p.is_symlink()):
            kind = _sniff_kind(path)
            if kind is not None:
                candidates[kind].append(path)

        found: dict[str, Path] = {}
        for kind, paths in candidates.items():
            if len(paths) == 1:
                found[kind] = paths[0]
            elif len(paths) > 1:
                preferred = [p for p in paths if fnmatch.fnmatch(p.name, ARTIFACT_GLOBS[kind])]
                if len(preferred) != 1:
                    raise CuttlefishError(
                        f"bundle carries {len(paths)} candidate {kind} archives "
                        f"({', '.join(p.name for p in paths)}) and none is unambiguously "
                        f"{ARTIFACT_GLOBS[kind]} — flash the one you want as a file instead"
                    )
                found[kind] = preferred[0]
        return found

    def _artifacts_root(self) -> Path:
        configured = self.parent.artifacts_dir
        if not configured:
            raise CuttlefishError(
                "artifacts_dir is not configured on the cuttlefish driver — "
                "set it to the directory env_config reads the build artifacts from"
            )
        root = Path(configured)
        if not root.is_dir():
            raise CuttlefishError(f"artifacts_dir {configured} does not exist on the exporter")
        return root

    def _extract(self, staged: Path, root: Path, target: str | None) -> None:
        kind = _sniff_kind(staged)
        if kind is None:
            raise CuttlefishError("unrecognized artifact: expected a CVD image zip or a gzipped host package tar")
        if target is not None and target != kind:
            raise CuttlefishError(f"target {target!r} given but the archive was detected as {kind!r}")

        if kind == "image":
            with zipfile.ZipFile(staged) as z:
                names = [i.filename for i in z.infolist() if not i.is_dir()]
                self._unlink_existing(root, names)
                self.logger.info("Extracting %d image files into %s", len(names), root)
                z.extractall(root)
        else:
            with tarfile.open(staged, "r:gz") as t:
                names = [m.name for m in t.getmembers() if m.isfile()]
                self._unlink_existing(root, names)
                self.logger.info("Extracting %d host package files into %s", len(names), root)
                t.extractall(root, filter="data")

    def _unlink_existing(self, root: Path, names: list[str]) -> None:
        """Remove regular files the extraction is about to overwrite.

        A still-running CVD keeps binaries executing out of the artifacts
        directory, and opening one for writing fails with ETXTBSY — while
        unlinking it is always legal. Re-flashing therefore gives every file
        a fresh inode instead of failing on whichever file happens to be
        executing.
        """
        resolved_root = root.resolve()
        for name in names:
            dest = (root / name).resolve()
            if not dest.is_relative_to(resolved_root):
                continue  # extractall sanitizes such members; never touch paths outside root
            if dest.is_file() and not dest.is_symlink():
                dest.unlink(missing_ok=True)
