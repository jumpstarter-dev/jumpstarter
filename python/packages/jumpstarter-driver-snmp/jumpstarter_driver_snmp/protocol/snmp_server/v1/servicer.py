"""Auto-generated gRPC servicer adapter for SNMPServer.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import snmp_server_pb2, snmp_server_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.snmp_server.v1.SNMPServer"


def _register():
    """Register the SNMPServer servicer adapter."""
    from jumpstarter_driver_snmp.driver import SNMPServer

    register_servicer_adapter(
        interface_class=SNMPServer,
        service_name=SERVICE_NAME,
        servicer_factory=SNMPServerServicer,
        add_to_server=snmp_server_pb2_grpc.add_SNMPServerServicer_to_server,
    )


class SNMPServerServicer(snmp_server_pb2_grpc.SNMPServerServicer):
    """gRPC servicer that bridges SNMPServer to @export driver methods."""

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
