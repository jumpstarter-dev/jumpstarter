"""Auto-generated gRPC servicer adapter for FlasherInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import flasher_pb2, flasher_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.flasher.v1.FlasherInterface"


def _register():
    """Register the FlasherInterface servicer adapter."""
    from jumpstarter_driver_opendal.driver import FlasherInterface

    register_servicer_adapter(
        interface_class=FlasherInterface,
        service_name=SERVICE_NAME,
        servicer_factory=FlasherInterfaceServicer,
        add_to_server=flasher_pb2_grpc.add_FlasherInterfaceServicer_to_server,
    )


class FlasherInterfaceServicer(flasher_pb2_grpc.FlasherInterfaceServicer):
    """gRPC servicer that bridges FlasherInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Dump(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.dump):
            await driver.dump(request.target, request.partition)
        else:
            driver.dump(request.target, request.partition)
        return Empty()

    async def Flash(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flash):
            await driver.flash(request.source, request.target)
        else:
            driver.flash(request.source, request.target)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
