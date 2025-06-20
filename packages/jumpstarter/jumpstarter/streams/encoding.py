import bz2
import lzma
import zlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping

from anyio import ClosedResourceError, EndOfStream
from anyio.abc import AnyByteStream, ObjectStream


class Compression(StrEnum):
    GZIP = "gzip"
    XZ = "xz"
    BZ2 = "bz2"


@dataclass(kw_only=True)
class CompressedStream(ObjectStream[bytes]):
    stream: AnyByteStream
    decompressor: Any
    compressor: Any

    async def send(self, item: bytes) -> None:
        if self.compressor is None:
            raise ClosedResourceError

        await self.stream.send(self.compressor.compress(item))

    async def receive(self) -> bytes:
        return self.decompressor.decompress(await self.stream.receive())

    async def send_eof(self) -> None:
        await self._flush()
        await self.stream.send_eof()

    async def aclose(self) -> None:
        await self._flush()
        await self.stream.aclose()

    async def _flush(self) -> None:
        if self.compressor is None:
            return

        await self.stream.send(self.compressor.flush())
        self.compressor = None

    @property
    def extra_attributes(self) -> Mapping[Any, Callable[[], Any]]:
        return self.stream.extra_attributes


@dataclass(kw_only=True)
class ZlibCompressedStream(CompressedStream):
    async def receive(self) -> bytes:
        if self.decompressor is None:
            raise EndOfStream

        try:
            return self.decompressor.decompress(await self.stream.receive())
        except EndOfStream:
            data = self.decompressor.flush()
            self.decompressor = None
            return data


def compress_stream(stream: AnyByteStream, compression: Compression | None) -> AnyByteStream:
    match compression:
        case None:
            return stream
        case Compression.GZIP:
            return ZlibCompressedStream(
                stream=stream,
                compressor=zlib.compressobj(wbits=31),
                decompressor=zlib.decompressobj(wbits=47),
            )
        case Compression.XZ:
            return CompressedStream(
                stream=stream,
                compressor=lzma.LZMACompressor(),
                decompressor=lzma.LZMADecompressor(),
            )
        case Compression.BZ2:
            return CompressedStream(
                stream=stream,
                compressor=bz2.BZ2Compressor(),
                decompressor=bz2.BZ2Decompressor(),
            )
