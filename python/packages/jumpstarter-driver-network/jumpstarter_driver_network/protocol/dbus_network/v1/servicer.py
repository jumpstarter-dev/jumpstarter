"""Auto-generated gRPC servicer adapter for DbusNetwork.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import dbus_network_pb2, dbus_network_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.dbus_network.v1.DbusNetwork"


def _register():
    """Register the DbusNetwork servicer adapter."""
    from jumpstarter_driver_network.driver import DbusNetwork

    register_servicer_adapter(
        interface_class=DbusNetwork,
        service_name=SERVICE_NAME,
        servicer_factory=DbusNetworkServicer,
        add_to_server=dbus_network_pb2_grpc.add_DbusNetworkServicer_to_server,
    )


class DbusNetworkServicer(dbus_network_pb2_grpc.DbusNetworkServicer):
    """gRPC servicer that bridges DbusNetwork to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Connect(self, request_iterator, context):
        await context.abort(
            grpc.StatusCode.UNIMPLEMENTED,
            "Connect uses RouterService.Stream, not native gRPC",
        )


# Register the adapter at import time so the Session can discover it.
_register()
