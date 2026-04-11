"""Auto-generated gRPC servicer adapter for AdbServer.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import adb_pb2, adb_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.adb.v1.AdbServer"


def _register():
    """Register the AdbServer servicer adapter."""
    from jumpstarter_driver_adb.driver import AdbServer

    register_servicer_adapter(
        interface_class=AdbServer,
        service_name=SERVICE_NAME,
        servicer_factory=AdbServerServicer,
        add_to_server=adb_pb2_grpc.add_AdbServerServicer_to_server,
    )


class AdbServerServicer(adb_pb2_grpc.AdbServerServicer):
    """gRPC servicer that bridges AdbServer to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def KillServer(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.kill_server):
            result = await driver.kill_server()
        else:
            result = driver.kill_server()
        return adb_pb2.KillServerResponse(value=result)

    async def ListDevices(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.list_devices):
            result = await driver.list_devices()
        else:
            result = driver.list_devices()
        return adb_pb2.ListDevicesResponse(value=result)

    async def StartServer(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.start_server):
            result = await driver.start_server()
        else:
            result = driver.start_server()
        return adb_pb2.StartServerResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
