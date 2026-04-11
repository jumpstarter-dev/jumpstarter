"""Auto-generated gRPC servicer adapter for Xcp.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import xcp_pb2, xcp_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.xcp.v1.Xcp"


def _register():
    """Register the Xcp servicer adapter."""
    from jumpstarter_driver_xcp.driver import Xcp

    register_servicer_adapter(
        interface_class=Xcp,
        service_name=SERVICE_NAME,
        servicer_factory=XcpServicer,
        add_to_server=xcp_pb2_grpc.add_XcpServicer_to_server,
    )


class XcpServicer(xcp_pb2_grpc.XcpServicer):
    """gRPC servicer that bridges Xcp to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def AllocDaq(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.alloc_daq):
            await driver.alloc_daq(request.daq_count)
        else:
            driver.alloc_daq(request.daq_count)
        return Empty()

    async def AllocOdt(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.alloc_odt):
            await driver.alloc_odt(request.daq_list_number, request.odt_count)
        else:
            driver.alloc_odt(request.daq_list_number, request.odt_count)
        return Empty()

    async def AllocOdtEntry(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.alloc_odt_entry):
            await driver.alloc_odt_entry(request.daq_list_number, request.odt_number, request.odt_entries_count)
        else:
            driver.alloc_odt_entry(request.daq_list_number, request.odt_number, request.odt_entries_count)
        return Empty()

    async def BuildChecksum(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.build_checksum):
            result = await driver.build_checksum(request.block_size)
        else:
            result = driver.build_checksum(request.block_size)
        return xcp_pb2.BuildChecksumResponse(value=result)

    async def Connect(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.connect):
            result = await driver.connect(request.mode)
        else:
            result = driver.connect(request.mode)
        return xcp_pb2.ConnectResponse(value=result)

    async def Disconnect(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.disconnect):
            await driver.disconnect()
        else:
            driver.disconnect()
        return Empty()

    async def Download(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.download):
            await driver.download(request.address, request.data, request.ext)
        else:
            driver.download(request.address, request.data, request.ext)
        return Empty()

    async def FreeDaq(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.free_daq):
            await driver.free_daq()
        else:
            driver.free_daq()
        return Empty()

    async def GetDaqInfo(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_daq_info):
            result = await driver.get_daq_info()
        else:
            result = driver.get_daq_info()
        return xcp_pb2.GetDaqInfoResponse(value=result)

    async def GetId(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_id):
            result = await driver.get_id(request.id_type)
        else:
            result = driver.get_id(request.id_type)
        return xcp_pb2.GetIdResponse(value=result)

    async def GetStatus(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_status):
            result = await driver.get_status()
        else:
            result = driver.get_status()
        return xcp_pb2.GetStatusResponse(value=result)

    async def Program(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.program):
            await driver.program(request.data, request.block_length)
        else:
            driver.program(request.data, request.block_length)
        return Empty()

    async def ProgramClear(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.program_clear):
            await driver.program_clear(request.clear_range, request.mode)
        else:
            driver.program_clear(request.clear_range, request.mode)
        return Empty()

    async def ProgramReset(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.program_reset):
            await driver.program_reset()
        else:
            driver.program_reset()
        return Empty()

    async def ProgramStart(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.program_start):
            result = await driver.program_start()
        else:
            result = driver.program_start()
        return xcp_pb2.ProgramStartResponse(value=result)

    async def SetDaqListMode(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_daq_list_mode):
            await driver.set_daq_list_mode(request.mode, request.daq_list, request.event, request.prescaler, request.priority)
        else:
            driver.set_daq_list_mode(request.mode, request.daq_list, request.event, request.prescaler, request.priority)
        return Empty()

    async def SetDaqPtr(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_daq_ptr):
            await driver.set_daq_ptr(request.daq_list, request.odt, request.entry)
        else:
            driver.set_daq_ptr(request.daq_list, request.odt, request.entry)
        return Empty()

    async def SetMta(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.set_mta):
            await driver.set_mta(request.address, request.ext)
        else:
            driver.set_mta(request.address, request.ext)
        return Empty()

    async def StartStopDaqList(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.start_stop_daq_list):
            await driver.start_stop_daq_list(request.mode, request.daq_list)
        else:
            driver.start_stop_daq_list(request.mode, request.daq_list)
        return Empty()

    async def StartStopSynch(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.start_stop_synch):
            await driver.start_stop_synch(request.mode)
        else:
            driver.start_stop_synch(request.mode)
        return Empty()

    async def Unlock(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.unlock):
            result = await driver.unlock(request.resources)
        else:
            result = driver.unlock(request.resources)
        return xcp_pb2.UnlockResponse()

    async def Upload(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.upload):
            result = await driver.upload(request.length, request.address, request.ext)
        else:
            result = driver.upload(request.length, request.address, request.ext)
        return xcp_pb2.UploadResponse(value=result)

    async def WriteDaq(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.write_daq):
            await driver.write_daq(request.bit_offset, request.size, request.ext, request.address)
        else:
            driver.write_daq(request.bit_offset, request.size, request.ext, request.address)
        return Empty()


# Register the adapter at import time so the Session can discover it.
_register()
