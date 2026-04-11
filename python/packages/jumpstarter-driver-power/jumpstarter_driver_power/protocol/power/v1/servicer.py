"""Auto-generated gRPC servicer adapter for PowerInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import power_pb2, power_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.power.v1.PowerInterface"


def _register():
    """Register the PowerInterface servicer adapter."""
    from jumpstarter_driver_power.driver import PowerInterface

    register_servicer_adapter(
        interface_class=PowerInterface,
        service_name=SERVICE_NAME,
        servicer_factory=PowerInterfaceServicer,
        add_to_server=power_pb2_grpc.add_PowerInterfaceServicer_to_server,
    )


class PowerInterfaceServicer(power_pb2_grpc.PowerInterfaceServicer):
    """gRPC servicer that bridges PowerInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Off(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.off):
            await driver.off()
        else:
            driver.off()
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
                yield power_pb2.PowerReading(voltage=item.voltage, current=item.current)
        else:
            for item in driver.read():
                yield power_pb2.PowerReading(voltage=item.voltage, current=item.current)


# Register the adapter at import time so the Session can discover it.
_register()
