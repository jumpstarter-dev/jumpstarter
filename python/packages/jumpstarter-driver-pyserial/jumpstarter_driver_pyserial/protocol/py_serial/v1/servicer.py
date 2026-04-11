"""Auto-generated gRPC servicer adapter for PySerial.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import py_serial_pb2, py_serial_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.py_serial.v1.PySerial"


def _register():
    """Register the PySerial servicer adapter."""
    from jumpstarter_driver_pyserial.driver import PySerial

    register_servicer_adapter(
        interface_class=PySerial,
        service_name=SERVICE_NAME,
        servicer_factory=PySerialServicer,
        add_to_server=py_serial_pb2_grpc.add_PySerialServicer_to_server,
    )


class PySerialServicer(py_serial_pb2_grpc.PySerialServicer):
    """gRPC servicer that bridges PySerial to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )

    async def SetDtr(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_dtr):
            await driver.set_dtr(request.value)
        else:
            driver.set_dtr(request.value)
        return Empty()

    async def SetRts(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_rts):
            await driver.set_rts(request.value)
        else:
            driver.set_rts(request.value)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
