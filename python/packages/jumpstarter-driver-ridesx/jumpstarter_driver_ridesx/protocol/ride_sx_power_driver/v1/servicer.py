"""Auto-generated gRPC servicer adapter for RideSXPowerDriver.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import ride_sx_power_driver_pb2, ride_sx_power_driver_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.ride_sx_power_driver.v1.RideSXPowerDriver"


def _register():
    """Register the RideSXPowerDriver servicer adapter."""
    from jumpstarter_driver_ridesx.driver import RideSXPowerDriver

    register_servicer_adapter(
        interface_class=RideSXPowerDriver,
        service_name=SERVICE_NAME,
        servicer_factory=RideSXPowerDriverServicer,
        add_to_server=ride_sx_power_driver_pb2_grpc.add_RideSXPowerDriverServicer_to_server,
    )


class RideSXPowerDriverServicer(ride_sx_power_driver_pb2_grpc.RideSXPowerDriverServicer):
    """gRPC servicer that bridges RideSXPowerDriver to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Cycle(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.cycle):
            await driver.cycle(request.delay)
        else:
            driver.cycle(request.delay)
        return Empty()

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

    async def Rescue(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.rescue):
            await driver.rescue()
        else:
            driver.rescue()
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
