"""Auto-generated gRPC servicer adapter for Esp32Flasher.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import esp32_flasher_pb2, esp32_flasher_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.esp32_flasher.v1.Esp32Flasher"


def _register():
    """Register the Esp32Flasher servicer adapter."""
    from jumpstarter_driver_esp32.driver import Esp32Flasher

    register_servicer_adapter(
        interface_class=Esp32Flasher,
        service_name=SERVICE_NAME,
        servicer_factory=Esp32FlasherServicer,
        add_to_server=esp32_flasher_pb2_grpc.add_Esp32FlasherServicer_to_server,
    )


class Esp32FlasherServicer(esp32_flasher_pb2_grpc.Esp32FlasherServicer):
    """gRPC servicer that bridges Esp32Flasher to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Dump(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.dump):
            await driver.dump(request.target, request.partition)
        else:
            driver.dump(request.target, request.partition)
        return Empty()

    async def EnterBootloader(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.enter_bootloader):
            await driver.enter_bootloader()
        else:
            driver.enter_bootloader()
        return Empty()

    async def Erase(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.erase):
            await driver.erase()
        else:
            driver.erase()
        return Empty()

    async def Flash(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flash):
            await driver.flash(request.source, request.target)
        else:
            driver.flash(request.source, request.target)
        return Empty()

    async def GetChipInfo(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_chip_info):
            result = await driver.get_chip_info()
        else:
            result = driver.get_chip_info()
        return esp32_flasher_pb2.GetChipInfoResponse(value=result)

    async def HardReset(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.hard_reset):
            await driver.hard_reset()
        else:
            driver.hard_reset()
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
