"""Auto-generated gRPC servicer adapter for AndroidEmulator.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import android_emulator_pb2, android_emulator_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.android_emulator.v1.AndroidEmulator"


def _register():
    """Register the AndroidEmulator servicer adapter."""
    from jumpstarter_driver_androidemulator.driver import AndroidEmulator

    register_servicer_adapter(
        interface_class=AndroidEmulator,
        service_name=SERVICE_NAME,
        servicer_factory=AndroidEmulatorServicer,
        add_to_server=android_emulator_pb2_grpc.add_AndroidEmulatorServicer_to_server,
    )


class AndroidEmulatorServicer(android_emulator_pb2_grpc.AndroidEmulatorServicer):
    """gRPC servicer that bridges AndroidEmulator to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def SetHeadless(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_headless):
            await driver.set_headless(request.headless)
        else:
            driver.set_headless(request.headless)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
