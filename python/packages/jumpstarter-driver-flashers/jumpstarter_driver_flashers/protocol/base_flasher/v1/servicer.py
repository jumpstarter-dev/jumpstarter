"""Auto-generated gRPC servicer adapter for BaseFlasher.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import base_flasher_pb2, base_flasher_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.base_flasher.v1.BaseFlasher"


def _register():
    """Register the BaseFlasher servicer adapter."""
    from jumpstarter_driver_flashers.driver import BaseFlasher

    register_servicer_adapter(
        interface_class=BaseFlasher,
        service_name=SERVICE_NAME,
        servicer_factory=BaseFlasherServicer,
        add_to_server=base_flasher_pb2_grpc.add_BaseFlasherServicer_to_server,
    )


class BaseFlasherServicer(base_flasher_pb2_grpc.BaseFlasherServicer):
    """gRPC servicer that bridges BaseFlasher to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetBootcmd(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_bootcmd):
            result = await driver.get_bootcmd()
        else:
            result = driver.get_bootcmd()
        return base_flasher_pb2.GetBootcmdResponse(value=result)

    async def GetDefaultTarget(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_default_target):
            result = await driver.get_default_target()
        else:
            result = driver.get_default_target()
        return base_flasher_pb2.GetDefaultTargetResponse(value=result)

    async def GetDtbAddress(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_dtb_address):
            result = await driver.get_dtb_address()
        else:
            result = driver.get_dtb_address()
        return base_flasher_pb2.GetDtbAddressResponse(value=result)

    async def GetDtbFilename(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_dtb_filename):
            result = await driver.get_dtb_filename()
        else:
            result = driver.get_dtb_filename()
        return base_flasher_pb2.GetDtbFilenameResponse(value=result)

    async def GetFlasherManifestYaml(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_flasher_manifest_yaml):
            result = await driver.get_flasher_manifest_yaml()
        else:
            result = driver.get_flasher_manifest_yaml()
        return base_flasher_pb2.GetFlasherManifestYamlResponse(value=result)

    async def GetInitramAddress(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_initram_address):
            result = await driver.get_initram_address()
        else:
            result = driver.get_initram_address()
        return base_flasher_pb2.GetInitramAddressResponse(value=result)

    async def GetInitramFilename(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_initram_filename):
            result = await driver.get_initram_filename()
        else:
            result = driver.get_initram_filename()
        return base_flasher_pb2.GetInitramFilenameResponse(value=result)

    async def GetKernelAddress(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_kernel_address):
            result = await driver.get_kernel_address()
        else:
            result = driver.get_kernel_address()
        return base_flasher_pb2.GetKernelAddressResponse(value=result)

    async def GetKernelFilename(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_kernel_filename):
            result = await driver.get_kernel_filename()
        else:
            result = driver.get_kernel_filename()
        return base_flasher_pb2.GetKernelFilenameResponse(value=result)

    async def SetDtb(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_dtb):
            await driver.set_dtb(request.handle)
        else:
            driver.set_dtb(request.handle)
        return Empty()

    async def SetupFlasherBundle(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.setup_flasher_bundle):
            await driver.setup_flasher_bundle(request.force_flash_bundle)
        else:
            driver.setup_flasher_bundle(request.force_flash_bundle)
        return Empty()

    async def UseDtbVariant(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.use_dtb_variant):
            await driver.use_dtb_variant(request.variant)
        else:
            driver.use_dtb_variant(request.variant)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
