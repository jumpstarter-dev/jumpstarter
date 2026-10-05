"""Generic fastboot flasher driver: exporter-side staging and lease-independent flash jobs.

A flash is a job that outlives the call that started it, and the driver makes
it safe to run on a bench:

1. **Stage**: bundles are pulled (OCI), uploaded (tar archives, local files),
   or downloaded by the exporter, then digest-verified and decompressed before
   the device is touched. The network is never in the write path.
2. **Enter**: entry scripts put the device into fastboot.
3. **Preflight**: ``requires`` (and the build's ``android-info.txt``) are
   checked against the live bootloader, and the plan is resolved for it: A/B
   slots, partition sizes, logical partitions, and the critical-partition policy.
4. **Commit**: the plan runs as a long-running exporter task, journaled step
   by step. It keeps going if the client disconnects or the lease ends (the
   lease's teardown waits for it, and no new lease is assigned meanwhile), and
   is resumed from its journal if the exporter dies mid-flash.

While a job is active, power and the flasher's hardware children are
interlocked (:mod:`.interlock`), and an exit script can bring the device back
out of fastboot when the job is done. Phases 1-3 are ordinary, cancellable
calls; phase 4 is not cancellable once the first write has started.

The bundle format is :mod:`.manifest`; the device is pinned by bench USB port
(or serial), like the ADB driver.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import field
from functools import partial
from pathlib import Path
from typing import Any, ClassVar, NamedTuple

import anyio
from anyio import to_thread
from jumpstarter_driver_power.driver import PowerInterface, VirtualPowerInterface
from pydantic.dataclasses import dataclass

from .entry import EntryRunner, EntryStrategy, ExitScript
from .fastboot import Fastboot, FastbootError, parse_size
from .interlock import add_guard, protect_lifecycle, resolve_path, walk
from .jobs import (
    JobError,
    JobStore,
    append_record,
    atomic_write_json,
    claim_gate,
    describe,
    device_lock_path,
    read_records,
    runner_timeout,
)
from .manifest import FastbootOptions, FlashManifest, ManifestError, Requirement, normalize_path, synthesized_manifest
from .plan import DEFAULT_CRITICAL_PARTITIONS, android_info_requirements, build_plan
from .runner import run_job
from .store import BlobInfo, StageError, StageStore, bundle_root, ingest_archive, pull_oci
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export
from jumpstarter.driver.scripts import DriverScript, ScriptRunnerMixin
from jumpstarter.driver.tasks import running_tasks, start_task

OCI = "oci://"
STAGE = "stage:"

REBOOT_ARGS = {
    "normal": ["reboot"],
    "bootloader": ["reboot", "bootloader"],
    "fastboot": ["reboot", "fastboot"],
}

# Methods that only observe hardware, allowed on interlocked children during a flash.
READ_ONLY_METHODS = frozenset({"read", "status", "state", "read_pin"})


def _resolve_child(child: Any) -> Any | None:
    """The real driver behind a ``ref:`` child (a composite ``Proxy``), or ``None`` until resolved."""
    if type(child).__name__ == "Proxy" and hasattr(type(child), "_resolve_proxy_target"):
        return child._proxy_target
    return child


class DeviceBusyError(RuntimeError):
    """A flash job owns the device."""


class _Bundle(NamedTuple):
    """A bundle fetched into the store, before its manifest is chosen."""

    files: dict[str, BlobInfo]
    manifest_text: str | None
    targets: list[tuple[str, str]]
    label: str


@dataclass(kw_only=True)
class FastbootFlasher(ScriptRunnerMixin, Driver):
    """Flash devices over fastboot, with exporter-side staging and lease-independent jobs."""

    driver_type = "storage"

    unprotected_children: ClassVar[frozenset[str]] = frozenset({"adb"})
    """Children left out of the default ``interlock`` (``adb`` can't cut power)."""
    # What an entry script may still call on its own flasher (block_self refuses the rest):
    # read-only, and none of them take the lock that enter() holds while the script runs.
    script_callable_methods: ClassVar[frozenset[str]] = frozenset(
        {"wait_present", "stages", "status", "jobs", "getvar"}
    )

    usb_port: str | None = None
    """The bench USB port, as ``fastboot devices -l`` / ``adb devices -l`` report it (preferred)."""
    serial: str | None = None
    """An explicit device serial, for hardware with no usable USB devpath. Exactly one of the two."""
    fastboot_path: str = "fastboot"
    state_dir: str = "/var/lib/jumpstarter/fastboot"
    entry: list[EntryStrategy] = field(default_factory=list)
    """How this bench puts the device into fastboot: ``j`` scripts, tried in order.

    Bundles never say how to reach fastboot; this does.
    """
    exit: ExitScript | None = None
    """A script that brings the device out of fastboot after a job, before the lease can end."""
    variant: str | None = None
    """The board variant, for manifest entries marked ``variant:``."""
    critical_partitions: list[str] = field(default_factory=lambda: list(DEFAULT_CRITICAL_PARTITIONS))
    allow_non_ab_critical: bool = False
    allow_active_slot_critical: bool = False
    allowed_oem_commands: list[str] = field(default_factory=list)
    command_timeout: float = 30.0
    mode_switch_settle: float = 2.0
    step_retries: int = 3
    stall_timeout: float = 300.0
    reenumerate_timeout: float = 120.0
    max_job_duration: float = 7200.0
    stage_cache_bytes: int = 20 * 1024**3
    free_space_reserve_bytes: int = 1024**3
    oci_insecure: bool = False
    manifest_name: str = "manifest.yaml"
    interlock: list[str] | None = None
    """Children refused (except read-only methods) while a flash job is active.

    Defaults to every child (except ``unprotected_children``): power, buttons,
    and consoles that could cut power, reset, or interrupt the bootloader mid-write.
    """
    interlock_power: str | list[str] = "all"
    """Which power drivers to protect while flashing.

    * ``all`` (default): every ``PowerInterface``/``VirtualPowerInterface`` driver
      in the exporter.
    * a list of exporter driver paths, e.g. ``["pdu.outlet3"]``: only the power
      that feeds this device, for exporters that power several devices.

    The flasher's own children (and the drivers behind their ``ref:``) are
    protected regardless, through ``interlock``.
    """

    _fb: Any = field(default=None, init=False, repr=False)
    _store: Any = field(default=None, init=False, repr=False)
    _jobs: Any = field(default=None, init=False, repr=False)
    _entry_runner: Any = field(default=None, init=False, repr=False)
    _lock: Any = field(default=None, init=False, repr=False)
    _spawned: dict = field(default_factory=dict, init=False, repr=False)
    _interlocked: set = field(default_factory=set, init=False, repr=False)

    def __post_init__(self):
        try:
            self._fb = Fastboot(
                usb_port=self.usb_port,
                serial=self.serial,
                binary=self.fastboot_path,
                command_timeout=self.command_timeout,
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc
        self.usb_port = self._fb.usb_port  # normalized to usb:<path>

        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        for child in self.interlock or []:
            if child not in self.children:
                raise ConfigurationError(f"interlock names child {child!r}, which is not configured")
        names = [s.name for s in self.entry]
        if len(names) != len(set(names)):
            raise ConfigurationError("entry strategy names must be unique")

        self._entry_runner = EntryRunner(
            label=self.device,
            present=self._fb.present,
            wait_present=self._fb.wait_present,
            diagnose=self._diagnose,
            logger=self.logger,
            script_runner=self._run_entry_script,
        )
        self._store = StageStore(Path(self.state_dir), reserve_bytes=self.free_space_reserve_bytes)
        self._jobs = JobStore(Path(self.state_dir))
        self._lock = asyncio.Lock()
        self._validate_interlock_power()
        self._install_interlocks()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_fastboot.client.FastbootFlasherClient"

    # -- the device --------------------------------------------------------------

    @property
    def device(self) -> str:
        """Bench identity used for job ownership and locking: the USB port, or the serial."""
        return self._fb.label

    async def identity(self) -> dict[str, Any]:
        """The device's identity and state, recorded with the job (and returned by ``enter``)."""
        identity = {}
        for var in ("serialno", "product", "current-slot", "slot-count", "unlocked", "is-userspace"):
            identity[var] = await self._fb.getvar(var)
        return identity

    async def read_variable(self, name: str) -> str | None:
        return await self._fb.getvar(name)

    async def _diagnose(self) -> str:
        """Why the device isn't in fastboot, as far as the host can tell (for errors)."""
        try:
            await self._fb.resolve()
        except FastbootError as exc:
            return str(exc)
        return "The device is now in fastboot."

    def script_env(self) -> dict[str, str]:
        """Extra environment for entry and exit scripts (the device's port)."""
        return {"FASTBOOT_USB_PORT": self.usb_port or "", "FASTBOOT_SERIAL": self.serial or ""}

    def synthesize(self, targets: list[tuple[str, str]], name: str) -> dict[str, Any]:
        """Images without a manifest (``-t boot:boot.img``, ``fls``-style OCI): flashed in order, then booted."""
        return synthesized_manifest(
            name,
            [{"fastboot": {"flash": [{"partition": p, "file": f} for p, f in targets]}}],
            fastboot={"slot": "current", "finally": "continue"},
        )

    def _job_config(self, identity: dict[str, Any], options: FastbootOptions) -> dict[str, Any]:
        """What the runner needs, saved with the job so a resumed job runs with the same settings."""
        return {
            "usb_port": self._fb.usb_port,
            "serial": self._fb.serial,
            "binary": self._fb.binary,
            "command_timeout": self.command_timeout,
            "mode_switch_settle": self.mode_switch_settle,
            "expected_serial": identity.get("serialno"),
            "finally": options.finally_,
        }

    async def resolve_plan(
        self, manifest: FlashManifest, plan: list[dict[str, Any]], identity: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Resolve slots, and enforce partition-size and critical-write policy."""
        options = manifest.options
        current = (identity.get("current-slot") or "").lstrip("_") or None
        ab = (parse_size(identity.get("slot-count")) or 0) >= 2 and current in ("a", "b")
        other = {"a": "b", "b": "a"}.get(current or "")
        initial_mode = "userspace" if (identity.get("is-userspace") or "").lower() == "yes" else "bootloader"
        slots_for = {"current": [current], "inactive": [other], "a": ["a"], "b": ["b"], "all": ["a", "b"]}

        resolved: list[dict[str, Any]] = []
        for op in plan:
            if op["op"] in ("flash", "erase"):
                partition = op["partition"]
                slotted = ab and (await self._fb.getvar(f"has-slot:{partition}") or "").lower() == "yes"
                slots = slots_for[options.slot] if slotted else [None]
                if op.get("critical"):
                    if not slotted and not self.allow_non_ab_critical:
                        raise ManifestError(
                            f"critical partition {partition!r} has no A/B fallback slot on this device; "
                            "set allow_non_ab_critical on the exporter to permit it"
                        )
                    if slotted and current in slots and not self.allow_active_slot_critical:
                        raise ManifestError(
                            f"plan writes critical partition {partition!r} to the active slot {current!r}; "
                            "use 'slot: inactive' with a set_active step, or set allow_active_slot_critical"
                        )
                for slot in slots:
                    target = f"{partition}_{slot}" if slot else partition
                    entry = op | {"target": target, "describe": f"{op['op']} {target}"}
                    if op["op"] == "flash" and op["mode"] == initial_mode:
                        await self._check_partition(target, op["expanded_size"], op["mode"])
                    resolved.append(entry)
            elif op["op"] == "set_active":
                if not ab:
                    raise ManifestError("set_active requires an A/B device")
                slot = {"inactive": other, "current": current}.get(op["slot"], op["slot"])
                resolved.append(op | {"target": slot})
            else:
                resolved.append(op)
        return resolved

    async def _check_partition(self, target: str, expanded_size: int, mode: str) -> None:
        if mode == "bootloader" and (await self._fb.getvar(f"is-logical:{target}") or "").lower() == "yes":
            raise ManifestError(
                f"{target} is a logical partition; add a 'fastboot: {{reboot: fastboot}}' step before flashing it"
            )
        size = parse_size(await self._fb.getvar(f"partition-size:{target}"))
        if size is not None and expanded_size > size:
            raise ManifestError(f"{target}: image expands to {expanded_size} bytes, partition is {size}")

    # -- interlocks ------------------------------------------------------------

    def reset(self):
        # Children are resolved by now (ref: proxies included): protect them before
        # anything else can call them in this session.
        self._install_interlocks()
        if self._jobs.active(self.device) is not None:
            # Don't propagate reset to hardware children mid-flash: a power driver's
            # reset can switch the DUT off (e.g. Ykush with default: off).
            self.logger.warning("flash job active on %s: not resetting child drivers", self.device)
        else:
            super().reset()

    def _validate_interlock_power(self) -> None:
        scope = self.interlock_power
        if isinstance(scope, str):
            if scope != "all":
                raise ConfigurationError(f"interlock_power must be 'all' or a list of driver paths, got {scope!r}")
        elif not scope or not all(isinstance(p, str) and p.strip() for p in scope):
            raise ConfigurationError("interlock_power must list at least one driver path, e.g. ['pdu.outlet3']")

    def enumerate(self, *, root=None, parent=None, name=None):
        # The exporter enumerates the whole tree (resolving ref: proxies) when it
        # builds a session, before it calls reset(). ScriptRunnerMixin captures the
        # root, which lets the flasher protect every power driver before any of them
        # is reset.
        result = super().enumerate(root=root, parent=parent, name=name)
        self._install_interlocks()
        return result

    def _install_interlocks(self) -> None:
        if self.interlock is not None:
            names = self.interlock
        else:
            names = [n for n in self.children if n not in self.unprotected_children]
        for name in names:
            target = _resolve_child(self.children[name])
            if target is not None:
                self._protect(target, name)
        for label, target in self._power_targets():
            self._protect(target, label)

    def _power_targets(self) -> list[tuple[str, Any]]:
        """The power drivers to protect, per ``interlock_power``."""
        root = self.exporter_root
        if root is None:  # before enumerate(): the tree isn't known yet
            return []
        if isinstance(self.interlock_power, list):
            return [(path, self._resolve_power_path(path)) for path in self.interlock_power]
        power_types = (PowerInterface, VirtualPowerInterface)
        return [(type(n).__name__, n) for n in walk(root) if n is not self and isinstance(n, power_types)]

    def _resolve_power_path(self, path: str) -> Any:
        try:
            return resolve_path(self.exporter_root, path)
        except KeyError:
            raise ConfigurationError(f"interlock_power path {path!r} is not in this exporter") from None

    def _protect(self, target: Any, label: str) -> None:
        if id(target) in self._interlocked:
            return
        add_guard(target, self._interlock_guard(label))
        protect_lifecycle(target, self._busy)
        self._interlocked.add(id(target))

    def _busy(self) -> str | None:
        active = self._jobs.active(self.device)
        return None if active is None else f"flash job {active} is writing to the device on {self.device}"

    def _interlock_guard(self, label: str):
        def guard(method: str) -> str | None:
            if method in READ_ONLY_METHODS:
                return None
            busy = self._busy()
            if busy is None:
                return None
            return (
                f"{label}.{method} refused: {busy}. "
                "Cutting power, pressing buttons, or using its console mid-flash can brick it. "
                "Wait for the job with 'wait-idle'."
            )

        return guard

    # -- device access ---------------------------------------------------------

    def _guard(self) -> None:
        """Refuse device access while a job owns the device."""
        active = self._jobs.active(self.device)
        if active is not None:
            info = self._jobs.info(active)
            raise DeviceBusyError(
                f"device {self.device} is owned by flash job {active} ({info['state']}, "
                f"{info['steps_done']}/{info['total_steps']} steps); wait for it with 'wait-idle'"
            )

    def _not_from_entry_script(self, what: str) -> None:
        # With block_self (the default) the guard refuses these first; this covers block_self: false.
        if self.script_running():
            raise RuntimeError(f"an entry script cannot call {what} on its own flasher")

    async def _run_entry_script(self, script: DriverScript) -> None:
        await self.run_script(script, env=self.script_env())

    @export
    async def wait_present(self, timeout: float = 0) -> bool:
        """Whether the device is in fastboot, waiting up to ``timeout`` seconds for it.

        Callable from the flasher's own entry scripts, e.g. to hold a button until
        the device shows up.
        """
        return await self._fb.wait_present(timeout)

    @export
    async def enter(self, strategy: str | None = None) -> dict[str, Any]:
        """Put the device into fastboot using the exporter's entry strategies."""
        self._not_from_entry_script("enter")
        await self._resume_stalled()
        async with self._lock:
            self._guard()
            used = await self._entry_runner.enter(self.entry, strategy)
            return {"strategy": used, **await self.identity()}

    @export
    async def getvar(self, name: str) -> str | None:
        """Read a bootloader variable (refused while a job owns the device)."""
        self._guard()
        return await self._fb.getvar(name)

    @export
    async def reboot(self, mode: str = "normal") -> None:
        """Reboot from fastboot into ``normal``, ``bootloader``, or ``fastboot`` (fastbootd)."""
        if mode not in REBOOT_ARGS:
            raise ValueError(f"mode must be one of {', '.join(REBOOT_ARGS)}")
        self._not_from_entry_script("reboot")
        async with self._lock:
            self._guard()
            if not await self._fb.present():
                raise RuntimeError(f"device on {self.device} is not in fastboot")
            await self._fb.run(REBOOT_ARGS[mode], timeout=self.command_timeout)

    # -- flashing --------------------------------------------------------------

    @export
    async def flash(
        self,
        source: Any,
        manifest: Any | None = None,
        wipe: bool = False,
        job_id: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stage ``source``, start a job, and follow it to the end, streaming ``FlashStatus`` updates.

        ``source`` is a bundle archive (a resource handle: a local tar file or an
        http(s) URL), ``oci://registry/repo:tag``, or ``stage:<id>`` for a bundle
        staged earlier. ``manifest`` (YAML text or a mapping) replaces the
        bundle's own. ``wipe`` runs the ``when: wipe`` steps; ``job_id`` makes
        the call idempotent.

        Ending the call (or the lease) doesn't stop the job; follow it again with
        ``watch``.
        """
        if isinstance(source, str) and source.startswith(STAGE):
            if manifest is not None:
                raise ValueError("a staged bundle carries its manifest; stage again to use another")
            stage_id = source.removeprefix(STAGE)
        else:
            stage_id = None
            async for status in self.stage(source, manifest, username, password):
                if "stage_id" in status:
                    stage_id = status["stage_id"]
                    status = status | {"phase": "cache"}  # staged: the bundle is cached on the exporter
                yield status
            if stage_id is None:
                raise StageError("staging finished without a stage")
        info = await self.start(stage_id, job_id, wipe)
        yield {
            "phase": "step",
            "message": f"job {info['job_id']} started ({info['total_steps']} steps)",
            "job_id": info["job_id"],
            "total_steps": info["total_steps"],
        }
        async for status in self._follow(info["job_id"], until_exit=True):
            yield status

    # -- staging ---------------------------------------------------------------

    @export
    async def stage(
        self,
        source: Any,
        manifest: Any | None = None,
        username: str | None = None,
        password: str | None = None,
        manifest_name: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Copy a bundle onto the exporter and verify it, without touching the device.

        ``source`` is ``oci://...`` (pulled by the exporter) or a resource handle
        to a tar archive. ``manifest`` replaces the bundle's own; ``manifest_name``
        names it inside the bundle (default: the flasher's ``manifest_name``). The
        last update carries the ``stage_id``.
        """
        name = normalize_path(manifest_name or self.manifest_name)
        bundle: _Bundle | None = None
        async for kind, item in self._fetch(source, name, username, password):
            if kind == "progress":
                yield item
            else:
                bundle = item
        if bundle is None:
            raise StageError("fetching the bundle produced no result")

        if manifest is not None:
            raw, synthesized = manifest, False
        elif bundle.manifest_text is not None:
            raw, synthesized = bundle.manifest_text, False
        elif bundle.targets:
            raw, synthesized = self.synthesize(bundle.targets, bundle.label), True
        else:
            raise StageError(f"{bundle.label} has no {name} and no target annotations")
        yield await self._finish_stage(bundle.label, raw, bundle.files, synthesized)

    async def _fetch(
        self, source: Any, name: str, username: str | None, password: str | None
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """Pull (OCI) or receive (archive) a bundle into the store: progress items, then a ``_Bundle``."""
        if isinstance(source, str):
            if not source.startswith(OCI):
                raise ValueError(f"unsupported source {source!r}: pass oci://..., stage:<id>, or a bundle archive")
            ref = source.removeprefix(OCI)
            async for kind, item in _run_with_progress(partial(self._pull_oci, ref, name, username, password)):
                if kind == "progress":
                    yield kind, item
                else:
                    yield "result", _Bundle(item.files, item.manifest_text, item.targets, f"{OCI}{ref}")
            return

        yield "progress", {"phase": "download", "message": "receiving the bundle"}
        async for kind, item in self._receive_archive(source):
            if kind == "progress":
                yield kind, item
                continue
            prefix, found = bundle_root(item, name)
            manifest_text = Path(item[found].path).read_text() if found else None
            files = {n.removeprefix(prefix): info for n, info in item.items() if n.startswith(prefix)}
            yield "result", _Bundle(files, manifest_text, [], "bundle archive")

    def _pull_oci(self, ref: str, name: str, username: str | None, password: str | None, progress) -> Any:
        return pull_oci(self._store, ref, manifest_name=name, username=username, password=password,
                        insecure=self.oci_insecure, progress=progress)

    async def _receive_archive(self, handle: Any) -> AsyncGenerator[tuple[str, Any], None]:
        tmp = self._store.tmpfile()
        try:
            received = 0
            async with self.resource(handle) as stream, await anyio.open_file(tmp, "wb") as f:
                async for chunk in stream:
                    await f.write(chunk)
                    received += len(chunk)
            if received == 0:
                raise StageError("received no data")
            async for item in _run_with_progress(lambda progress: ingest_archive(self._store, tmp, progress=progress)):
                yield item
        finally:
            tmp.unlink(missing_ok=True)

    @export
    async def stage_blob(self, handle: Any, sha256: str | None = None) -> dict[str, Any]:
        """Receive one file onto the exporter, verify ``sha256`` (of the bytes sent), and store it."""
        tmp = self._store.tmpfile()
        try:
            received = 0
            async with self.resource(handle) as stream, await anyio.open_file(tmp, "wb") as f:
                async for chunk in stream:
                    await f.write(chunk)
                    received += len(chunk)
            if received == 0:
                raise StageError("received no data")
            info = await to_thread.run_sync(lambda: self._store.ingest(tmp, expected_sha256=sha256))
        finally:
            tmp.unlink(missing_ok=True)
        return {"sha256": info.sha256, "size": info.size, "sparse": info.sparse}

    @export
    async def stage_create(
        self,
        files: dict[str, str],
        manifest: Any | None = None,
        targets: list[list[str]] | None = None,
    ) -> dict[str, Any]:
        """Create a stage from blobs sent with ``stage_blob``; ``files`` maps bundle paths to blob digests.

        Pass either ``manifest`` (a ``FlashManifest``, YAML text or a mapping,
        referencing the paths) or ``targets`` (``[[partition, path], ...]`` in
        flash order, to flash images without a manifest).
        """
        if (manifest is None) == (targets is None):
            raise ValueError("pass exactly one of manifest or targets")
        staged = {normalize_path(name): self._store.blob(digest) for name, digest in files.items()}
        if manifest is not None:
            raw, synthesized = manifest, False
        else:
            raw = self.synthesize([(t, normalize_path(n)) for t, n in targets or []], "local files")
            synthesized = True
        return await self._finish_stage("local files", raw, staged, synthesized)

    def _plan(self, manifest: FlashManifest, files: dict[str, BlobInfo], *, wipe: bool) -> list[dict[str, Any]]:
        return build_plan(
            manifest,
            files=set(files),
            variant=self.variant,
            wipe=wipe,
            critical_partitions=self.critical_partitions,
            allowed_oem_commands=self.allowed_oem_commands,
        )

    async def _finish_stage(
        self, source: str, raw: Any, files: dict[str, BlobInfo], synthesized: bool
    ) -> dict[str, Any]:
        manifest = FlashManifest.parse(raw)
        # Validate now (wipe=True covers every step) so a bad bundle fails before a lease is spent on it.
        plan = self._plan(manifest, files, wipe=True)
        if synthesized:
            _refuse_synthesized_critical(plan)
        self.requirements(manifest, files)  # native requirement files parse
        stage_id = self._store.create_stage(source=source, manifest=manifest.raw, files=files, synthesized=synthesized)
        pinned = {stage_id} | {
            self._jobs.load(j)["stage_id"] for j in self._jobs.ids() if self._jobs.result(j) is None
        }
        await to_thread.run_sync(lambda: self._store.gc(self.stage_cache_bytes, pinned))
        total = sum(f.size for f in files.values())
        return {
            "phase": "complete",
            "message": f"staged {len(files)} file(s), {total} bytes, as {stage_id}",
            "stage_id": stage_id,
        }

    @export
    async def stages(self) -> list[dict[str, Any]]:
        return self._store.list_stages()

    # -- jobs ------------------------------------------------------------------

    @export
    async def start(self, stage_id: str, job_id: str | None = None, wipe: bool = False) -> dict[str, Any]:
        """Enter, preflight, and start the job as an exporter task. Idempotent on ``job_id``."""
        self._not_from_entry_script("start")
        await self._resume_stalled()
        async with self._lock:
            if job_id is not None and self._jobs.exists(job_id):
                return self._jobs.info(job_id)
            self._guard()

            stage = self._store.load_stage(stage_id)
            manifest = FlashManifest.parse(stage["manifest"])
            files = {name: BlobInfo.from_dict(info) for name, info in stage["files"].items()}
            plan = self._plan(manifest, files, wipe=wipe)
            if stage["synthesized"]:
                _refuse_synthesized_critical(plan)  # the flasher's policy may have changed since staging

            await self._entry_runner.enter(self.entry)
            identity = await self.identity()
            await self._check_requirements(manifest, files)
            resolved = await self.resolve_plan(manifest, _attach_blobs(plan, files), identity)

            job_id = job_id or uuid.uuid4().hex[:12]
            self._jobs.create(
                job_id,
                {
                    "job_id": job_id,
                    "created": time.time(),
                    "device": self.device,
                    "stage_id": stage_id,
                    "source": stage["source"],
                    "identity": identity,
                    "plan": resolved,
                    "fastboot": self._job_config(identity, manifest.options),
                    "exit": self._exit_spec(job_id),
                    "device_lock": str(device_lock_path(Path(self.state_dir), self.device)),
                    "settings": {
                        "step_retries": self.step_retries,
                        "stall_timeout": self.stall_timeout,
                        "reenumerate_timeout": self.reenumerate_timeout,
                        "max_job_duration": self.max_job_duration,
                    },
                },
            )
            await self._start_runner(job_id)
            if not await to_thread.run_sync(lambda: self._jobs.wait_started(job_id)):
                raise RuntimeError(
                    f"flash job {job_id} did not start; nothing was written. Cancel it with 'cancel {job_id}'."
                )
            self.logger.info("flash job %s started (%d steps)", job_id, len(resolved))
            return self._jobs.info(job_id)

    def requirements(self, manifest: FlashManifest, files: dict[str, BlobInfo]) -> list[Requirement]:
        """The manifest's ``requires``, then those of the build's ``android-info.txt``."""

        def read_text(path: str) -> str:
            ref = normalize_path(path)
            if ref not in files:
                raise ManifestError(f"file {path!r} is not in the bundle")
            return Path(files[ref].path).read_text(errors="replace")

        return [*manifest.requires.values(), *android_info_requirements(manifest.options, read_text)]

    async def _check_requirements(self, manifest: FlashManifest, files: dict[str, BlobInfo]) -> None:
        product: str | None = None
        for req in self.requirements(manifest, files):
            name = req.variable or "?"
            if req.for_product is not None:
                product = product if product is not None else await self.read_variable("product")
                if product != req.for_product:
                    continue
            value = await self.read_variable(name)
            if value is None:
                if req.optional:
                    continue
                raise ManifestError(f"requirement {name!r}: the device does not report it")
            if not req.matches(value):
                verb = "rejects" if req.reject else "requires"
                raise ManifestError(f"requirement {name!r}: device reports {value!r}, bundle {verb} {req.allowed}")

    def _exit_spec(self, job_id: str) -> dict[str, Any] | None:
        """The exit script, as the job runs it when it finishes."""
        if self.exit is None:
            return None
        cfg = self.exit
        env = {**cfg.env, "JMP_DRIVER_PATH": self._self_path(cfg), **self.script_env(), "FLASH_JOB_ID": job_id}
        return {"script": cfg.script, "exec": cfg.exec_, "timeout": cfg.timeout, "when": cfg.when, "env": env}

    def _latest(self, job_id: str | None) -> str:
        if job_id is not None:
            self._jobs.load(job_id)
            return job_id
        mine = self._my_jobs()
        if not mine:
            raise JobError("no flash jobs for this device")
        return mine[-1]

    def _my_jobs(self) -> list[str]:
        return [j for j in self._jobs.ids() if self._jobs.load(j)["device"] == self.device]

    def _task_name(self, job_id: str) -> str:
        return f"flash-{job_id}"

    async def _start_runner(self, job_id: str, *, resume: bool = False) -> None:
        """Run the job as a long-running exporter task: the lease lifecycle doesn't interrupt it."""
        job = self._jobs.load(job_id)
        self._spawned[job_id] = time.monotonic()
        await start_task(
            self._task_name(job_id),
            f"fastboot flash job {job_id} on {job['device']}",
            self._run_job,
            job_id,
            resume,
            timeout=runner_timeout(job["settings"]),
            on_lease_end="wait",  # never interrupt a flash
        )

    async def _run_job(self, job_id: str, resume: bool) -> None:
        await run_job(self._jobs.path(job_id), resume, after=lambda: self._run_exit(job_id))

    async def _run_exit(self, job_id: str) -> None:
        """Run the job's exit script, if it has one and the job's outcome calls for it."""
        job_dir = self._jobs.path(job_id)
        spec = self._jobs.load(job_id).get("exit")
        result = self._jobs.result(job_id)
        if not spec or result is None:
            return
        state = result["state"]
        if spec["when"] != "always" and state != "succeeded":
            append_record(job_dir / "journal.jsonl", "exit-skipped", reason=f"job {state}; runs after successes only")
            return
        append_record(job_dir / "journal.jsonl", "exit-running")
        script = ExitScript.model_validate(
            {"script": spec["script"], "exec": spec.get("exec"), "timeout": spec["timeout"]}
        )
        try:
            output = (await self.run_script(script, env=spec["env"] | {"FLASH_JOB_STATE": state})).output
            outcome = {"state": "succeeded", "error": None, "output": output.splitlines()[-20:]}
        except Exception as exc:  # noqa: BLE001 - recorded in the job's status
            self.logger.warning("exit script of flash job %s failed: %s", job_id, exc)
            outcome = {"state": "failed", "error": str(exc), "output": []}
        atomic_write_json(job_dir / "exit.json", outcome)
        append_record(job_dir / "journal.jsonl", "exit-done", state=outcome["state"])

    def _task_running(self, job_id: str) -> bool:
        return any(task.name == self._task_name(job_id) for task in running_tasks())

    async def _ensure_runner(self, job_id: str) -> None:
        """Resume a job whose runner died without a result (exporter crash, host power loss)."""
        if self._jobs.info(job_id)["state"] != "stalled" or self._task_running(job_id):
            return
        if time.monotonic() - self._spawned.get(job_id, 0.0) < 15:
            return
        self.logger.warning("resuming flash job %s from its journal", job_id)
        await self._start_runner(job_id, resume=True)

    async def _resume_stalled(self) -> None:
        active = self._jobs.active(self.device)
        if active is not None:
            await self._ensure_runner(active)

    @export
    async def status(self, job_id: str | None = None) -> dict[str, Any]:
        job_id = self._latest(job_id)
        await self._ensure_runner(job_id)
        return self._jobs.info(job_id)

    @export
    async def jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        return [self._jobs.info(j) for j in self._my_jobs()[-limit:]]

    @export
    async def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a job that has not started writing. Refused once the first write began."""
        job_dir = self._jobs.path(job_id)
        self._jobs.load(job_id)
        if self._jobs.result(job_id) is not None:
            return self._jobs.info(job_id)
        winner = claim_gate(job_dir, "cancel")
        if winner != "cancel":
            raise RuntimeError(
                f"job {job_id} has started writing; cancelling would leave a partially written device, "
                "so it will run to completion"
            )
        await asyncio.sleep(1.0)
        if self._jobs.result(job_id) is None and not self._jobs.alive(job_id):
            result = {"state": "cancelled", "message": "cancelled before the first write", "finished": time.time()}
            atomic_write_json(job_dir / "result.json", result)
            append_record(job_dir / "journal.jsonl", "result", **result)
        return self._jobs.info(job_id)

    @export
    async def resume(self, job_id: str) -> dict[str, Any]:
        """Re-run an ``interrupted`` job from its first unfinished step (e.g. after re-entering)."""
        async with self._lock:
            result = self._jobs.result(job_id)
            if result is not None and result["state"] != "interrupted":
                raise RuntimeError(f"job {job_id} is {result['state']}; only interrupted jobs can be resumed")
            if result is not None:
                job_dir = self._jobs.path(job_id)
                (job_dir / "result.json").rename(job_dir / f"result.interrupted.{int(time.time())}.json")
            if not self._jobs.alive(job_id) and not self._task_running(job_id):
                await self._start_runner(job_id, resume=True)
                await to_thread.run_sync(lambda: self._jobs.wait_started(job_id))
            return self._jobs.info(job_id)

    @export
    async def wait_idle(self, timeout: float | None = None) -> dict[str, Any] | None:
        """Block until no job owns the device and its exit script has run; return the last job's info.

        For scripts and CI that need the device back. Lease hooks don't need it:
        the exporter runs them only after a flash has finished.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            active = self._jobs.active(self.device)
            if active is None:
                mine = self._my_jobs()
                info = self._jobs.info(mine[-1]) if mine else None
                if info is None or not _exit_pending(info):
                    return info
                active = info["job_id"]  # finished; its exit script hasn't run yet
            else:
                await self._ensure_runner(active)
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"flash job {active} still running after {timeout}s")
            await asyncio.sleep(1.0)

    @export
    async def watch(self, job_id: str | None = None) -> AsyncGenerator[dict[str, Any], None]:
        """Replay a job's journal and follow it until it reaches a terminal state."""
        async for status in self._follow(self._latest(job_id), until_exit=False):
            yield status

    async def _follow(  # noqa: C901 - one loop merging the journal and the fastboot output
        self, job_id: str, *, until_exit: bool
    ) -> AsyncGenerator[dict[str, Any], None]:
        job_dir = self._jobs.path(job_id)
        plan = self._jobs.load(job_id)["plan"]
        journal_offset = 0
        output_offset = 0
        partial = ""
        step: dict[str, Any] = {}
        while True:
            # Journal records are read up to a snapshot first, then the output that
            # preceded them, so fastboot's output is never cut off by the result.
            records, journal_offset = read_records(job_dir / "journal.jsonl", journal_offset)
            data = await to_thread.run_sync(lambda offset=output_offset: _read_from(job_dir / "output.log", offset))
            if data:
                output_offset += len(data)
                partial += data.decode(errors="replace")
                *lines, partial = partial.replace("\r", "\n").split("\n")
                for line in lines:
                    if line.strip():
                        yield {"phase": "step", "message": line.strip(), "job_id": job_id, **step}

            for record in records:
                status = _status_from_record(record, plan, job_id)
                if record["event"] == "step-start":
                    step = {k: status[k] for k in ("step_index", "total_steps", "step_name")}
                if status["phase"] in ("complete", "error"):
                    if until_exit:
                        async for final in self._finish_with_exit(job_id, status):
                            yield final
                    else:
                        yield status
                    return
                yield status

            if self._jobs.result(job_id) is None:
                await self._ensure_runner(job_id)
            await asyncio.sleep(0.5)

    async def _finish_with_exit(self, job_id: str, final: dict[str, Any]) -> AsyncGenerator[dict[str, Any], None]:
        """The job's result, once its exit script (if any) has run: a failed exit script fails the flash."""
        info = self._jobs.info(job_id)
        if _exit_pending(info):
            yield {"phase": "step", "message": "running the exit script", "job_id": job_id}
            while _exit_pending(info := self._jobs.info(job_id)):
                await asyncio.sleep(0.5)
        exit_info = info.get("exit")
        if exit_info is not None:
            final = final | {"exit": exit_info}
            if final["phase"] == "complete" and exit_info["state"] == "failed":
                final |= {"phase": "error", "message": f"{final['message']}; exit script failed: {exit_info['error']}"}
        yield final


def _exit_pending(info: dict[str, Any]) -> bool:
    return info.get("exit", {}).get("state") in ("waiting", "running")


def _refuse_synthesized_critical(plan: list[dict[str, Any]]) -> None:
    critical = sorted({op["describe"] for op in plan if op.get("critical")})
    if critical:
        raise ManifestError(
            f"critical writes ({', '.join(critical)}) need an explicit FlashManifest "
            "(ordered steps, the fastboot safety options); refusing to synthesize one"
        )


def _attach_blobs(plan: list[dict[str, Any]], files: dict[str, BlobInfo]) -> list[dict[str, Any]]:
    out = []
    for op in plan:
        if "file" in op:
            blob = files[op["file"]]
            op = op | {"path": blob.path, "sha256": blob.sha256, "size": blob.size, "expanded_size": blob.expanded_size}
        out.append(op)
    return out


def _read_from(path: Path, offset: int) -> bytes:
    if not path.exists():
        return b""
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read()


def _status_from_record(record: dict[str, Any], plan: list[dict[str, Any]], job_id: str) -> dict[str, Any]:
    base = {"job_id": job_id, "total_steps": len(plan)}
    event = record["event"]
    if event == "step-start":
        op = plan[record["index"]]
        return base | {
            "phase": "step",
            "message": f"{'[critical] ' if op.get('critical') else ''}{describe(op)}",
            "step_index": record["index"] + 1,
            "step_name": describe(op),
        }
    if event == "step-done":
        return base | {"phase": "step", "message": "done", "step_index": record["index"] + 1}
    if event == "result":
        ok = record["state"] == "succeeded"
        return base | {
            "phase": "complete" if ok else "error",
            "message": f"{record['state']}: {record['message']}",
            "state": record["state"],
        }
    details = {k: v for k, v in record.items() if k not in ("ts", "event")}
    text = event + (" " + " ".join(f"{k}={v}" for k, v in details.items()) if details else "")
    return base | {"phase": "step", "message": text}


async def _run_with_progress(work) -> AsyncGenerator[tuple[str, Any], None]:
    """Run blocking ``work(progress)`` in a thread: yields ``("progress", dict)`` items, then ``("result", value)``."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def progress(item: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    task = asyncio.ensure_future(to_thread.run_sync(lambda: work(progress)))
    while not task.done() or not queue.empty():
        try:
            yield "progress", await asyncio.wait_for(queue.get(), timeout=0.5)
        except TimeoutError:
            continue
    yield "result", task.result()


__all__ = ["DeviceBusyError", "FastbootFlasher"]
