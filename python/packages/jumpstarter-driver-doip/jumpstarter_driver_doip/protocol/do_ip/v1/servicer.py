"""Auto-generated gRPC servicer adapter for DoIP.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import do_ip_pb2, do_ip_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.do_ip.v1.DoIP"


def _register():
    """Register the DoIP servicer adapter."""
    from jumpstarter_driver_doip.driver import DoIP

    register_servicer_adapter(
        interface_class=DoIP,
        service_name=SERVICE_NAME,
        servicer_factory=DoIPServicer,
        add_to_server=do_ip_pb2_grpc.add_DoIPServicer_to_server,
    )


class DoIPServicer(do_ip_pb2_grpc.DoIPServicer):
    """gRPC servicer that bridges DoIP to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def AliveCheck(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.alive_check):
            result = await driver.alive_check()
        else:
            result = driver.alive_check()
        return do_ip_pb2.AliveCheckResponse(value=result)

    async def CloseConnection(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.close_connection):
            await driver.close_connection()
        else:
            driver.close_connection()
        return Empty()

    async def DiagnosticPowerMode(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.diagnostic_power_mode):
            result = await driver.diagnostic_power_mode()
        else:
            result = driver.diagnostic_power_mode()
        return do_ip_pb2.DiagnosticPowerModeResponse(value=result)

    async def EntityStatus(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.entity_status):
            result = await driver.entity_status()
        else:
            result = driver.entity_status()
        return do_ip_pb2.EntityStatusResponse(value=result)

    async def ReceiveDiagnostic(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.receive_diagnostic):
            result = await driver.receive_diagnostic(request.timeout)
        else:
            result = driver.receive_diagnostic(request.timeout)
        return do_ip_pb2.ReceiveDiagnosticResponse(value=result)

    async def Reconnect(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.reconnect):
            await driver.reconnect(request.close_delay)
        else:
            driver.reconnect(request.close_delay)
        return Empty()

    async def RequestVehicleIdentification(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.request_vehicle_identification):
            result = await driver.request_vehicle_identification(request.vin, request.eid)
        else:
            result = driver.request_vehicle_identification(request.vin, request.eid)
        return do_ip_pb2.RequestVehicleIdentificationResponse(value=result)

    async def RoutingActivation(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.routing_activation):
            result = await driver.routing_activation(request.activation_type)
        else:
            result = driver.routing_activation(request.activation_type)
        return do_ip_pb2.RoutingActivationResponse(value=result)

    async def SendDiagnostic(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.send_diagnostic):
            await driver.send_diagnostic(request.payload)
        else:
            driver.send_diagnostic(request.payload)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
