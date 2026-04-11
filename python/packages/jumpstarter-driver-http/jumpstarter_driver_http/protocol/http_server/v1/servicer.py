"""Auto-generated gRPC servicer adapter for HttpServer.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import http_server_pb2, http_server_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.http_server.v1.HttpServer"


def _register():
    """Register the HttpServer servicer adapter."""
    from jumpstarter_driver_http.driver import HttpServer

    register_servicer_adapter(
        interface_class=HttpServer,
        service_name=SERVICE_NAME,
        servicer_factory=HttpServerServicer,
        add_to_server=http_server_pb2_grpc.add_HttpServerServicer_to_server,
    )


class HttpServerServicer(http_server_pb2_grpc.HttpServerServicer):
    """gRPC servicer that bridges HttpServer to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetHost(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_host):
            result = await driver.get_host()
        else:
            result = driver.get_host()
        return http_server_pb2.GetHostResponse(value=result)

    async def GetPort(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_port):
            result = await driver.get_port()
        else:
            result = driver.get_port()
        return http_server_pb2.GetPortResponse(value=result)

    async def GetUrl(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_url):
            result = await driver.get_url()
        else:
            result = driver.get_url()
        return http_server_pb2.GetUrlResponse(value=result)

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
