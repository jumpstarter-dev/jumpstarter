"""VM identity and conditional updates shared by power and storage."""

import asyncio
import json
from dataclasses import dataclass

from lightkube import AsyncClient
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import create_namespaced_resource
from lightkube.types import PatchType

VirtualMachine = create_namespaced_resource("kubevirt.io", "v1", "VirtualMachine", "virtualmachines")
VirtualMachineInstance = create_namespaced_resource(
    "kubevirt.io", "v1", "VirtualMachineInstance", "virtualmachineinstances"
)
DataVolume = create_namespaced_resource("cdi.kubevirt.io", "v1beta1", "DataVolume", "datavolumes")
UploadTokenRequest = create_namespaced_resource(
    "upload.cdi.kubevirt.io", "v1beta1", "UploadTokenRequest", "uploadtokenrequests"
)

FLASH_ANNOTATION = "jumpstarter.dev/storage-operation"

# A finished operation stays on the VM as a record, so it does not block anything.
TERMINAL_PHASES = ("complete", "aborted")

# One lock per VM, shared by every driver instance in this process. Power and the
# disk activation take it so that neither can interleave with the other; a process
# serving one VM from two exporter processes is unsupported.
_locks: dict[tuple[str, str], asyncio.Lock] = {}


def operation(vm):
    raw = (vm.metadata.annotations or {}).get(FLASH_ANNOTATION)
    return json.loads(raw) if raw else None


def active(vm, operation_id, phases):
    """The operation's state, provided it is the live one and in one of phases."""
    state = operation(vm)
    if not state or state["id"] != operation_id or state["phase"] not in phases:
        raise RuntimeError("Flash operation is no longer active; it was replaced or cleaned up")
    return state


@dataclass
class VMControl:
    client: AsyncClient
    namespace: str
    name: str
    retries: int = 5
    retry_delay: float = 0.1

    @property
    def lock(self) -> asyncio.Lock:
        return _locks.setdefault((self.namespace, self.name), asyncio.Lock())

    async def get(self):
        vm = await self.client.get(VirtualMachine, name=self.name, namespace=self.namespace)
        if not vm.metadata or not vm.metadata.uid or not vm.metadata.resourceVersion:
            raise RuntimeError("VM is missing its UID or resource version")
        if vm.metadata.deletionTimestamp:
            raise RuntimeError("VM is being deleted")
        return vm

    async def patch(self, vm, *, spec=None, state=None):
        # The resource version fences concurrent power, flash, and recovery calls.
        # The UID also prevents a request reaching a replacement VM of the same name.
        obj = {"metadata": {"uid": vm.metadata.uid, "resourceVersion": vm.metadata.resourceVersion}}
        if state is not None:
            obj["metadata"]["annotations"] = {FLASH_ANNOTATION: json.dumps(state)}
        if spec is not None:
            obj["spec"] = spec
        return await self.client.patch(
            VirtualMachine, name=self.name, namespace=self.namespace, obj=obj, patch_type=PatchType.MERGE
        )

    async def power(self, run_strategy):
        """Set runStrategy unconditionally, as the driver did before storage operations existed.

        Callers hold `lock`, so this cannot land between the halt that starts an
        activation and the swap that finishes it.
        """
        return await self.client.patch(
            VirtualMachine,
            name=self.name,
            namespace=self.namespace,
            obj={"spec": {"runStrategy": run_strategy}},
            patch_type=PatchType.MERGE,
        )

    async def update(self, change):
        """Apply change(vm) -> (spec, state), retrying when the VM changed underneath.

        virt-controller rewrites VM status on every VMI transition, so a conditional
        patch can lose a race at any point. change must be pure: it is re-run against a
        fresh read, so the ownership, phase and disk-reference checks it makes always
        see current state.
        """
        for attempt in range(self.retries):
            vm = await self.get()
            spec, state = change(vm)
            try:
                return await self.patch(vm, spec=spec, state=state)
            except ApiError as exc:
                if exc.status.code != 409 or attempt == self.retries - 1:
                    raise
                await asyncio.sleep(self.retry_delay)

    async def transaction(self, operation_id, phases):
        vm = await self.get()
        return vm, active(vm, operation_id, phases)
