"""Auto-generated gRPC servicer adapter for NetworkInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction

import grpc
import anyio

from . import network_pb2, network_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.network.v1.NetworkInterface"


def _register():
    """Register the NetworkInterface servicer adapter."""
    from jumpstarter_driver_network.driver import NetworkInterface

    register_servicer_adapter(
        interface_class=NetworkInterface,
        service_name=SERVICE_NAME,
        servicer_factory=NetworkInterfaceServicer,
        add_to_server=network_pb2_grpc.add_NetworkInterfaceServicer_to_server,
    )


class NetworkInterfaceServicer(network_pb2_grpc.NetworkInterfaceServicer):
    """gRPC servicer that bridges NetworkInterface to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        async with driver.connect() as stream:
            async def _inbound():
                async for msg in request_iterator:
                    await stream.send(msg.payload)
                await stream.send_eof()
            async with anyio.create_task_group() as tg:
                tg.start_soon(_inbound)
                try:
                    while True:
                        data = await stream.receive()
                        yield network_pb2.StreamData(payload=data)
                except (anyio.EndOfStream, anyio.ClosedResourceError):
                    pass
                tg.cancel_scope.cancel()


# Register the adapter at import time so the Session can discover it.
_register()
