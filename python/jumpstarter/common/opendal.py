from dataclasses import dataclass

from anyio import EndOfStream
from anyio.abc import ByteReceiveStream
from opendal import AsyncFile


@dataclass(kw_only=True)
class AsyncFileStream(ByteReceiveStream):
    file: AsyncFile

    async def receive(self, max_bytes=65536):
        data = await self.file.read(size=max_bytes)
        if len(data) == 0:
            raise EndOfStream
        return data

    async def aclose(self):
        await self.file.close()
