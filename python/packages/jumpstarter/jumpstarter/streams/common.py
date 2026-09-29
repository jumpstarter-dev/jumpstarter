import asyncio
import errno
import logging
from contextlib import asynccontextmanager

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    create_memory_object_stream,
    create_task_group,
)
from anyio.abc import AnyByteStream
from anyio.streams.stapled import StapledObjectStream

from jumpstarter.metrics.registry import (
    StreamDirection,
    exemplars_from_log_context,
    exporter_from_log_context,
    get_registry,
)

logger = logging.getLogger(__name__)


async def _send_eof(dst: AnyByteStream) -> bool:
    """Half-close ``dst``; return False if its transport is unusable."""
    try:
        await dst.send_eof()
    except AttributeError:
        pass  # The destination cannot half-close.
    except OSError as e:
        # https://github.com/jumpstarter-dev/jumpstarter/issues/444
        # sending EOF to UDS on Darwin could result in
        # OSError: [Errno 57] Socket is not connected
        if e.errno != errno.ENOTCONN:
            logger.debug("stream EOF failed: %r", e)
            return False
    return True


async def copy_stream(
    dst: AnyByteStream,
    src: AnyByteStream,
    *,
    metrics_direction: StreamDirection | None = None,
    metrics_driver_type: str = "other",
) -> bool:
    """Copy until EOF; return False if the transport becomes unusable."""
    try:
        # Capture once per copy; context should be stable for the stream lifetime.
        if metrics_direction is not None:
            metrics_exporter = exporter_from_log_context()
            metrics_exemplars = exemplars_from_log_context()
            metrics_registry = get_registry()
        async for v in src:
            if metrics_direction is not None:
                metrics_registry.add_stream_bytes(
                    exporter=metrics_exporter,
                    driver_type=metrics_driver_type,
                    direction=metrics_direction,
                    nbytes=len(v) if isinstance(v, (bytes, bytearray, memoryview)) else 0,
                    exemplars=metrics_exemplars,
                )
            await dst.send(v)
        return await _send_eof(dst)
    except (BrokenResourceError, ClosedResourceError, asyncio.InvalidStateError) as e:
        if isinstance(e.__cause__, BrokenPipeError):
            # BrokenPipeError (EPIPE) = writing to a closed pipe during normal teardown
            logger.debug("stream copy interrupted (%s): %s", type(e).__name__, e)
        elif isinstance(e, BrokenResourceError) and e.__cause__ is None:
            # anyio raises BrokenResourceError(from None) when the underlying
            # TCP socket is closed by the peer (e.g. pexpect/fdspawn teardown).
            logger.debug("stream copy interrupted (%s): %s", type(e).__name__, e)
        else:
            logger.warning("stream copy interrupted (%s): %s", type(e).__name__, e)
            if e.__cause__ is not None:
                logger.debug("stream copy root cause: %r", e.__cause__)
        return False


@asynccontextmanager
async def forward_stream(a, b, *, metrics_driver_type: str | None = None):
    async with a, b, create_task_group() as tg:

        async def copy(dst, src, direction: StreamDirection):
            completed = await copy_stream(
                dst,
                src,
                metrics_direction=direction if metrics_driver_type is not None else None,
                metrics_driver_type=metrics_driver_type if metrics_driver_type is not None else "other",
            )
            if not completed:
                # Transport failure closes both peers; clean EOF still permits
                # a response in the other direction.
                tg.cancel_scope.cancel()

        tg.start_soon(copy, a, b, "tx")
        tg.start_soon(copy, b, a, "rx")
        yield


def create_memory_stream():
    a_tx, a_rx = create_memory_object_stream[bytes](32)
    b_tx, b_rx = create_memory_object_stream[bytes](32)
    a = StapledObjectStream(a_tx, b_rx)
    b = StapledObjectStream(b_tx, a_rx)
    return a, b
