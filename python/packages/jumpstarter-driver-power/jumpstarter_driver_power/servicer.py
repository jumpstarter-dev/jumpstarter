"""
Auto-generated gRPC servicer adapter for PowerInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from jumpstarter_driver_power.power.v1 import power_pb2, power_pb2_grpc

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
    """gRPC servicer that bridges PowerInterface proto service to @export driver methods.

    Each RPC resolves the target driver from gRPC metadata via DriverRegistry,
    then delegates to the driver's @export methods.
    """

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def On(self, request: Empty, context: grpc.aio.ServicerContext) -> Empty:
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.on):
            await driver.on()
        else:
            driver.on()
        return Empty()

    async def Off(self, request: Empty, context: grpc.aio.ServicerContext) -> Empty:
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.off):
            await driver.off()
        else:
            driver.off()
        return Empty()

    async def Read(self, request: Empty, context: grpc.aio.ServicerContext):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).read):
            async for reading in driver.read():
                yield power_pb2.PowerReading(
                    voltage=reading.voltage,
                    current=reading.current,
                )
        else:
            for reading in driver.read():
                yield power_pb2.PowerReading(
                    voltage=reading.voltage,
                    current=reading.current,
                )


# Register the adapter at import time so the Session can discover it.
_register()
