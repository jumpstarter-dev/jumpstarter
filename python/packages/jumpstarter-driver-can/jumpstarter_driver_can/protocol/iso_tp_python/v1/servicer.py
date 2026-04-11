"""Auto-generated gRPC servicer adapter for IsoTpPython.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import iso_tp_python_pb2, iso_tp_python_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.iso_tp_python.v1.IsoTpPython"


def _register():
    """Register the IsoTpPython servicer adapter."""
    from jumpstarter_driver_can.driver import IsoTpPython

    register_servicer_adapter(
        interface_class=IsoTpPython,
        service_name=SERVICE_NAME,
        servicer_factory=IsoTpPythonServicer,
        add_to_server=iso_tp_python_pb2_grpc.add_IsoTpPythonServicer_to_server,
    )


class IsoTpPythonServicer(iso_tp_python_pb2_grpc.IsoTpPythonServicer):
    """gRPC servicer that bridges IsoTpPython to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Available(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.available):
            result = await driver.available()
        else:
            result = driver.available()
        return iso_tp_python_pb2.AvailableResponse(value=result)

    async def Recv(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.recv):
            result = await driver.recv(request.block, request.timeout)
        else:
            result = driver.recv(request.block, request.timeout)
        return iso_tp_python_pb2.IsoTpMessage(data=result.data)

    async def Send(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.send):
            await driver.send(request.msg, request.target_address_type, request.send_timeout)
        else:
            driver.send(request.msg, request.target_address_type, request.send_timeout)
        return Empty()

    async def SetAddress(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_address):
            await driver.set_address(request.address)
        else:
            driver.set_address(request.address)
        return Empty()

    async def Start(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.start):
            await driver.start()
        else:
            driver.start()
        return Empty()

    async def Stop(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.stop):
            await driver.stop()
        else:
            driver.stop()
        return Empty()

    async def StopReceiving(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.stop_receiving):
            await driver.stop_receiving()
        else:
            driver.stop_receiving()
        return Empty()

    async def StopSending(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.stop_sending):
            await driver.stop_sending()
        else:
            driver.stop_sending()
        return Empty()

    async def Transmitting(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.transmitting):
            result = await driver.transmitting()
        else:
            result = driver.transmitting()
        return iso_tp_python_pb2.TransmittingResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
