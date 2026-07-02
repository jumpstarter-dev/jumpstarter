import os
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

# Resource-stream read chunk. The byte plane's throughput is per-chunk-overhead-bound (one FFI
# crossing + Python async hop per chunk on both client and host), so a small chunk caps a bulk
# flash regardless of transport: 64 KiB ≈ 345 MiB/s, 1 MiB ≈ 670 (and ≈ 2 GiB/s with the
# shared-memory tunnel). 1 MiB amortizes that overhead while staying modest in memory; override
# with `JMP_RESOURCE_CHUNK` (bytes) for memory-constrained hosts or many concurrent streams.
_RESOURCE_CHUNK = int(os.environ.get("JMP_RESOURCE_CHUNK", str(1024 * 1024)))


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
            item = await self.file.read(size=_RESOURCE_CHUNK)
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
    original_url: str | None = None,  # original HTTP URL, bypasses OpenDAL presign to avoid path re-encoding
):
    if original_url is not None and compression is not None:
        raise ValueError("compression is not supported with direct HTTP URLs (original_url)")
    if mode == "wb" and original_url is not None:
        raise ValueError("original_url is not supported in write mode")
    if mode == "rb" and compression is None and original_url is not None:
        yield PresignedRequestResource(
            headers={}, url=original_url, method="GET"
        ).model_dump_json()
        return
    # if the access mode is binary read, and the storage backend supports presigned read requests
    elif mode == "rb" and operator.capability().presign_read and compression is None:
        # create presigned url for the specified file with a 60 second expiration
        presigned = await operator.to_async_operator().presign_read(path, expire_second=60)
        yield PresignedRequestResource(
            headers=presigned.headers, url=presigned.url, method=presigned.method
        ).model_dump_json()
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
