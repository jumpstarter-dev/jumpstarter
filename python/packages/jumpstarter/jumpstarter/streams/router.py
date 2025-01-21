import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

import grpc
from anyio import (
    BrokenResourceError,
    EndOfStream,
)
from anyio.abc import ObjectStream
from jumpstarter_protocol import router_pb2

logger = logging.getLogger(__name__)


@dataclass(kw_only=True, slots=True)
class RouterStream(ObjectStream[bytes]):
    context: grpc.aio.StreamStreamCall | grpc._cython.cygrpc._ServicerContext
    cls: type = field(init=False)

    def __post_init__(self):
        match self.context:
            case grpc.aio.StreamStreamCall():
                self.cls = router_pb2.StreamRequest
            case grpc._cython.cygrpc._ServicerContext():
                self.cls = router_pb2.StreamResponse
            case _:
                raise ValueError(f"RouterStream: invalid context type: {type(self.context)}")

    async def send(self, payload: bytes) -> None:
        try:
            await self.context.write(self.cls(payload=payload))
        except grpc.aio.AioRpcError as e:
            raise BrokenResourceError from e

    async def receive(self) -> bytes:
        try:
            frame = await self.context.read()
        except grpc.aio.AioRpcError as e:
            raise BrokenResourceError from e

        # Reference: https://grpc.github.io/grpc/python/grpc_asyncio.html#grpc.aio.StreamStreamCall.read
        if frame == grpc.aio.EOF:
            raise EndOfStream

        match frame.frame_type:
            case router_pb2.FRAME_TYPE_DATA:
                return frame.payload
            case router_pb2.FRAME_TYPE_GOAWAY:
                raise EndOfStream
            case router_pb2.FRAME_TYPE_PING:
                pass
            case _:
                logger.debug(f"RouterStream: unrecognized frame ignored: {frame}")

        return b""

    async def send_eof(self):
        with contextlib.suppress(grpc.aio.AioRpcError, asyncio.exceptions.InvalidStateError):
            await self.context.write(self.cls(frame_type=router_pb2.FRAME_TYPE_GOAWAY))
            if isinstance(self.context, grpc.aio.StreamStreamCall):
                await self.context.done_writing()

    async def aclose(self):
        with contextlib.suppress(grpc.aio.AioRpcError, asyncio.exceptions.InvalidStateError):
            await self.send_eof()
            if isinstance(self.context, grpc._cython.cygrpc._ServicerContext):
                await self.context.abort(grpc.StatusCode.ABORTED, "RouterStream: aclose")
