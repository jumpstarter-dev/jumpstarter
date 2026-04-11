"""Auto-generated gRPC servicer adapter for Tftp.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import tftp_pb2, tftp_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.tftp.v1.Tftp"


def _register():
    """Register the Tftp servicer adapter."""
    from jumpstarter_driver_tftp.driver import Tftp

    register_servicer_adapter(
        interface_class=Tftp,
        service_name=SERVICE_NAME,
        servicer_factory=TftpServicer,
        add_to_server=tftp_pb2_grpc.add_TftpServicer_to_server,
    )


class TftpServicer(tftp_pb2_grpc.TftpServicer):
    """gRPC servicer that bridges Tftp to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetHost(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_host):
            result = await driver.get_host()
        else:
            result = driver.get_host()
        return tftp_pb2.GetHostResponse(value=result)

    async def GetPort(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_port):
            result = await driver.get_port()
        else:
            result = driver.get_port()
        return tftp_pb2.GetPortResponse(value=result)

    async def Start(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.start):
            await driver.start()
        else:
            driver.start()
        return Empty()

    async def Stop(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.stop):
            await driver.stop()
        else:
            driver.stop()
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
