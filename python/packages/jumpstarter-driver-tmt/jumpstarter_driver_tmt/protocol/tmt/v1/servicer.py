"""Auto-generated gRPC servicer adapter for TMT.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import tmt_pb2, tmt_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.tmt.v1.TMT"


def _register():
    """Register the TMT servicer adapter."""
    from jumpstarter_driver_tmt.driver import TMT

    register_servicer_adapter(
        interface_class=TMT,
        service_name=SERVICE_NAME,
        servicer_factory=TMTServicer,
        add_to_server=tmt_pb2_grpc.add_TMTServicer_to_server,
    )


class TMTServicer(tmt_pb2_grpc.TMTServicer):
    """gRPC servicer that bridges TMT to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetDefaultUserPass(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_default_user_pass):
            result = await driver.get_default_user_pass()
        else:
            result = driver.get_default_user_pass()
        return tmt_pb2.GetDefaultUserPassResponse(value=result)

    async def GetRebootCmd(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_reboot_cmd):
            result = await driver.get_reboot_cmd()
        else:
            result = driver.get_reboot_cmd()
        return tmt_pb2.GetRebootCmdResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
