from dataclasses import dataclass

from aiohttp import ClientError, StreamReader
from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream


@dataclass(frozen=True, kw_only=True, slots=True)
class AiohttpStreamReaderStream(ObjectStream[bytes]):
    reader: StreamReader

    async def send(self, item: bytes):
        raise BrokenResourceError

    async def receive(self) -> bytes:
        try:
            item = await self.reader.readany()
        except ClientError as e:
            raise BrokenResourceError from e
        if len(item) == 0:
            raise EndOfStream
        return item

    async def send_eof(self):
        pass

    async def aclose(self):
        pass
