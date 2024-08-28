import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union
from uuid import UUID

import grpc
from anyio import (
    create_memory_object_stream,
    create_task_group,
)
from anyio.abc import AnyByteStream, ByteStream, ObjectStream
from anyio.streams.stapled import StapledObjectStream
from pydantic import BaseModel, Field, TypeAdapter

from jumpstarter.streams import RouterStream
from jumpstarter.v1 import router_pb2_grpc

KEEPALIVE_INTERVAL = int(os.environ.get("JMP_KEEPALIVE_INTERVAL", "300"))
KEEPALIVE_TOLERANCE = int(os.environ.get("JMP_KEEPALIVE_TOLERANCE", "600"))

logger = logging.getLogger(__name__)


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


async def copy_stream(tx: AnyByteStream, rx: AnyByteStream):
    try:
        async for v in rx:
            await tx.send(v)
        if isinstance(tx, ObjectStream) or isinstance(tx, ByteStream):
            await tx.send_eof()
    except Exception:
        logger.debug("copy stream error cancelling task")
        # raise get_cancelled_exc_class() from e


@asynccontextmanager
async def forward_stream(a, b):
    async with create_task_group() as tg:
        tg.start_soon(copy_stream, a, b)
        tg.start_soon(copy_stream, b, a)
        yield
        tg.cancel_scope.cancel()


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


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
