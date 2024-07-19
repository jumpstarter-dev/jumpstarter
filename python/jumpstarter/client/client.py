from jumpstarter.v1 import (
    jumpstarter_pb2_grpc,
    router_pb2_grpc,
)
from jumpstarter.drivers.composite.base import ClientFromReports
from jumpstarter.common.streams import forward_client_stream
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
        self.root = ClientFromReports(
            (await self.stub.GetReport(empty_pb2.Empty())).reports, self.channel
        )

    @contextlib.asynccontextmanager
    async def Resource(
        self,
        stream,
    ):
        uuid = uuid4()

        async def handle(stream):
            async with stream:
                await forward_client_stream(
                    self.router, stream, {"kind": "resource", "uuid": str(uuid)}.items()
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
