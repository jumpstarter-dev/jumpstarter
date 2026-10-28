"""Stage CDI disks, then activate the whole set with one conditional VM update."""

import asyncio
import logging
import ssl
from copy import deepcopy
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

import aiohttp
from lightkube.core.exceptions import ApiError
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.resources.core_v1 import PersistentVolumeClaim
from lightkube.resources.storage_v1 import StorageClass
from lightkube.utils.quantity import parse_quantity

from .vm import DataVolume, UploadTokenRequest, VirtualMachineInstance, VMControl, operation, require_idle

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


@dataclass
class Flash:
    vm: VMControl
    disks: dict[str, Disk]
    upload_proxy_url: str
    upload_ca: str = ""
    timeout: float = 1800
    shutdown_timeout: float = 120
    poll_interval: float = 1

    async def begin(self, targets: list[str]) -> str:
        if not targets or len(set(targets)) != len(targets):
            raise ValueError("Specify distinct disk targets")
        unknown = set(targets) - self.disks.keys()
        if unknown:
            raise ValueError(f"Unknown or non-flashable disk targets: {', '.join(sorted(unknown))}")
        url = urlsplit(self.upload_proxy_url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Configure upload_proxy_url as an HTTPS CDI upload proxy URL")
        ssl.create_default_context(cadata=self.upload_ca or None)
        vm = await self.vm.get()
        require_idle(vm)
        if operation(vm) and operation(vm)["phase"] == "committed":
            await self.recover(operation(vm)["id"])
            vm = await self.vm.get()
            require_idle(vm)
        current = disk_names(volumes(vm))
        state = {"id": uuid4().hex, "phase": "staging", "disks": {}}
        for target in targets:
            if target not in current:
                raise ValueError(f"Disk {target!r} must use a DataVolume to support flashing")
            storage = await self._storage(target, current[target])
            state["disks"][target] = {
                "old": current[target],
                "new": f"{self.vm.name[:20]}-{target[:12]}-{uuid4().hex[:20]}",
                "storage": storage,
            }
        await self.vm.patch(vm, state=state)
        return state["id"]

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

    async def prepare(self, operation_id, target):
        """Provision the upload pod before the client opens a short-lived resource URL."""
        async with asyncio.timeout(self.timeout):
            vm, disk = await self._target(operation_id, target)
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
            await self._wait_volume(disk["new"], "UploadReady")
            await self.vm.transaction(operation_id, {"staging"})

    async def stage(self, operation_id, target, resource):
        async with asyncio.timeout(self.timeout):
            _, disk = await self._target(operation_id, target)
            dv = await self.vm.client.get(DataVolume, name=disk["new"], namespace=self.vm.namespace)
            if dv.get("status", {}).get("phase") != "UploadReady":
                raise RuntimeError(f"Disk {target!r} is not ready for upload")
            token = await self.vm.client.create(
                UploadTokenRequest(
                    metadata=ObjectMeta(generateName="jumpstarter-", namespace=self.vm.namespace),
                    spec={"pvcName": disk["new"]},
                )
            )
            logger.info("Uploading disk %s", target)
            async with resource() as stream:
                await self._upload(stream, token["status"]["token"])
            await self._wait_volume(disk["new"], "Succeeded")
            await self.vm.transaction(operation_id, {"staging"})

    async def _upload(self, stream, token):
        context = ssl.create_default_context(cadata=self.upload_ca or None)
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.timeout)) as session,
            session.post(
                self.upload_proxy_url.rstrip("/") + "/v1beta1/upload-async",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
                data=stream,
                ssl=context,
                allow_redirects=False,
            ) as response,
        ):
            if response.status != 200:
                raise RuntimeError(f"CDI upload failed with HTTP {response.status}")

    async def _wait_volume(self, name, phase):
        while True:
            dv = await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
            status = dv.get("status", {})
            if status.get("phase") == phase:
                return
            if status.get("phase") in ("Failed", "WaitForFirstConsumer", "PendingPopulation"):
                raise RuntimeError(f"DataVolume {name}: {status.get('phase')}: {status.get('conditions', [])}")
            await asyncio.sleep(self.poll_interval)

    async def commit(self, operation_id):
        vm, state = await self.vm.transaction(operation_id, {"staging", "committed", "complete"})
        if state["phase"] == "staging":
            self._check_disks(vm, state, "old")
            for disk in state["disks"].values():
                dv = await self.vm.client.get(DataVolume, name=disk["new"], namespace=self.vm.namespace)
                if dv.get("status", {}).get("phase") != "Succeeded":
                    raise RuntimeError("Every disk must finish uploading before activation")
            logger.info("Stopping VM before activating disks")
            await self.vm.patch(vm, spec={"runStrategy": "Halted"})
            async with asyncio.timeout(self.shutdown_timeout):
                while await self._vmi() is not None:
                    await asyncio.sleep(self.poll_interval)
            vm, state = await self.vm.transaction(operation_id, {"staging"})
            self._check_disks(vm, state, "old")
            if vm["spec"].get("runStrategy") != "Halted":
                raise RuntimeError("VM was started during flash")
            updated = deepcopy(volumes(vm))
            for volume in updated:
                if volume["name"] in state["disks"]:
                    volume["dataVolume"]["name"] = state["disks"][volume["name"]]["new"]
            old_names = {d["old"] for d in state["disks"].values()}
            templates = [t for t in vm["spec"].get("dataVolumeTemplates", []) if t["metadata"]["name"] not in old_names]
            state["phase"] = "committed"
            await self.vm.patch(
                vm,
                spec={
                    "template": {"spec": {"volumes": updated}},
                    "dataVolumeTemplates": templates,
                },
                state=state,
            )
        # A failed cleanup must not turn a successful disk activation into a failed flash.
        try:
            await self.recover(operation_id)
        except Exception:
            logger.warning("Disks activated; cleanup pending. Run 'j storage recover' to retry", exc_info=True)

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

    async def recover(self, operation_id=None) -> bool:
        """Abort uncommitted work, or finish cleanup after a committed activation."""
        vm = await self.vm.get()
        state = operation(vm)
        if state is None:
            return False
        if state["phase"] == "destroying":
            raise RuntimeError("VM deletion is pending; retry power.off(destroy=True)")
        if operation_id is not None and state["id"] != operation_id:
            raise RuntimeError("A different flash operation is active")
        if state["phase"] in ("complete", "aborted"):
            return state["phase"] == "complete"
        committed = state["phase"] == "committed"
        self._check_disks(vm, state, "new" if committed else "old")
        if not committed:
            # Fence any in-flight commit before removing its staged volumes.
            state["phase"] = "aborting"
            vm = await self.vm.patch(vm, state=state)
        vmi = await self._vmi()
        in_use = references(volumes(vm))
        candidates = {d["old" if committed else "new"] for d in state["disks"].values()}
        if vmi and (candidates - in_use) & references(vmi["spec"].get("volumes", [])):
            raise RuntimeError("A running VMI still uses a superseded disk; retry recovery after shutdown")
        async with asyncio.timeout(self.shutdown_timeout):
            for name in candidates - in_use:
                await self._delete_owned(name, vm.metadata.uid)
        vm, state = await self.vm.transaction(state["id"], {"committed", "aborting"})
        state["phase"] = "complete" if committed else "aborted"
        await self.vm.patch(vm, state=state)
        return committed

    async def _delete_owned(self, name, vm_uid):
        try:
            dv = await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
            owners = dv.metadata.ownerReferences if dv.metadata else []
            if not any(o.uid == vm_uid and o.kind == "VirtualMachine" for o in owners or []):
                return
            await self.vm.client.delete(DataVolume, name=name, namespace=self.vm.namespace)
            while True:
                await self.vm.client.get(DataVolume, name=name, namespace=self.vm.namespace)
                await asyncio.sleep(self.poll_interval)
        except ApiError as exc:
            if exc.status.code != 404:
                raise
