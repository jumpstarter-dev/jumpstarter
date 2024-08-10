from dataclasses import dataclass

from aiohttp import StreamReader
from anyio import EndOfStream
from anyio.abc import ByteReceiveStream


@dataclass(kw_only=True)
class AiohttpStream(ByteReceiveStream):
    stream: StreamReader

    async def receive(self, max_bytes=65536):
        data = await self.stream.read(n=max_bytes)
        if len(data) == 0:
            raise EndOfStream
        return data

    async def aclose(self):
        pass
