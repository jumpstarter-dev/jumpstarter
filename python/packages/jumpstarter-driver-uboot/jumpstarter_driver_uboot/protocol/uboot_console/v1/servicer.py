"""Auto-generated gRPC servicer adapter for UbootConsole.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import uboot_console_pb2, uboot_console_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.uboot_console.v1.UbootConsole"


def _register():
    """Register the UbootConsole servicer adapter."""
    from jumpstarter_driver_uboot.driver import UbootConsole

    register_servicer_adapter(
        interface_class=UbootConsole,
        service_name=SERVICE_NAME,
        servicer_factory=UbootConsoleServicer,
        add_to_server=uboot_console_pb2_grpc.add_UbootConsoleServicer_to_server,
    )


class UbootConsoleServicer(uboot_console_pb2_grpc.UbootConsoleServicer):
    """gRPC servicer that bridges UbootConsole to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def GetPrompt(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_prompt):
            result = await driver.get_prompt()
        else:
            result = driver.get_prompt()
        return uboot_console_pb2.GetPromptResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
