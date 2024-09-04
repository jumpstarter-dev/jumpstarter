"""
Base classes for drivers and driver clients
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass

from google.protobuf import json_format, struct_pb2
from grpc.aio import Channel

from jumpstarter.common import Metadata
from jumpstarter.common.resources import ResourceMetadata
from jumpstarter.common.streams import (
    DriverStreamRequest,
    ResourceStreamRequest,
    StreamRequestMetadata,
)
from jumpstarter.streams import (
    MetadataStream,
    MetadataStreamAttributes,
    ProgressStream,
    RouterStream,
    forward_stream,
)
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
            metadata=StreamRequestMetadata.model_construct(request=DriverStreamRequest(uuid=self.uuid, method=method))
            .model_dump(mode="json", round_trip=True)
            .items(),
        )
        metadata = dict(list(await context.initial_metadata()))
        async with MetadataStream(stream=RouterStream(context=context), metadata=metadata) as stream:
            yield stream

    @asynccontextmanager
    async def resource_async(
        self,
        stream,
    ):
        context = self.Stream(
            metadata=StreamRequestMetadata.model_construct(request=ResourceStreamRequest(uuid=self.uuid))
            .model_dump(mode="json", round_trip=True)
            .items(),
        )
        metadata = dict(list(await context.initial_metadata()))
        async with MetadataStream(stream=RouterStream(context=context), metadata=metadata) as rstream:
            async with forward_stream(ProgressStream(stream=stream), rstream):
                yield ResourceMetadata(**rstream.extra(MetadataStreamAttributes.metadata)).resource.model_dump(
                    mode="json"
                )
