"""Auto-generated gRPC servicer adapter for NoyitoPowerSerial.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import noyito_power_serial_pb2, noyito_power_serial_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.noyito_power_serial.v1.NoyitoPowerSerial"


def _register():
    """Register the NoyitoPowerSerial servicer adapter."""
    from jumpstarter_driver_noyito_relay.driver import NoyitoPowerSerial

    register_servicer_adapter(
        interface_class=NoyitoPowerSerial,
        service_name=SERVICE_NAME,
        servicer_factory=NoyitoPowerSerialServicer,
        add_to_server=noyito_power_serial_pb2_grpc.add_NoyitoPowerSerialServicer_to_server,
    )


class NoyitoPowerSerialServicer(noyito_power_serial_pb2_grpc.NoyitoPowerSerialServicer):
    """gRPC servicer that bridges NoyitoPowerSerial to @export driver methods."""

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
                yield noyito_power_serial_pb2.PowerReading(voltage=item.voltage, current=item.current)
        else:
            for item in driver.read():
                yield noyito_power_serial_pb2.PowerReading(voltage=item.voltage, current=item.current)

    async def Status(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.status):
            result = await driver.status()
        else:
            result = driver.status()
        return noyito_power_serial_pb2.StatusResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
