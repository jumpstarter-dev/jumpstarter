"""Stage CDI disks, then activate the whole set with one conditional VM update."""

import logging
import ssl
import time
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from anyio import CancelScope, create_task_group, fail_after, sleep
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.resources.core_v1 import PersistentVolumeClaim
from lightkube.resources.storage_v1 import StorageClass
from lightkube.utils.quantity import parse_quantity

from .errors import SUPERSEDED
from .vm import (
    TERMINAL_PHASES,
    DataVolume,
    UploadTokenRequest,
    VirtualMachineInstance,
    VMControl,
    active,
    operation,
)

logger = logging.getLogger(__name__)


@dataclass
class Disk:
    size: str = "20Gi"
    storage_class_name: str = ""
    access_modes: list[str] = field(default_factory=lambda: ["ReadWriteOnce"])
    volume_mode: str = ""


def volumes(vm):
    return vm["spec"]["template"]["spec"]["volumes"]


def disk_names(items):
    return {v["name"]: v["dataVolume"]["name"] for v in items if "dataVolume" in v}


def references(items):
    return set(disk_names(items).values()) | {
        v["persistentVolumeClaim"]["claimName"] for v in items if "persistentVolumeClaim" in v
    }


# CDI reports an exhausted namespace quota as ErrExceededQuota: on Bound when
# the claim cannot be created, and on Running when the upload pod hits it. The
# pressure can clear on its own — a quota frees up, a preempted flash's claim is
# still terminating — so it fails a flash only once it has persisted. Everything
# else, including a still-provisioning "Pending", is a condition the volume can
# recover from, and it surfaces with the conditions when the caller's timeout
# expires; an upload pod with nowhere to run produces no condition at all (CDI
# only annotates unschedulable importer pods), so it too waits out the timeout.
_FATAL_REASONS = frozenset({"ErrExceededQuota"})


def _fatal_condition(status):
    for condition in status.get("conditions") or []:
        if condition.get("status") == "False" and condition.get("reason") in _FATAL_REASONS:
            return condition
    return None


# KubeVirt hard-stops the guest at its termination grace period; the margin covers teardown.
_SHUTDOWN_MARGIN = 30


def _failure(error):
    """Unwrap a TaskGroup failure so the RPC reports the disk error, not the group.

    The task group reports provisioning failures as "unhandled errors in a TaskGroup
    (1 sub-exception)", which hides the quota or storage-class message that caused them.
    """
    exceptions = getattr(error, "exceptions", None)
    if not exceptions:
        return error
    if len(exceptions) == 1:
        return exceptions[0]
    return RuntimeError("; ".join(str(exception) for exception in exceptions))


@dataclass
class Flash:
    vm: VMControl
    disks: dict[str, Disk]
    upload_proxy_url: str
    upload_ca: str = ""
    timeout: float = 1800
    shutdown_timeout: float = 120
    ready_timeout: float = 30
    provision_timeout: float = 300
    fatal_grace: float = 60
    poll_interval: float = 1
    ssl_context: ssl.SSLContext = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        # Reject an unusable CA bundle before the operation creates any volume.
        self.ssl_context = ssl.create_default_context(cadata=self.upload_ca or None)

    async def begin(self, operation_id: str, targets: list[str]) -> None:
        if not targets or len(set(targets)) != len(targets):
            raise ValueError("Specify distinct disk targets")
        unknown = set(targets) - self.disks.keys()
        if unknown:
            raise ValueError(f"Unknown or non-flashable disk targets: {', '.join(sorted(unknown))}")
        self._validate_proxy_url()
        vm = await self.vm.get()
        # Reclaiming never changes which disks the VM references: a staged volume is
        # unreferenced, and a committed activation swapped the references before this
        # read, so the disk names resolved below are already current.
        await self.reclaim(vm)
        current = disk_names(volumes(vm))
        state = {"id": operation_id, "phase": "staging", "disks": {}}
        for target in targets:
            if target not in current:
                raise ValueError(f"Disk {target!r} must use a DataVolume to support flashing")
            storage = await self._storage(target, current[target])
            state["disks"][target] = {
                "old": current[target],
                "new": f"{self.vm.name[:20]}-{target[:12]}-{uuid4().hex[:20]}",
                "storage": storage,
            }

        def change(vm):
            # Re-checked on every retry: the disk references must be the ones whose
            # storage we just resolved, and nothing may have claimed the VM meanwhile.
            self._check_disks(vm, state, "old")
            if (pending := operation(vm)) and pending["phase"] not in TERMINAL_PHASES:
                raise RuntimeError("Another storage operation started during flash; retry the flash")
            return None, state

        vm = await self.vm.update(change)
        await self._provision(state, vm)

    def _validate_proxy_url(self):
        url = urlsplit(self.upload_proxy_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configure upload_proxy_url as an HTTPS CDI upload proxy URL")

    async def reclaim(self, vm=None) -> bool:
        """Clear an unfinished operation; the last flash wins.

        Recovery is pinned to the id that was read, so it can never touch a newer
        operation that landed in between.
        """
        state = operation(vm if vm is not None else await self.vm.get())
        if state is None or state["phase"] in TERMINAL_PHASES:
            return False
        return await self.recover(state["id"])

    async def _provision(self, state, vm):
        """Create every DataVolume and wait for its upload pod, all at once."""
        try:
            async with create_task_group() as group:
                for target, disk in state["disks"].items():
                    group.start_soon(self._prepare, state["id"], target, disk, vm)
        except BaseException as error:  # noqa: BLE001 - cancellation must discard too
            # A failure, or this RPC being cancelled, must not leave an operation behind:
            # this lease's next flash would be rejected by it. The id is already written,
            # so finish discarding it even if the cancellation came from our own caller.
            with CancelScope(shield=True):
                await self._discard(state["id"])
            raise _failure(error) from None

    async def _discard(self, operation_id):
        try:
            await self.recover(operation_id)
        except Exception:
            logger.warning(
                "Failed to discard the interrupted flash; the next flash or lease cleans it up", exc_info=True
            )

    async def _storage(self, target, current):
        disk = self.disks[target]
        size = parse_quantity(disk.size)
        if size is None or size <= 0:
            raise ValueError(f"Disk {target!r} must have a positive size")
        storage = {"resources": {"requests": {"storage": disk.size}}, "accessModes": disk.access_modes}
        storage_class = disk.storage_class_name
        mode = disk.volume_mode
        if not storage_class or not mode:
            dv = await self.vm.client.get(DataVolume, name=current, namespace=self.vm.namespace)
            claim = dv.get("status", {}).get("claimName") or current
            try:
                pvc = await self.vm.client.get(PersistentVolumeClaim, name=claim, namespace=self.vm.namespace)
                if pvc.spec is None:
                    raise RuntimeError(f"PVC {claim!r} has no storage specification")
                storage_class = storage_class or pvc.spec.storageClassName
                mode = mode or pvc.spec.volumeMode
            except ApiError as exc:
                if exc.status.code != 404:
                    raise
        if not storage_class:
            raise ValueError(f"Disk {target!r} has no resolved storage class; configure storageClassName")
        sc = await self.vm.client.get(StorageClass, name=storage_class)
        if sc.volumeBindingMode == "WaitForFirstConsumer":
            raise ValueError(f"Storage class {storage_class!r} uses WaitForFirstConsumer; flashing requires Immediate")
        storage["storageClassName"] = storage_class
        if mode:
            storage["volumeMode"] = mode
        return storage

    async def _target(self, operation_id, target):
        vm, state = await self.vm.transaction(operation_id, {"staging"})
        if target not in state["disks"]:
            raise ValueError(f"Disk {target!r} is not part of this flash")
        return vm, state["disks"][target]

    async def _prepare(self, operation_id, target, disk, vm):
        """Provision the upload pod before the client opens a short-lived resource URL."""
        with fail_after(self.timeout):
            dv = DataVolume(
                metadata=ObjectMeta(
                    name=disk["new"],
                    namespace=self.vm.namespace,
                    labels=dict(vm.metadata.labels or {}),
                    annotations={
                        "cdi.kubevirt.io/storage.deleteAfterCompletion": "false",
                        "cdi.kubevirt.io/storage.usePopulator": "false",
                    },
                    ownerReferences=[
                        OwnerReference(
                            apiVersion="kubevirt.io/v1",
                            kind="VirtualMachine",
                            name=self.vm.name,
                            uid=vm.metadata.uid,
                            controller=True,
                        )
                    ],
                ),
                spec={"source": {"upload": {}}, "contentType": "kubevirt", "storage": disk["storage"]},
            )
            await self.vm.client.create(dv)
            # No resource URL is open yet, so this wait covers PVC binding, volume attach
            # and the first pull of the uploadserver image on a cold node.
            await self._wait_ready(disk["new"], self.provision_timeout)

    async def _wait_ready(self, name, timeout):
        """Wait for the upload pod to serve the volume.

        The caller picks the timeout: begin has no resource URL open yet, while
        stage's client has already opened one, so its wait has to stay short.
        """
        try:
            with fail_after(timeout):
                await self._wait_volume(name, "UploadReady")
        except TimeoutError:
            dv = await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
            status = dv.get("status", {})
            raise RuntimeError(
                f"DataVolume {name} did not become ready within {timeout:g}s: "
                f"{status.get('phase')}: {status.get('conditions', [])}"
            ) from None

    async def stage(self, operation_id, target, resource):
        with fail_after(self.timeout):
            _, disk = await self._target(operation_id, target)
            # An upload pod that was evicted or restarted since provisioning needs a
            # moment to come back; the client has already opened its resource URL, so this
            # wait has to stay short.
            await self._wait_ready(disk["new"], self.ready_timeout)
            # CDI restarts the upload pod when conversion or resizing fails after the
            # transfer, and drops the DataVolume back to UploadScheduled/UploadReady
            # instead of failing it, so a restart is what tells this wait to give up.
            restarts = (await self._status(disk["new"])).get("restartCount") or 0
            token = await self.vm.client.create(
                UploadTokenRequest(
                    metadata=ObjectMeta(generateName="jumpstarter-", namespace=self.vm.namespace),
                    spec={"pvcName": disk["new"]},
                )
            )
            logger.info("Uploading disk %s", target)
            async with resource() as stream:
                await self._upload(stream, token["status"]["token"])
            await self._wait_volume(disk["new"], "Succeeded", restarts=restarts)

    async def _upload(self, stream, token):
        # The caller bounds the upload with fail_after; aiohttp's own total timeout
        # only duplicates it, and defaults to 5 minutes, which is shorter than a flash.
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session,
            session.post(
                self.upload_proxy_url.rstrip("/") + "/v1beta1/upload-async",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
                data=stream,
                ssl=self.ssl_context,
                allow_redirects=False,
            ) as response,
        ):
            if response.status != 200:
                raise RuntimeError(f"CDI upload failed with HTTP {response.status}")

    async def _status(self, name):
        dv = await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
        return dv.get("status", {})

    async def _wait_volume(self, name, phase, restarts=None):
        stuck_since = None
        while True:
            status = await self._status(name)
            if status.get("phase") == phase:
                return
            if restarts is not None and (status.get("restartCount") or 0) > restarts:
                raise RuntimeError(
                    f"DataVolume {name} restarted its upload pod after the upload, so CDI conversion "
                    f"or resizing failed: {status.get('phase')}: {status.get('conditions', [])}"
                )
            if status.get("phase") in ("Failed", "WaitForFirstConsumer", "PendingPopulation"):
                raise RuntimeError(f"DataVolume {name}: {status.get('phase')}: {status.get('conditions', [])}")
            condition = _fatal_condition(status)
            if condition is None:
                stuck_since = None
            else:
                # Quota pressure can clear on its own: a quota frees up, or a
                # preempted flash's claim finishes terminating. Only pressure that
                # persists for fatal_grace fails the flash; a shorter blip is
                # waited out.
                now = time.monotonic()
                if stuck_since is None:
                    stuck_since = now
                elif now - stuck_since >= self.fatal_grace:
                    raise RuntimeError(
                        f"DataVolume {name} cannot be provisioned: {condition.get('reason')}: "
                        f"{condition.get('message')} ({status.get('phase')})"
                    )
            await sleep(self.poll_interval)

    async def commit(self, operation_id):
        _, state = await self.vm.transaction(operation_id, {"staging", "committed", "complete"})
        if state["phase"] == "staging":
            for disk in state["disks"].values():
                dv = await self.vm.client.get(DataVolume, name=disk["new"], namespace=self.vm.namespace)
                if dv.get("status", {}).get("phase") != "Succeeded":
                    raise RuntimeError("Every disk must finish uploading before activation")
            # Power takes the same lock, so it cannot start the VM between the halt
            # below and the swap, and an activation never runs on a booted VM.
            async with self.vm.lock:
                await self._activate(operation_id, state)
        # A failed cleanup must not turn a successful disk activation into a failed flash.
        try:
            await self.recover(operation_id)
        except Exception:
            logger.warning("Disks activated; cleanup pending; the next flash or lease finishes it", exc_info=True)

    async def _activate(self, operation_id, state):
        logger.info("Stopping VM before activating disks")
        # Read the grace period before the halt starts deleting the VMI.
        vmi = await self._vmi()
        await self.vm.update(lambda vm: (self._halt(vm, operation_id, state), None))
        wait = self.shutdown_timeout
        if vmi is not None:
            grace = vmi["spec"].get("terminationGracePeriodSeconds")
            if isinstance(grace, int):
                wait = max(wait, grace + _SHUTDOWN_MARGIN)
        with fail_after(wait):
            while await self._vmi() is not None:
                await sleep(self.poll_interval)
        await self.vm.update(lambda vm: self._swap(vm, operation_id, state))

    def _halt(self, vm, operation_id, state):
        active(vm, operation_id, {"staging"})
        self._check_disks(vm, state, "old")
        return {"runStrategy": "Halted"}

    def _swap(self, vm, operation_id, state):
        current = active(vm, operation_id, {"staging"})
        self._check_disks(vm, current, "old")
        if vm["spec"].get("runStrategy") != "Halted":
            raise RuntimeError("VM was started during flash")
        updated = deepcopy(volumes(vm))
        for volume in updated:
            if volume["name"] in current["disks"]:
                volume["dataVolume"]["name"] = current["disks"][volume["name"]]["new"]
        old_names = {d["old"] for d in current["disks"].values()}
        templates = [t for t in vm["spec"].get("dataVolumeTemplates", []) if t["metadata"]["name"] not in old_names]
        return (
            {"template": {"spec": {"volumes": updated}}, "dataVolumeTemplates": templates},
            {**current, "phase": "committed"},
        )

    @staticmethod
    def _check_disks(vm, state, which):
        current = disk_names(volumes(vm))
        if any(current.get(target) != disk[which] for target, disk in state["disks"].items()):
            raise RuntimeError("VM disk references changed during flash; refusing to overwrite them")

    async def _vmi(self):
        try:
            return await self.vm.client.get(VirtualMachineInstance, name=self.vm.name, namespace=self.vm.namespace)
        except ApiError as exc:
            if exc.status.code != 404:
                raise
            return None

    async def recover(self, operation_id: str) -> bool:
        """Abort uncommitted work, or finish cleanup after a committed activation."""
        vm = await self.vm.get()
        state = operation(vm)
        if state is None:
            return False
        if state["id"] != operation_id:
            # The caller's operation was never written, or a newer flash preempted it:
            # its cleanup belongs to whoever wrote the operation the VM carries now.
            if state["phase"] in TERMINAL_PHASES:
                return False
            raise RuntimeError(SUPERSEDED)
        if state["phase"] in TERMINAL_PHASES:
            return state["phase"] == "complete"
        committed = state["phase"] == "committed"
        logger.info("Recovering storage operation %s (%s)", state["id"], state["phase"])
        if not committed:
            # Fence any in-flight commit before removing its staged volumes. update()
            # re-reads, so a commit that won the race is re-checked, not overwritten.
            vm = await self.vm.update(
                lambda vm: (None, {**active(vm, state["id"], {"staging", "aborting"}), "phase": "aborting"})
            )
        vmi = await self._vmi()
        in_use = references(volumes(vm))
        candidates = {d["old" if committed else "new"] for d in state["disks"].values()}
        if vmi and (candidates - in_use) & references(vmi["spec"].get("volumes", [])):
            raise RuntimeError(
                "A running VMI still uses a superseded disk; cleanup resumes on the next flash or lease once it stops"
            )
        with fail_after(self.shutdown_timeout):
            # Release every volume at once: each wait covers a claim held by a
            # terminating upload pod, so doing the volumes one at a time would
            # make the waits add up across disks.
            await self._delete_all_owned(candidates - in_use, vm.metadata.uid)
        final = "complete" if committed else "aborted"
        await self.vm.update(lambda vm: (None, {**active(vm, state["id"], {"committed", "aborting"}), "phase": final}))
        return committed

    async def _delete_all_owned(self, names, vm_uid):
        """Delete every volume, letting the others finish when one fails, then raise the first failure."""
        failures: dict[str, Exception] = {}

        async def delete(name):
            try:
                await self._delete_owned(name, vm_uid)
            except Exception as error:  # noqa: BLE001 - re-raised once every deletion has finished
                failures[name] = error

        names = list(names)
        async with create_task_group() as group:
            for name in names:
                group.start_soon(delete, name)
        for name in names:
            if name in failures:
                raise failures[name]

    async def _delete_owned(self, name, vm_uid):
        try:
            dv = await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
        except ApiError as exc:
            if exc.status.code != 404:
                raise
            return
        owners = (dv.metadata.ownerReferences if dv.metadata else None) or []
        if not any(o.uid == vm_uid and o.kind == "VirtualMachine" for o in owners):
            return
        claim = dv.get("status", {}).get("claimName") or name
        try:
            await self.vm.client.delete(DataVolume, name=name, namespace=self.vm.namespace)
        except ApiError as exc:
            # A racing cleanup (a shielded discard against the next session's
            # reclaim) may have deleted it first; "already gone" is done.
            if exc.status.code != 404:
                raise
        # Wait for the claim as well: one held by a terminating upload pod still
        # counts against the namespace quota, and the replacement volumes a
        # preempting flash creates would not bind while it exists.
        for kind, object_name in ((DataVolume, name), (PersistentVolumeClaim, claim)):
            while True:
                try:
                    await self.vm.client.get(kind, name=object_name, namespace=self.vm.namespace)
                except ApiError as exc:
                    if exc.status.code != 404:
                        raise
                    break
                await sleep(self.poll_interval)
