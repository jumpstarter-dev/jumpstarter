"""Auto-generated gRPC servicer adapter for BleWriteNotifyStream.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import ble_write_notify_stream_pb2, ble_write_notify_stream_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.ble_write_notify_stream.v1.BleWriteNotifyStream"


def _register():
    """Register the BleWriteNotifyStream servicer adapter."""
    from jumpstarter_driver_ble.driver import BleWriteNotifyStream

    register_servicer_adapter(
        interface_class=BleWriteNotifyStream,
        service_name=SERVICE_NAME,
        servicer_factory=BleWriteNotifyStreamServicer,
        add_to_server=ble_write_notify_stream_pb2_grpc.add_BleWriteNotifyStreamServicer_to_server,
    )


class BleWriteNotifyStreamServicer(ble_write_notify_stream_pb2_grpc.BleWriteNotifyStreamServicer):
    """gRPC servicer that bridges BleWriteNotifyStream to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )

    async def Info(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.info):
            result = await driver.info()
        else:
            result = driver.info()
        return ble_write_notify_stream_pb2.InfoResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
