"""Auto-generated gRPC servicer adapter for Can.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import can_pb2, can_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.can.v1.Can"


def _register():
    """Register the Can servicer adapter."""
    from jumpstarter_driver_can.driver import Can

    register_servicer_adapter(
        interface_class=Can,
        service_name=SERVICE_NAME,
        servicer_factory=CanServicer,
        add_to_server=can_pb2_grpc.add_CanServicer_to_server,
    )


class CanServicer(can_pb2_grpc.CanServicer):
    """gRPC servicer that bridges Can to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def ChannelInfo(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.channel_info):
            result = await driver.channel_info()
        else:
            result = driver.channel_info()
        return can_pb2.ChannelInfoResponse(value=result)

    async def FlushTxBuffer(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flush_tx_buffer):
            await driver.flush_tx_buffer()
        else:
            driver.flush_tx_buffer()
        return Empty()

    async def Protocol(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.protocol):
            result = await driver.protocol()
        else:
            result = driver.protocol()
        return can_pb2.ProtocolResponse(value=result)

    async def Send(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.send):
            await driver.send(request.msg, request.timeout)
        else:
            driver.send(request.msg, request.timeout)
        return Empty()

    async def Shutdown(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.shutdown):
            await driver.shutdown()
        else:
            driver.shutdown()
        return Empty()

    async def State(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.state):
            result = await driver.state(request.value)
        else:
            result = driver.state(request.value)
        return can_pb2.StateResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
