"""Auto-generated gRPC servicer adapter for UStreamer.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import u_streamer_pb2, u_streamer_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.u_streamer.v1.UStreamer"


def _register():
    """Register the UStreamer servicer adapter."""
    from jumpstarter_driver_ustreamer.driver import UStreamer

    register_servicer_adapter(
        interface_class=UStreamer,
        service_name=SERVICE_NAME,
        servicer_factory=UStreamerServicer,
        add_to_server=u_streamer_pb2_grpc.add_UStreamerServicer_to_server,
    )


class UStreamerServicer(u_streamer_pb2_grpc.UStreamerServicer):
    """gRPC servicer that bridges UStreamer to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )

    async def Snapshot(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.snapshot):
            result = await driver.snapshot()
        else:
            result = driver.snapshot()
        return u_streamer_pb2.SnapshotResponse(value=result)

    async def State(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.state):
            result = await driver.state()
        else:
            result = driver.state()
        return u_streamer_pb2.UStreamerState(ok=result.ok, result=result.result)


# Register the adapter at import time so the Session can discover it.
_register()
