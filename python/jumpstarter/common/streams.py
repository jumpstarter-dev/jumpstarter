from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union
from uuid import UUID

import grpc
from pydantic import BaseModel, Field, TypeAdapter

from jumpstarter.streams import RouterStream, forward_stream
from jumpstarter.v1 import router_pb2_grpc


class ResourceStreamRequest(BaseModel):
    kind: Literal["resource"] = "resource"
    uuid: UUID


class DriverStreamRequest(BaseModel):
    kind: Literal["driver"] = "driver"
    uuid: UUID
    method: str


StreamRequest = TypeAdapter(
    Annotated[
        Union[ResourceStreamRequest, DriverStreamRequest],
        Field(discriminator="kind"),
    ]
)


@asynccontextmanager
async def connect_router_stream(endpoint, token, stream):
    credentials = grpc.composite_channel_credentials(
        grpc.local_channel_credentials(),  # TODO: Use TLS
        grpc.access_token_call_credentials(token),
    )

    async with grpc.aio.secure_channel(endpoint, credentials) as channel:
        router = router_pb2_grpc.RouterServiceStub(channel)
        context = router.Stream(metadata=())
        async with RouterStream(context=context) as s:
            async with forward_stream(s, stream):
                yield
