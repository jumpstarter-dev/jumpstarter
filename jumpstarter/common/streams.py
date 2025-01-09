from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union
from uuid import UUID

import grpc
from pydantic import BaseModel, Field, Json

from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import router_pb2_grpc


class ResourceStreamRequest(BaseModel):
    kind: Literal["resource"] = "resource"
    uuid: UUID


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
async def connect_router_stream(endpoint, token, stream, tls_config):
    credentials = grpc.composite_channel_credentials(
        ssl_channel_credentials(endpoint, tls_config),
        grpc.access_token_call_credentials(token),
    )

    async with aio_secure_channel(endpoint, credentials) as channel:
        router = router_pb2_grpc.RouterServiceStub(channel)
        context = router.Stream(metadata=())
        async with RouterStream(context=context) as s:
            async with forward_stream(s, stream):
                yield
