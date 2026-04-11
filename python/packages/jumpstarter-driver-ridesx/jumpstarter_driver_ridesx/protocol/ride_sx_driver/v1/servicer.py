"""Auto-generated gRPC servicer adapter for RideSXDriver.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import ride_sx_driver_pb2, ride_sx_driver_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.ride_sx_driver.v1.RideSXDriver"


def _register():
    """Register the RideSXDriver servicer adapter."""
    from jumpstarter_driver_ridesx.driver import RideSXDriver

    register_servicer_adapter(
        interface_class=RideSXDriver,
        service_name=SERVICE_NAME,
        servicer_factory=RideSXDriverServicer,
        add_to_server=ride_sx_driver_pb2_grpc.add_RideSXDriverServicer_to_server,
    )


class RideSXDriverServicer(ride_sx_driver_pb2_grpc.RideSXDriverServicer):
    """gRPC servicer that bridges RideSXDriver to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def BootToFastboot(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.boot_to_fastboot):
            await driver.boot_to_fastboot()
        else:
            driver.boot_to_fastboot()
        return Empty()

    async def DetectFastbootDevice(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.detect_fastboot_device):
            result = await driver.detect_fastboot_device(request.max_attempts, request.delay)
        else:
            result = driver.detect_fastboot_device(request.max_attempts, request.delay)
        return ride_sx_driver_pb2.DetectFastbootDeviceResponse(value=result)

    async def FlashOciImage(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flash_oci_image):
            result = await driver.flash_oci_image(request.oci_url, request.partitions, request.oci_username, request.oci_password)
        else:
            result = driver.flash_oci_image(request.oci_url, request.partitions, request.oci_username, request.oci_password)
        return ride_sx_driver_pb2.FlashOciImageResponse(value=result)

    async def FlashWithFastboot(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.flash_with_fastboot):
            await driver.flash_with_fastboot(request.device_id, request.partitions)
        else:
            driver.flash_with_fastboot(request.device_id, request.partitions)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
