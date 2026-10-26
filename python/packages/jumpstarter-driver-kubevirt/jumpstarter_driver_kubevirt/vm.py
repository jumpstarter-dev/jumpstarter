"""VM identity and conditional updates shared by power and storage."""

import json
from dataclasses import dataclass

from lightkube import AsyncClient
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


def operation(vm):
    raw = (vm.metadata.annotations or {}).get(FLASH_ANNOTATION)
    return json.loads(raw) if raw else None


def require_idle(vm):
    pending = operation(vm)
    if pending and pending["phase"] not in ("complete", "aborted", "committed"):
        raise RuntimeError("VM has an unfinished storage operation; finish it or run 'j storage recover'")


@dataclass
class VMControl:
    client: AsyncClient
    namespace: str
    name: str

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

    async def transaction(self, operation_id, phases):
        vm = await self.get()
        state = operation(vm)
        if not state or state["id"] != operation_id or state["phase"] not in phases:
            raise RuntimeError("Flash operation is no longer active; start a new flash")
        return vm, state
