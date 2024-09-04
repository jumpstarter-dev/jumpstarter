from contextlib import suppress
from dataclasses import dataclass
from typing import Literal

from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream
from opendal import AsyncFile, Operator
from opendal.exceptions import Error

from jumpstarter.common.resources import PresignedRequestResource

from .common import ClientAdapter


@dataclass(frozen=True, kw_only=True, slots=True)
class AsyncFileStream(ObjectStream[bytes]):
    file: AsyncFile

    async def send(self, item: bytes):
        try:
            await self.file.write(item)
        except Error as e:
            raise BrokenResourceError from e

    async def receive(self) -> bytes:
        try:
            item = await self.file.read(size=65536)
        except Error as e:
            raise BrokenResourceError from e
        if len(item) == 0:
            raise EndOfStream
        return item

    async def send_eof(self):
        pass

    async def aclose(self):
        with suppress(Error):
            await self.file.close()


@dataclass(kw_only=True)
class OpendalAdapter(ClientAdapter):
    operator: Operator
    path: str
    mode: Literal["rb", "wb"] = "rb"

    async def __aenter__(self):
        if self.mode == "rb" and self.operator.capability().presign_read:
            presigned = await self.operator.to_async_operator().presign_read(self.path, expire_second=60)
            return PresignedRequestResource(
                headers=presigned.headers, url=presigned.url, method=presigned.method
            ).model_dump(mode="json")
        else:
            file = await self.operator.to_async_operator().open(self.path, self.mode)

            self.resource = self.client.resource_async(AsyncFileStream(file=file))

            return await self.resource.__aenter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        if hasattr(self, "resource"):
            await self.resource.__aexit__(exc_type, exc_value, traceback)
