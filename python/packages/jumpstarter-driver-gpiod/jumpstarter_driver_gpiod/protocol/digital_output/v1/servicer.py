"""Auto-generated gRPC servicer adapter for DigitalOutput.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import digital_output_pb2, digital_output_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.digital_output.v1.DigitalOutput"


def _register():
    """Register the DigitalOutput servicer adapter."""
    from jumpstarter_driver_gpiod.driver import DigitalOutput

    register_servicer_adapter(
        interface_class=DigitalOutput,
        service_name=SERVICE_NAME,
        servicer_factory=DigitalOutputServicer,
        add_to_server=digital_output_pb2_grpc.add_DigitalOutputServicer_to_server,
    )


class DigitalOutputServicer(digital_output_pb2_grpc.DigitalOutputServicer):
    """gRPC servicer that bridges DigitalOutput to @export driver methods."""

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


# Register the adapter at import time so the Session can discover it.
_register()
