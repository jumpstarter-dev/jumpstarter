"""
Base classes for drivers and driver clients
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID

from anyio import create_task_group, sleep_forever
from anyio.streams.stapled import StapledObjectStream
from google.protobuf import json_format, struct_pb2
from grpc.aio import Channel
from opendal import AsyncOperator

from jumpstarter.common import Metadata
from jumpstarter.common.opendal import AsyncFileStream
from jumpstarter.common.progress import ProgressStream
from jumpstarter.common.resources import ClientStreamResource, PresignedRequestResource
from jumpstarter.common.streams import (
    DriverStreamRequest,
    ResourceStreamRequest,
)
from jumpstarter.streams import RouterStream, create_memory_stream, forward_stream
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc


@dataclass(kw_only=True)
class AsyncDriverClient(
    Metadata,
    jumpstarter_pb2_grpc.ExporterServiceStub,
    router_pb2_grpc.RouterServiceStub,
):
    """
    Async driver client base class

    Backing implementation of blocking driver client.
    """

    channel: Channel

    def __post_init__(self):
        super().__post_init__()
        jumpstarter_pb2_grpc.ExporterServiceStub.__init__(self, self.channel)
        router_pb2_grpc.RouterServiceStub.__init__(self, self.channel)

    async def call_async(self, method, *args):
        """Make DriverCall by method name and arguments"""

        request = jumpstarter_pb2.DriverCallRequest(
            uuid=str(self.uuid),
            method=method,
            args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
        )

        response = await self.DriverCall(request)

        return json_format.MessageToDict(response.result)

    async def streamingcall_async(self, method, *args):
        """Make StreamingDriverCall by method name and arguments"""

        request = jumpstarter_pb2.StreamingDriverCallRequest(
            uuid=str(self.uuid),
            method=method,
            args=[json_format.ParseDict(arg, struct_pb2.Value()) for arg in args],
        )

        async for response in self.StreamingDriverCall(request):
            yield json_format.MessageToDict(response.result)

    @asynccontextmanager
    async def stream_async(self, method):
        context = self.Stream(
            metadata={"request": DriverStreamRequest(uuid=self.uuid, method=method).model_dump_json()}.items()
        )
        async with RouterStream(context=context) as stream:
            yield stream

    @asynccontextmanager
    async def portforward_async(self, method, listener):
        async def handle(client):
            async with client:
                context = self.Stream(
                    metadata={"request": DriverStreamRequest(uuid=self.uuid, method=method).model_dump_json()}.items()
                )
                async with RouterStream(context=context) as stream:
                    async with forward_stream(client, stream):
                        await sleep_forever()

        async with create_task_group() as tg:
            tg.start_soon(listener.serve, handle)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()

    @asynccontextmanager
    async def resource_async(
        self,
        stream,
    ):
        tx, rx = create_memory_stream()

        combined = StapledObjectStream(tx, ProgressStream(stream=stream))

        async with combined:
            context = self.Stream(
                metadata={"request": ResourceStreamRequest(uuid=self.uuid).model_dump_json()}.items(),
            )
            async with RouterStream(context=context) as rstream:
                async with forward_stream(combined, rstream):
                    yield ClientStreamResource(uuid=UUID((await rx.receive()).decode())).model_dump(mode="json")

    @asynccontextmanager
    async def file_async(
        self,
        operator: AsyncOperator,
        path: str,
    ):
        if operator.capability().presign_read:
            presigned = await operator.presign_read(path, expire_second=60)
            yield PresignedRequestResource(
                headers=presigned.headers, url=presigned.url, method=presigned.method
            ).model_dump(mode="json")
        else:
            file = await operator.open(path, "rb")
            async with self.resource_async(AsyncFileStream(file=file)) as handle:
                yield handle
