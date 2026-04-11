"""Auto-generated gRPC servicer adapter for Vnc.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import vnc_pb2, vnc_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.vnc.v1.Vnc"


def _register():
    """Register the Vnc servicer adapter."""
    from jumpstarter_driver_vnc.driver import Vnc

    register_servicer_adapter(
        interface_class=Vnc,
        service_name=SERVICE_NAME,
        servicer_factory=VncServicer,
        add_to_server=vnc_pb2_grpc.add_VncServicer_to_server,
    )


class VncServicer(vnc_pb2_grpc.VncServicer):
    """gRPC servicer that bridges Vnc to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetDefaultEncrypt(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_default_encrypt):
            result = await driver.get_default_encrypt()
        else:
            result = driver.get_default_encrypt()
        return vnc_pb2.GetDefaultEncryptResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
