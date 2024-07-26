"""
Base classes for drivers and driver clients
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass

from anyio import create_task_group
from anyio.streams.stapled import StapledObjectStream
from google.protobuf import json_format, struct_pb2
from grpc.aio import Channel

from jumpstarter.common import Interface, Metadata
from jumpstarter.common.streams import (
    create_memory_stream,
    forward_client_stream,
)
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc


@dataclass(kw_only=True)
class AsyncDriverClient(
    Metadata,
    Interface,
    jumpstarter_pb2_grpc.ExporterServiceStub,
    router_pb2_grpc.RouterServiceStub,
):
    """
    Async driver client base class

    Backing implementation of blocking driver client.
    """

    channel: Channel

    def __post_init__(self, *args):
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
    async def stream_async(self):
        client_stream, device_stream = create_memory_stream()

        async with create_task_group() as tg:
            tg.start_soon(
                forward_client_stream,
                self,
                device_stream,
                {"kind": "connect", "uuid": str(self.uuid)}.items(),
            )
            async with client_stream:
                yield client_stream

    @asynccontextmanager
    async def portforward_async(self, listener):
        async def handle(client):
            async with client:
                await forward_client_stream(
                    self,
                    client,
                    {"kind": "connect", "uuid": str(self.uuid)}.items(),
                )

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

        combined = StapledObjectStream(tx, stream)

        async def handle(stream):
            async with stream:
                await forward_client_stream(
                    self,
                    stream,
                    {"kind": "resource", "uuid": str(self.uuid)}.items(),
                )

        async with create_task_group() as tg:
            tg.start_soon(handle, combined)
            try:
                yield (await rx.receive()).decode()
            finally:
                tg.cancel_scope.cancel()
