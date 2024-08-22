import logging
import os
from asyncio import InvalidStateError
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Union
from uuid import UUID

import grpc
from anyio import (
    BrokenResourceError,
    EndOfStream,
    create_memory_object_stream,
    create_task_group,
    fail_after,
    get_cancelled_exc_class,
    move_on_after,
)
from anyio.abc import AnyByteStream, ByteStream, ObjectStream
from anyio.streams.stapled import StapledObjectStream
from pydantic import BaseModel, Field, TypeAdapter

from jumpstarter.v1 import router_pb2, router_pb2_grpc

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


async def encapsulate_stream(context: grpc.aio.StreamStreamCall | grpc.aio.ServicerContext, rx: AnyByteStream):
    match context:
        case grpc.aio.StreamStreamCall():
            cls = router_pb2.StreamRequest
        case grpc.aio.ServicerContext() | grpc._cython.cygrpc._ServicerContext():
            cls = router_pb2.StreamResponse
        case _:
            raise ValueError(f"stream encapsulation invalid context type: {type(context)}")

    try:
        while True:
            with move_on_after(KEEPALIVE_INTERVAL) as scope:
                try:
                    payload = await rx.receive()
                # Ignore Exception: peer disconnect and EOF
                # Reference: https://anyio.readthedocs.io/en/stable/api.html#anyio.BrokenResourceError
                # https://anyio.readthedocs.io/en/stable/api.html#anyio.EndOfStream
                except (BrokenResourceError, EndOfStream):
                    logger.debug("stream encapsulation peer disconnect/EOF ignored")
                    break

            if scope.cancelled_caught:
                await context.write(cls(frame_type=router_pb2.FRAME_TYPE_PING))
            else:
                await context.write(cls(payload=payload))

        await context.write(cls(frame_type=router_pb2.FRAME_TYPE_GOAWAY))
        if isinstance(context, grpc.aio.StreamStreamCall):
            await context.done_writing()
    except (grpc.aio.AioRpcError, InvalidStateError) as e:
        logger.debug("stream encapsulation grpc error cancelling task")
        raise get_cancelled_exc_class() from e


async def decapsulate_stream(context: grpc.aio.StreamStreamCall | grpc.aio.ServicerContext, tx: AnyByteStream):
    while True:
        with fail_after(KEEPALIVE_INTERVAL + KEEPALIVE_TOLERANCE):
            try:
                frame = await context.read()
            except (grpc.aio.AioRpcError, InvalidStateError) as e:
                logger.debug("stream decapsulation grpc error cancelling task")
                raise get_cancelled_exc_class() from e

        # Reference: https://grpc.github.io/grpc/python/grpc_asyncio.html#grpc.aio.StreamStreamCall.read
        if frame == grpc.aio.EOF:
            break

        try:
            match frame.frame_type:
                case router_pb2.FRAME_TYPE_DATA:
                    await tx.send(frame.payload)
                case router_pb2.FRAME_TYPE_GOAWAY:
                    # Streams like UDPSocket do not support send_eof
                    if isinstance(tx, ObjectStream) or isinstance(tx, ByteStream):
                        await tx.send_eof()
                    break
                case _:
                    logger.debug(f"stream decapsulation unrecognized frame ignored: {frame}")
        # Ignore Exception: peer disconnect and EOF
        # Reference: https://anyio.readthedocs.io/en/stable/api.html#anyio.BrokenResourceError
        # https://anyio.readthedocs.io/en/stable/api.html#anyio.EndOfStream
        except (BrokenResourceError, EndOfStream):
            logger.debug("stream decapsulation peer disconnect/EOF ignored")


@asynccontextmanager
async def forward_server_stream(context, stream):
    async with create_task_group() as tg:
        tg.start_soon(decapsulate_stream, context, stream)
        tg.start_soon(encapsulate_stream, context, stream)
        yield
        tg.cancel_scope.cancel()


@asynccontextmanager
async def forward_client_stream(router, stream, metadata):
    context = router.Stream(metadata=metadata)

    async with create_task_group() as tg:
        tg.start_soon(decapsulate_stream, context, stream)
        tg.start_soon(encapsulate_stream, context, stream)
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

        async with forward_client_stream(router, stream, ()):
            yield


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
