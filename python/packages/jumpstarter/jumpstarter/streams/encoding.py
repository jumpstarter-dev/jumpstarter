import bz2
import lzma
import sys
import zlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping

from anyio import ClosedResourceError, EndOfStream
from anyio.abc import AnyByteStream, ObjectStream

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd


class Compression(StrEnum):
    GZIP = "gzip"
    XZ = "xz"
    BZ2 = "bz2"
    ZSTD = "zstd"


@dataclass(frozen=True)
class FileSignature:
    """File signature (magic bytes) for a compression format."""

    signature: bytes
    compression: Compression


# File signatures for compression format detection
# Reference: https://file-extension.net/seeker/
COMPRESSION_SIGNATURES: tuple[FileSignature, ...] = (
    FileSignature(b"\x1f\x8b\x08", Compression.GZIP),
    FileSignature(b"\xfd\x37\x7a\x58\x5a\x00", Compression.XZ),
    FileSignature(b"\x42\x5a\x68", Compression.BZ2),
    FileSignature(b"\x28\xb5\x2f\xfd", Compression.ZSTD),
)

# Standard buffer size for file signature detection (covers most formats)
SIGNATURE_BUFFER_SIZE = 8


def detect_compression_from_signature(data: bytes) -> Compression | None:
    """Detect compression format from file signature bytes at the start of data.

    Args:
        data: The first few bytes of the file/stream (at least SIGNATURE_BUFFER_SIZE bytes recommended)

    Returns:
        The detected Compression type, or None if uncompressed/unknown
    """
    for sig in COMPRESSION_SIGNATURES:
        if data.startswith(sig.signature):
            return sig.compression
    return None


def create_decompressor(compression: Compression) -> Any:
    """Create a decompressor object for the given compression type."""
    match compression:
        case Compression.GZIP:
            return zlib.decompressobj(wbits=47)  # Auto-detect gzip/zlib
        case Compression.XZ:
            return lzma.LZMADecompressor()
        case Compression.BZ2:
            return bz2.BZ2Decompressor()
        case Compression.ZSTD:
            return zstd.ZstdDecompressor()


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
        case Compression.ZSTD:
            return CompressedStream(
                stream=stream,
                compressor=zstd.ZstdCompressor(),
                decompressor=zstd.ZstdDecompressor(),
            )


@dataclass(kw_only=True)
class AutoDecompressIterator(AsyncIterator[bytes]):
    """An async iterator that auto-detects and decompresses compressed data.

    This wraps an async iterator of bytes and transparently decompresses
    gzip, xz, bz2, or zstd compressed data based on file signature detection.
    Uncompressed data passes through unchanged.
    """

    source: AsyncIterator[bytes]
    _decompressor: Any = field(init=False, default=None)
    _detected: bool = field(init=False, default=False)
    _buffer: bytes = field(init=False, default=b"")
    _exhausted: bool = field(init=False, default=False)

    async def _detect_compression(self) -> None:
        """Read enough bytes to detect compression format."""
        # Buffer data until we have enough for detection
        while len(self._buffer) < SIGNATURE_BUFFER_SIZE and not self._exhausted:
            try:
                chunk = await self.source.__anext__()
                self._buffer += chunk
            except StopAsyncIteration:
                self._exhausted = True
                break

        # Detect compression from buffered data
        compression = detect_compression_from_signature(self._buffer)
        if compression is not None:
            self._decompressor = create_decompressor(compression)

        self._detected = True

    async def __anext__(self) -> bytes:
        # First call: detect compression format
        if not self._detected:
            await self._detect_compression()

        # Process buffered data first
        if self._buffer:
            data = self._buffer
            self._buffer = b""
            if self._decompressor is not None:
                return self._decompressor.decompress(data)
            return data

        # Stream exhausted
        if self._exhausted:
            raise StopAsyncIteration

        # Read and process next chunk
        try:
            chunk = await self.source.__anext__()
        except StopAsyncIteration:
            self._exhausted = True
            raise

        if self._decompressor is not None:
            return self._decompressor.decompress(chunk)
        return chunk

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self
