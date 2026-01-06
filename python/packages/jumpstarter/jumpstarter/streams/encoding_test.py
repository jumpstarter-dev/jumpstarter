import bz2
import gzip
import lzma
import os
import sys
from io import BytesIO

import pytest
from anyio import EndOfStream, create_memory_object_stream
from anyio.streams.stapled import StapledObjectStream

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd

from .encoding import (
    COMPRESSION_SIGNATURES,
    AutoDecompressIterator,
    Compression,
    compress_stream,
    detect_compression_from_signature,
)

pytestmark = pytest.mark.anyio


def create_buffer(size):
    tx, rx = create_memory_object_stream[bytes](size)
    return StapledObjectStream(tx, rx)


@pytest.mark.parametrize("compression", [None, "gzip", "xz", "bz2", "zstd"])
async def test_compress_stream(compression):
    stream = compress_stream(create_buffer(128), compression)

    await stream.send(b"hello")
    await stream.send_eof()

    result = BytesIO()
    while True:
        try:
            result.write(await stream.receive())
        except EndOfStream:
            break
    assert result.getvalue() == b"hello"


def _get_signature(compression: Compression) -> bytes:
    """Helper to get signature bytes for a compression type."""
    for sig in COMPRESSION_SIGNATURES:
        if sig.compression == compression:
            return sig.signature
    raise ValueError(f"No signature found for {compression}")


class TestDetectCompressionFromSignature:
    """Tests for file signature detection."""

    @pytest.mark.parametrize(
        "compression",
        [Compression.GZIP, Compression.XZ, Compression.BZ2, Compression.ZSTD],
    )
    def test_detect_from_signature(self, compression):
        """Each compression format should be detected from its signature."""
        signature = _get_signature(compression)
        # Pad with random bytes to simulate real file content
        data = signature + os.urandom(4)
        assert detect_compression_from_signature(data) == compression

    def test_detect_uncompressed(self):
        # Random data that doesn't match any compression format
        assert detect_compression_from_signature(b"hello world") is None

    def test_detect_empty(self):
        assert detect_compression_from_signature(b"") is None

    def test_detect_too_short(self):
        # Truncated signatures should not match
        assert detect_compression_from_signature(b"\x1f") is None  # gzip partial
        assert detect_compression_from_signature(b"\xfd\x37\x7a") is None  # xz partial

    def test_detect_from_real_gzip_data(self):
        compressed = gzip.compress(b"test data")
        assert detect_compression_from_signature(compressed) == Compression.GZIP

    def test_detect_from_real_xz_data(self):
        compressed = lzma.compress(b"test data", format=lzma.FORMAT_XZ)
        assert detect_compression_from_signature(compressed) == Compression.XZ

    def test_detect_from_real_bz2_data(self):
        compressed = bz2.compress(b"test data")
        assert detect_compression_from_signature(compressed) == Compression.BZ2

    def test_detect_from_real_zstd_data(self):
        compressed = zstd.compress(b"test data")
        assert detect_compression_from_signature(compressed) == Compression.ZSTD


class TestAutoDecompressIterator:
    """Tests for auto-decompressing async iterator."""

    async def _async_iter_from_bytes(self, data: bytes, chunk_size: int):
        """Helper to create an async iterator from bytes."""
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    async def _decompress_and_check(self, compressed: bytes, expected: bytes, chunk_size: int = 16):
        """Helper to decompress data and verify it matches expected output."""
        chunks = []
        async for chunk in AutoDecompressIterator(source=self._async_iter_from_bytes(compressed, chunk_size)):
            chunks.append(chunk)
        assert b"".join(chunks) == expected

    async def test_passthrough_uncompressed(self):
        """Uncompressed data should pass through unchanged."""
        original = b"hello world, this is uncompressed data"
        await self._decompress_and_check(original, original)

    async def test_decompress_gzip(self):
        """Gzip compressed data should be decompressed."""
        original = b"hello world, this is gzip compressed data"
        compressed = gzip.compress(original)
        await self._decompress_and_check(compressed, original)

    async def test_decompress_xz(self):
        """XZ compressed data should be decompressed."""
        original = b"hello world, this is xz compressed data"
        compressed = lzma.compress(original, format=lzma.FORMAT_XZ)
        await self._decompress_and_check(compressed, original)

    async def test_decompress_bz2(self):
        """BZ2 compressed data should be decompressed."""
        original = b"hello world, this is bz2 compressed data"
        compressed = bz2.compress(original)
        await self._decompress_and_check(compressed, original)

    async def test_decompress_zstd(self):
        """Zstd compressed data should be decompressed."""
        original = b"hello world, this is zstd compressed data"
        compressed = zstd.compress(original)
        await self._decompress_and_check(compressed, original)

    async def test_small_chunks(self):
        """Should work with very small chunks."""
        original = b"hello world"
        compressed = gzip.compress(original)
        await self._decompress_and_check(compressed, original, chunk_size=1)

    async def test_empty_input(self):
        """Empty input should produce no output."""

        async def empty_iter():
            if False:
                yield

        chunks = []
        async for chunk in AutoDecompressIterator(source=empty_iter()):
            chunks.append(chunk)
        assert chunks == []

    async def test_large_data(self):
        """Should handle large data correctly."""
        original = b"x" * 1024 * 1024  # 1MB of data
        compressed = gzip.compress(original)
        await self._decompress_and_check(compressed, original, chunk_size=65536)

    async def test_corrupted_gzip(self):
        """Corrupted gzip data should raise RuntimeError with clear message."""
        # Create fake gzip data: valid signature but corrupted payload
        corrupted = b"\x1f\x8b\x08" + b"corrupted data here"

        with pytest.raises(RuntimeError, match=r"Failed to decompress gzip:.*"):
            chunks = []
            async for chunk in AutoDecompressIterator(source=self._async_iter_from_bytes(corrupted, 16)):
                chunks.append(chunk)
