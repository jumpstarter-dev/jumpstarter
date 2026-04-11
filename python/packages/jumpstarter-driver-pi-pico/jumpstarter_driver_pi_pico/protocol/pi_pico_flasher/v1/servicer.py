"""Auto-generated gRPC servicer adapter for PiPicoFlasher.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import pi_pico_flasher_pb2, pi_pico_flasher_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.pi_pico_flasher.v1.PiPicoFlasher"


def _register():
    """Register the PiPicoFlasher servicer adapter."""
    from jumpstarter_driver_pi_pico.driver import PiPicoFlasher

    register_servicer_adapter(
        interface_class=PiPicoFlasher,
        service_name=SERVICE_NAME,
        servicer_factory=PiPicoFlasherServicer,
        add_to_server=pi_pico_flasher_pb2_grpc.add_PiPicoFlasherServicer_to_server,
    )


class PiPicoFlasherServicer(pi_pico_flasher_pb2_grpc.PiPicoFlasherServicer):
    """gRPC servicer that bridges PiPicoFlasher to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def BootloaderInfo(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.bootloader_info):
            result = await driver.bootloader_info()
        else:
            result = driver.bootloader_info()
        return pi_pico_flasher_pb2.BootloaderInfoResponse()

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

    async def Flash(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flash):
            await driver.flash(request.source, request.target)
        else:
            driver.flash(request.source, request.target)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
