"""Auto-generated gRPC servicer adapter for StorageMuxInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import storage_mux_pb2, storage_mux_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.storage_mux.v1.StorageMuxInterface"


def _register():
    """Register the StorageMuxInterface servicer adapter."""
    from jumpstarter_driver_opendal.driver import StorageMuxInterface

    register_servicer_adapter(
        interface_class=StorageMuxInterface,
        service_name=SERVICE_NAME,
        servicer_factory=StorageMuxInterfaceServicer,
        add_to_server=storage_mux_pb2_grpc.add_StorageMuxInterfaceServicer_to_server,
    )


class StorageMuxInterfaceServicer(storage_mux_pb2_grpc.StorageMuxInterfaceServicer):
    """gRPC servicer that bridges StorageMuxInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Dut(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.dut):
            await driver.dut()
        else:
            driver.dut()
        return Empty()

    async def Host(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.host):
            await driver.host()
        else:
            driver.host()
        return Empty()

    async def Off(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.off):
            await driver.off()
        else:
            driver.off()
        return Empty()

    async def Read(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.read):
            await driver.read(request.dst)
        else:
            driver.read(request.dst)
        return Empty()

    async def Write(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.write):
            await driver.write(request.src)
        else:
            driver.write(request.src)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
