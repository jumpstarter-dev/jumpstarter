from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

from anyio import BrokenResourceError, EndOfStream
from anyio.abc import ObjectStream
from opendal import AsyncFile, Metadata, Operator
from opendal.exceptions import Error

from jumpstarter.client import DriverClient
from jumpstarter.client.adapters import blocking
from jumpstarter.common.resources import PresignedRequestResource
from jumpstarter.streams.encoding import Compression
from jumpstarter.streams.progress import ProgressAttribute


@dataclass(frozen=True, kw_only=True, slots=True)
class AsyncFileStream(ObjectStream[bytes]):
    """
    wrapper type for opendal.AsyncFile to make it compatible with anyio streams
    """

    file: AsyncFile
    metadata: Metadata | None = field(default=None)

    async def send(self, item: bytes):
        try:
            await self.file.write(item)
        except Error as e:
            raise BrokenResourceError from e

    async def receive(self) -> bytes:
        if not await self.file.readable():
            raise EndOfStream
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

    @property
    def extra_attributes(self) -> Mapping[Any, Callable[[], Any]]:
        if self.metadata is not None and self.metadata.content_length != 0:
            return {ProgressAttribute.total: lambda: float(self.metadata.content_length)}
        else:
            return {}


@blocking
@asynccontextmanager
async def OpendalAdapter(
    *,
    client: DriverClient,
    operator: Operator,  # opendal.Operator for the storage backend
    path: str,  # file path in storage backend relative to the storage root
    mode: Literal["rb", "wb"] = "rb",  # binary read or binary write mode
    compression: Compression | None = None,  # compression algorithm
):
    # if the access mode is binary read, and the storage backend supports presigned read requests
    if mode == "rb" and operator.capability().presign_read and compression is None:
        # create presigned url for the specified file with a 60 second expiration
        presigned = await operator.to_async_operator().presign_read(path, expire_second=60)
        yield PresignedRequestResource(
            headers=presigned.headers, url=presigned.url, method=presigned.method
        ).model_dump(mode="json")
    # otherwise stream the file content from the client to the exporter
    else:
        try:
            metadata = await operator.to_async_operator().stat(path)
        except Exception:
            metadata = None
        file = await operator.to_async_operator().open(path, mode)
        async with client.resource_async(
            AsyncFileStream(file=file, metadata=metadata),
            content_encoding=compression,
        ) as res:
            yield res
