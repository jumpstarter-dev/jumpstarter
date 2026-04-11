"""Auto-generated gRPC servicer adapter for Shell.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import shell_pb2, shell_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.shell.v1.Shell"


def _register():
    """Register the Shell servicer adapter."""
    from jumpstarter_driver_shell.driver import Shell

    register_servicer_adapter(
        interface_class=Shell,
        service_name=SERVICE_NAME,
        servicer_factory=ShellServicer,
        add_to_server=shell_pb2_grpc.add_ShellServicer_to_server,
    )


class ShellServicer(shell_pb2_grpc.ShellServicer):
    """gRPC servicer that bridges Shell to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def CallMethod(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).call_method):
            async for item in driver.call_method(request.method, request.env, request.args):
                yield shell_pb2.CallMethodResponse(value=item.value)
        else:
            for item in driver.call_method(request.method, request.env, request.args):
                yield shell_pb2.CallMethodResponse(value=item.value)

    async def GetMethods(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_methods):
            result = await driver.get_methods()
        else:
            result = driver.get_methods()
        return shell_pb2.GetMethodsResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
