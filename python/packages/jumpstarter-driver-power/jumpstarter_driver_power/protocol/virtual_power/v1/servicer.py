"""Auto-generated gRPC servicer adapter for VirtualPowerInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import virtual_power_pb2, virtual_power_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.virtual_power.v1.VirtualPowerInterface"


def _register():
    """Register the VirtualPowerInterface servicer adapter."""
    from jumpstarter_driver_power.driver import VirtualPowerInterface

    register_servicer_adapter(
        interface_class=VirtualPowerInterface,
        service_name=SERVICE_NAME,
        servicer_factory=VirtualPowerInterfaceServicer,
        add_to_server=virtual_power_pb2_grpc.add_VirtualPowerInterfaceServicer_to_server,
    )


class VirtualPowerInterfaceServicer(virtual_power_pb2_grpc.VirtualPowerInterfaceServicer):
    """gRPC servicer that bridges VirtualPowerInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Off(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.off):
            await driver.off(request.destroy)
        else:
            driver.off(request.destroy)
        return Empty()

    async def On(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.on):
            await driver.on()
        else:
            driver.on()
        return Empty()

    async def Read(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).read):
            async for item in driver.read():
                yield virtual_power_pb2.PowerReading(voltage=item.voltage, current=item.current)
        else:
            for item in driver.read():
                yield virtual_power_pb2.PowerReading(voltage=item.voltage, current=item.current)


# Register the adapter at import time so the Session can discover it.
_register()
