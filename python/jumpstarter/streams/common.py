import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    create_memory_object_stream,
    create_task_group,
)
from anyio.abc import AnyByteStream
from anyio.streams.stapled import StapledObjectStream

logger = logging.getLogger(__name__)


async def copy_stream(dst: AnyByteStream, src: AnyByteStream):
    with suppress(BrokenResourceError, ClosedResourceError, asyncio.exceptions.InvalidStateError):
        async with dst:
            async for v in src:
                await dst.send(v)


@asynccontextmanager
async def forward_stream(a, b):
    async with create_task_group() as tg:
        tg.start_soon(copy_stream, a, b)
        tg.start_soon(copy_stream, b, a)
        yield


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
