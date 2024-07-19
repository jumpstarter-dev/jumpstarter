from jumpstarter.v1 import (
    jumpstarter_pb2,
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)
from jumpstarter.drivers.composite.base import ClientFromReports
from jumpstarter.common.streams import create_memory_stream, forward_client_stream
from google.protobuf import empty_pb2
from dataclasses import dataclass
from uuid import uuid4
from anyio.streams.file import FileReadStream
import contextlib
import anyio


@dataclass
class Client:
    stub: jumpstarter_pb2_grpc.ExporterServiceStub

    def __init__(self, channel):
        self.channel = channel
        self.stub = jumpstarter_pb2_grpc.ExporterServiceStub(channel)
        self.router = router_pb2_grpc.RouterServiceStub(channel)

    async def sync(self):
        self.root = ClientFromReports((await self.GetReport()).reports, self.channel)

    async def GetReport(self):
        return await self.stub.GetReport(empty_pb2.Empty())

    async def RawStream(self, stream, metadata):
        await forward_client_stream(self.router, stream, metadata)

    @contextlib.asynccontextmanager
    async def Stream(self, device):
        client_stream, device_stream = create_memory_stream()

        async with anyio.create_task_group() as tg:
            tg.start_soon(
                self.RawStream,
                device_stream,
                {"kind": "device", "uuid": str(device.uuid)}.items(),
            )
            try:
                yield client_stream
            finally:
                await client_stream.aclose()

    @contextlib.asynccontextmanager
    async def Forward(
        self,
        listener,
        device,
    ):
        async def handle(client):
            async with client:
                await self.RawStream(
                    client, {"kind": "device", "uuid": str(device.uuid)}.items()
                )

        async with anyio.create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

    @contextlib.asynccontextmanager
    async def Resource(
        self,
        stream,
    ):
        uuid = uuid4()

        async def handle(stream):
            async with stream:
                await self.RawStream(
                    stream, {"kind": "resource", "uuid": str(uuid)}.items()
                )

        async with anyio.create_task_group() as tg:
            tg.start_soon(handle, stream)
            try:
                yield str(uuid)
            finally:
                tg.cancel_scope.cancel()

    @contextlib.asynccontextmanager
    async def LocalFile(
        self,
        filepath,
    ):
        async with await FileReadStream.from_path(filepath) as file:
            async with self.Resource(file) as uuid:
                yield uuid
