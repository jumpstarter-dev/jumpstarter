import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union
from uuid import UUID

import grpc
from jumpstarter_protocol.jumpstarter.v1 import router_pb2_grpc
from pydantic import BaseModel, Field, Json

from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.streams.common import forward_stream
from jumpstarter.streams.router import RouterStream


class ResourceStreamRequest(BaseModel):
    kind: Literal["resource"] = "resource"
    uuid: UUID
    x_jmp_content_encoding: str | None = None


class DriverStreamRequest(BaseModel):
    kind: Literal["driver"] = "driver"
    uuid: UUID
    method: str


StreamRequest = Annotated[
    Union[ResourceStreamRequest, DriverStreamRequest],
    Field(discriminator="kind"),
]


class StreamRequestMetadata(BaseModel):
    request: Json[StreamRequest]


@asynccontextmanager
async def connect_router_stream(endpoint, token, stream, tls_config, grpc_options, channel_ready_timeout=10):
    credentials = grpc.composite_channel_credentials(
        await ssl_channel_credentials(endpoint, tls_config),
        grpc.access_token_call_credentials(token),
    )

    async with aio_secure_channel(endpoint, credentials, grpc_options) as channel:
        # Wait for the channel to be ready before starting the stream.
        # Without this, a broken router connection would cause the gRPC
        # stream to hang indefinitely waiting for the HTTP/2 SETTINGS frame,
        # which manifests as a timeout for the j command on the Unix socket.
        try:
            await asyncio.wait_for(channel.channel_ready(), timeout=channel_ready_timeout)
        except asyncio.TimeoutError:
            raise grpc.aio.AioRpcError(
                code=grpc.StatusCode.UNAVAILABLE,
                initial_metadata=grpc.aio.Metadata(),
                trailing_metadata=grpc.aio.Metadata(),
                details=f"Timed out waiting for router channel to become ready ({channel_ready_timeout}s)",
                debug_error_string=None,
            ) from None
        router = router_pb2_grpc.RouterServiceStub(channel)
        context = router.Stream(metadata=())
        async with RouterStream(context=context) as s:
            async with forward_stream(s, stream):
                yield
