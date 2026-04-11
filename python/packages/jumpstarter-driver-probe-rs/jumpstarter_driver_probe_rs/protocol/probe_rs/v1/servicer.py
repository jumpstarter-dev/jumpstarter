"""Auto-generated gRPC servicer adapter for ProbeRs.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import probe_rs_pb2, probe_rs_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.probe_rs.v1.ProbeRs"


def _register():
    """Register the ProbeRs servicer adapter."""
    from jumpstarter_driver_probe_rs.driver import ProbeRs

    register_servicer_adapter(
        interface_class=ProbeRs,
        service_name=SERVICE_NAME,
        servicer_factory=ProbeRsServicer,
        add_to_server=probe_rs_pb2_grpc.add_ProbeRsServicer_to_server,
    )


class ProbeRsServicer(probe_rs_pb2_grpc.ProbeRsServicer):
    """gRPC servicer that bridges ProbeRs to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Download(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.download):
            result = await driver.download(request.src)
        else:
            result = driver.download(request.src)
        return probe_rs_pb2.DownloadResponse(value=result)

    async def Erase(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.erase):
            result = await driver.erase()
        else:
            result = driver.erase()
        return probe_rs_pb2.EraseResponse(value=result)

    async def Info(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.info):
            result = await driver.info()
        else:
            result = driver.info()
        return probe_rs_pb2.InfoResponse(value=result)

    async def Read(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.read):
            result = await driver.read(request.width, request.address, request.words)
        else:
            result = driver.read(request.width, request.address, request.words)
        return probe_rs_pb2.ReadResponse(value=result)

    async def ResetTarget(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.reset_target):
            result = await driver.reset_target()
        else:
            result = driver.reset_target()
        return probe_rs_pb2.ResetTargetResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
