"""Auto-generated gRPC servicer adapter for Opendal.

Bridges native gRPC calls to @export driver methods via DriverRegistry.
Do not edit — regenerate with `jmp interface implement`.
"""

from __future__ import annotations

from inspect import isasyncgenfunction, iscoroutinefunction

import grpc
from google.protobuf.empty_pb2 import Empty

from . import opendal_pb2, opendal_pb2_grpc

from jumpstarter.exporter.registry import DriverRegistry, register_servicer_adapter

SERVICE_NAME = "jumpstarter.interfaces.opendal.v1.Opendal"


def _register():
    """Register the Opendal servicer adapter."""
    from jumpstarter_driver_opendal.driver import Opendal

    register_servicer_adapter(
        interface_class=Opendal,
        service_name=SERVICE_NAME,
        servicer_factory=OpendalServicer,
        add_to_server=opendal_pb2_grpc.add_OpendalServicer_to_server,
    )


class OpendalServicer(opendal_pb2_grpc.OpendalServicer):
    """gRPC servicer that bridges Opendal to @export driver methods."""

    def __init__(self, registry: DriverRegistry):
        self._registry = registry

    async def Capability(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.capability):
            result = await driver.capability()
        else:
            result = driver.capability()
        return opendal_pb2.CapabilityResponse(value=result)

    async def Copy(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.copy):
            await driver.copy(request.source, request.target)
        else:
            driver.copy(request.source, request.target)
        return Empty()

    async def CreateDir(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.create_dir):
            await driver.create_dir(request.path)
        else:
            driver.create_dir(request.path)
        return Empty()

    async def Delete(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.delete):
            await driver.delete(request.path)
        else:
            driver.delete(request.path)
        return Empty()

    async def Exists(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.exists):
            result = await driver.exists(request.path)
        else:
            result = driver.exists(request.path)
        return opendal_pb2.ExistsResponse(value=result)

    async def FileClose(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_close):
            await driver.file_close(request.fd)
        else:
            driver.file_close(request.fd)
        return Empty()

    async def FileClosed(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_closed):
            result = await driver.file_closed(request.fd)
        else:
            result = driver.file_closed(request.fd)
        return opendal_pb2.FileClosedResponse(value=result)

    async def FileRead(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_read):
            await driver.file_read(request.fd, request.dst)
        else:
            driver.file_read(request.fd, request.dst)
        return Empty()

    async def FileReadable(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_readable):
            result = await driver.file_readable(request.fd)
        else:
            result = driver.file_readable(request.fd)
        return opendal_pb2.FileReadableResponse(value=result)

    async def FileSeek(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_seek):
            result = await driver.file_seek(request.fd, request.pos, request.whence)
        else:
            result = driver.file_seek(request.fd, request.pos, request.whence)
        return opendal_pb2.FileSeekResponse(value=result)

    async def FileSeekable(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_seekable):
            result = await driver.file_seekable(request.fd)
        else:
            result = driver.file_seekable(request.fd)
        return opendal_pb2.FileSeekableResponse(value=result)

    async def FileTell(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_tell):
            result = await driver.file_tell(request.fd)
        else:
            result = driver.file_tell(request.fd)
        return opendal_pb2.FileTellResponse(value=result)

    async def FileWritable(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_writable):
            result = await driver.file_writable(request.fd)
        else:
            result = driver.file_writable(request.fd)
        return opendal_pb2.FileWritableResponse(value=result)

    async def FileWrite(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.file_write):
            await driver.file_write(request.fd, request.src)
        else:
            driver.file_write(request.fd, request.src)
        return Empty()

    async def GetCreatedResources(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.get_created_resources):
            result = await driver.get_created_resources()
        else:
            result = driver.get_created_resources()
        return opendal_pb2.GetCreatedResourcesResponse(value=result)

    async def Hash(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.hash):
            result = await driver.hash(request.path, request.algo)
        else:
            result = driver.hash(request.path, request.algo)
        return opendal_pb2.HashResponse(value=result)

    async def List(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).list):
            async for item in driver.list(request.path):
                yield opendal_pb2.ListResponse(value=item.value)
        else:
            for item in driver.list(request.path):
                yield opendal_pb2.ListResponse(value=item.value)

    async def Open(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.open):
            result = await driver.open(request.path, request.mode)
        else:
            result = driver.open(request.path, request.mode)
        return opendal_pb2.OpenResponse(value=result)

    async def PresignRead(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.presign_read):
            result = await driver.presign_read(request.path, request.expire_second)
        else:
            result = driver.presign_read(request.path, request.expire_second)
        return opendal_pb2.PresignReadResponse(value=result)

    async def PresignStat(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.presign_stat):
            result = await driver.presign_stat(request.path, request.expire_second)
        else:
            result = driver.presign_stat(request.path, request.expire_second)
        return opendal_pb2.PresignStatResponse(value=result)

    async def PresignWrite(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.presign_write):
            result = await driver.presign_write(request.path, request.expire_second)
        else:
            result = driver.presign_write(request.path, request.expire_second)
        return opendal_pb2.PresignWriteResponse(value=result)

    async def RegisterPath(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.register_path):
            await driver.register_path(request.path)
        else:
            driver.register_path(request.path)
        return Empty()

    async def RemoveAll(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.remove_all):
            await driver.remove_all(request.path)
        else:
            driver.remove_all(request.path)
        return Empty()

    async def Rename(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.rename):
            await driver.rename(request.source, request.target)
        else:
            driver.rename(request.source, request.target)
        return Empty()

    async def Scan(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if isasyncgenfunction(type(driver).scan):
            async for item in driver.scan(request.path):
                yield opendal_pb2.ScanResponse(value=item.value)
        else:
            for item in driver.scan(request.path):
                yield opendal_pb2.ScanResponse(value=item.value)

    async def Stat(self, request, context):
        driver = await self._registry.resolve(context, SERVICE_NAME)
        if iscoroutinefunction(driver.stat):
            result = await driver.stat(request.path)
        else:
            result = driver.stat(request.path)
        return opendal_pb2.StatResponse(value=result)


# Register the adapter at import time so the Session can discover it.
_register()
