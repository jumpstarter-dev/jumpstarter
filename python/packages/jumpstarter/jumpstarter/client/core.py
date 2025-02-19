"""
Base classes for drivers and driver clients
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from anyio import create_task_group
from google.protobuf import empty_pb2
from grpc import StatusCode
from grpc.aio import AioRpcError, Channel
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc, router_pb2_grpc

from jumpstarter.common import Metadata
from jumpstarter.common.resources import ResourceMetadata
from jumpstarter.common.serde import decode_value, encode_value
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

    log_level: str = "INFO"
    logger: logging.Logger = field(init=False)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        jumpstarter_pb2_grpc.ExporterServiceStub.__init__(self, self.channel)
        router_pb2_grpc.RouterServiceStub.__init__(self, self.channel)
        self.logger = logging.getLogger(self.__class__.__name__)
        self.logger.setLevel(self.log_level)

        # add default handler
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
            self.logger.addHandler(handler)

    async def call_async(self, method, *args):
        """Make DriverCall by method name and arguments"""

        request = jumpstarter_pb2.DriverCallRequest(
            uuid=str(self.uuid),
            method=method,
            args=[encode_value(arg) for arg in args],
        )

        try:
            response = await self.DriverCall(request)
        except AioRpcError as e:
            match e.code():
                case StatusCode.UNIMPLEMENTED:
                    raise NotImplementedError(e.details()) from None
                case StatusCode.INVALID_ARGUMENT:
                    raise ValueError(e.details()) from None
                case _:
                    raise

        return decode_value(response.result)

    async def streamingcall_async(self, method, *args):
        """Make StreamingDriverCall by method name and arguments"""

        request = jumpstarter_pb2.StreamingDriverCallRequest(
            uuid=str(self.uuid),
            method=method,
            args=[encode_value(arg) for arg in args],
        )

        try:
            async for response in self.StreamingDriverCall(request):
                yield decode_value(response.result)
        except AioRpcError as e:
            match e.code():
                case StatusCode.UNIMPLEMENTED:
                    raise NotImplementedError(e.details()) from None
                case StatusCode.INVALID_ARGUMENT:
                    raise ValueError(e.details()) from None
                case _:
                    raise

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

    @asynccontextmanager
    async def log_stream_async(self):
        async def log_stream():
            async for response in self.LogStream(empty_pb2.Empty()):
                self.logger.info(response.message)

        async with create_task_group() as tg:
            tg.start_soon(log_stream)
            try:
                yield
            finally:
                tg.cancel_scope.cancel()
