"""Auto-generated gRPC servicer adapter for DigitalInput.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import digital_input_pb2, digital_input_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.digital_input.v1.DigitalInput"


def _register():
    """Register the DigitalInput servicer adapter."""
    from jumpstarter_driver_gpiod.driver import DigitalInput

    register_servicer_adapter(
        interface_class=DigitalInput,
        service_name=SERVICE_NAME,
        servicer_factory=DigitalInputServicer,
        add_to_server=digital_input_pb2_grpc.add_DigitalInputServicer_to_server,
    )


class DigitalInputServicer(digital_input_pb2_grpc.DigitalInputServicer):
    """gRPC servicer that bridges DigitalInput to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def WaitForActive(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.wait_for_active):
            await driver.wait_for_active(request.timeout)
        else:
            driver.wait_for_active(request.timeout)
        return Empty()

    async def WaitForEdge(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.wait_for_edge):
            await driver.wait_for_edge(request.edge_type, request.timeout)
        else:
            driver.wait_for_edge(request.edge_type, request.timeout)
        return Empty()

    async def WaitForInactive(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.wait_for_inactive):
            await driver.wait_for_inactive(request.timeout)
        else:
            driver.wait_for_inactive(request.timeout)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
