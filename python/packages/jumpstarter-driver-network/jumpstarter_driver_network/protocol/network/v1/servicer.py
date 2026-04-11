"""Auto-generated gRPC servicer adapter for NetworkInterface.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction

import grpc
from google.protobuf.empty_pb2 import Empty

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
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )


# Register the adapter at import time so the Session can discover it.
_register()
