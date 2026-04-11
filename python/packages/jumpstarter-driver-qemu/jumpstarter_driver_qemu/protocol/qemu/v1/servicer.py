"""Auto-generated gRPC servicer adapter for Qemu.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import qemu_pb2, qemu_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.qemu.v1.Qemu"


def _register():
    """Register the Qemu servicer adapter."""
    from jumpstarter_driver_qemu.driver import Qemu

    register_servicer_adapter(
        interface_class=Qemu,
        service_name=SERVICE_NAME,
        servicer_factory=QemuServicer,
        add_to_server=qemu_pb2_grpc.add_QemuServicer_to_server,
    )


class QemuServicer(qemu_pb2_grpc.QemuServicer):
    """gRPC servicer that bridges Qemu to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetHostname(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_hostname):
            result = await driver.get_hostname()
        else:
            result = driver.get_hostname()
        return qemu_pb2.GetHostnameResponse(value=result)

    async def GetPassword(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_password):
            result = await driver.get_password()
        else:
            result = driver.get_password()
        return qemu_pb2.GetPasswordResponse(value=result)

    async def GetUsername(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_username):
            result = await driver.get_username()
        else:
            result = driver.get_username()
        return qemu_pb2.GetUsernameResponse(value=result)

    async def SetDiskSize(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_disk_size):
            await driver.set_disk_size(request.size)
        else:
            driver.set_disk_size(request.size)
        return Empty()

    async def SetMemorySize(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_memory_size):
            await driver.set_memory_size(request.size)
        else:
            driver.set_memory_size(request.size)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
