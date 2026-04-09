from contextlib import suppress
from dataclasses import dataclass, field
from typing import Tuple

from anyio import BrokenResourceError, WouldBlock, create_memory_object_stream
from anyio.abc import AnyByteStream, ObjectStream
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from websockets.asyncio.client import ClientConnection as WSSClientConnection
from wsproto import ConnectionType, WSConnection
from wsproto.connection import ConnectionState
from wsproto.events import (
    AcceptConnection,
    CloseConnection,
    Message,
    Ping,
    Request,
)
from wsproto.frame_protocol import CloseReason
from wsproto.utilities import LocalProtocolError, RemoteProtocolError


@dataclass(kw_only=True)
class WebsocketServerStream(ObjectStream[bytes]):
    stream: AnyByteStream

    ws: WSConnection = field(init=False, default_factory=lambda: WSConnection(ConnectionType.SERVER))
    queue: Tuple[MemoryObjectSendStream[bytes], MemoryObjectReceiveStream[bytes]] = field(
        init=False,
        default_factory=lambda: create_memory_object_stream[bytes](32),  # ty: ignore[call-non-callable]
    )

    async def send(self, data: bytes) -> None:
        try:
            self.ws.receive_data(data)
        except RemoteProtocolError as e:
            raise BrokenResourceError from e

        try:
            for event in self.ws.events():
                match event:
                    case Request():
                        await self.queue[0].send(self.ws.send(AcceptConnection()))
                    case CloseConnection():
                        await self.queue[0].send(self.ws.send(event.response()))
                    case Message():
                        await self.stream.send(event.data)
                    case Ping():
                        await self.queue[0].send(self.ws.send(event.response()))
        except LocalProtocolError as e:
            raise BrokenResourceError from e

    async def receive(self) -> bytes:
        with suppress(WouldBlock):
            return self.queue[1].receive_nowait()

        if self.ws.state == ConnectionState.CONNECTING:
            return await self.queue[1].receive()

        try:
            return self.ws.send(Message(data=await self.stream.receive()))
        except LocalProtocolError as e:
            raise BrokenResourceError from e

    async def send_eof(self):
        # websocket does not have half closed connections
        pass

    async def aclose(self):
        with suppress(LocalProtocolError):
            await self.stream.send(self.ws.send(CloseConnection(code=CloseReason.NORMAL_CLOSURE)))
        await self.stream.aclose()


@dataclass(kw_only=True)
class WebsocketClientStream(ObjectStream[bytes]):
    """
    Websocket client streaming.
    """

    conn: WSSClientConnection

    async def send(self, data: bytes) -> None:
        await self.conn.send(data)

    async def receive(self) -> bytes:
        return await self.conn.recv()

    async def send_eof(self):
        pass

    async def aclose(self):
        await self.conn.close()
