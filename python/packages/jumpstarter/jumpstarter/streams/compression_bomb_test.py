import bz2
import gzip
import io
import lzma
import zlib

import pytest
from hypothesis import given
from hypothesis import strategies as st

from jumpstarter.streams.encoding import (
    AutoDecompressIterator,
    Compression,
    create_decompressor,
    detect_compression_from_signature,
)


def _make_gzip_bomb(decompressed_size: int) -> bytes:
    data = b"\x00" * decompressed_size
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as f:
        f.write(data)
    return buf.getvalue()


def _make_bz2_bomb(decompressed_size: int) -> bytes:
    data = b"\x00" * decompressed_size
    return bz2.compress(data)


def _make_xz_bomb(decompressed_size: int) -> bytes:
    data = b"\x00" * decompressed_size
    return lzma.compress(data)


def _make_zstd_bomb(decompressed_size: int) -> bytes:
    import sys

    if sys.version_info >= (3, 14):
        from compression import zstd
    else:
        from backports import zstd

    data = b"\x00" * decompressed_size
    return zstd.compress(data)


BOMB_SIZE = 1024 * 1024


class TestDetectCompressionSignatures:
    def test_gzip_detected(self) -> None:
        data = _make_gzip_bomb(64)
        assert detect_compression_from_signature(data) == Compression.GZIP

    def test_bz2_detected(self) -> None:
        data = _make_bz2_bomb(64)
        assert detect_compression_from_signature(data) == Compression.BZ2

    def test_xz_detected(self) -> None:
        data = _make_xz_bomb(64)
        assert detect_compression_from_signature(data) == Compression.XZ

    def test_zstd_detected(self) -> None:
        data = _make_zstd_bomb(64)
        assert detect_compression_from_signature(data) == Compression.ZSTD

    def test_uncompressed_returns_none(self) -> None:
        assert detect_compression_from_signature(b"hello world") is None

    def test_empty_returns_none(self) -> None:
        assert detect_compression_from_signature(b"") is None

    @given(data=st.binary(max_size=100))
    def test_random_bytes_never_crash(self, data: bytes) -> None:
        result = detect_compression_from_signature(data)
        assert result is None or isinstance(result, Compression)


class TestCreateDecompressorRobustness:
    def test_gzip_decompressor_handles_truncated_body(self) -> None:
        bomb = _make_gzip_bomb(BOMB_SIZE)
        truncated = bomb[:20]
        decompressor = create_decompressor(Compression.GZIP)
        try:
            decompressor.decompress(truncated)
        except (zlib.error, EOFError, RuntimeError):
            pass

    def test_gzip_decompressor_handles_corrupted_body(self) -> None:
        bomb = _make_gzip_bomb(1024)
        corrupted = bomb[:10] + b"\xff\xfe\xfd" * 10 + bomb[40:]
        decompressor = create_decompressor(Compression.GZIP)
        try:
            decompressor.decompress(corrupted)
        except (zlib.error, EOFError, RuntimeError):
            pass

    def test_bz2_decompressor_handles_truncated_body(self) -> None:
        bomb = _make_bz2_bomb(BOMB_SIZE)
        truncated = bomb[:20]
        decompressor = create_decompressor(Compression.BZ2)
        try:
            decompressor.decompress(truncated)
        except (EOFError, OSError, RuntimeError, ValueError):
            pass

    def test_bz2_decompressor_handles_corrupted_body(self) -> None:
        bomb = _make_bz2_bomb(1024)
        corrupted = bomb[:3] + b"\xff\xfe\xfd" * 10 + bomb[33:]
        decompressor = create_decompressor(Compression.BZ2)
        try:
            decompressor.decompress(corrupted)
        except (EOFError, OSError, RuntimeError, ValueError):
            pass

    def test_xz_decompressor_handles_truncated_body(self) -> None:
        bomb = _make_xz_bomb(BOMB_SIZE)
        truncated = bomb[:20]
        decompressor = create_decompressor(Compression.XZ)
        try:
            decompressor.decompress(truncated)
        except (lzma.LZMAError, EOFError, RuntimeError):
            pass

    def test_xz_decompressor_handles_corrupted_body(self) -> None:
        bomb = _make_xz_bomb(1024)
        corrupted = bomb[:6] + b"\xff\xfe\xfd" * 10 + bomb[36:]
        decompressor = create_decompressor(Compression.XZ)
        try:
            decompressor.decompress(corrupted)
        except (lzma.LZMAError, EOFError, RuntimeError):
            pass

    def test_zstd_decompressor_handles_truncated_body(self) -> None:
        import sys

        if sys.version_info >= (3, 14):
            from compression import zstd
        else:
            from backports import zstd

        bomb = _make_zstd_bomb(BOMB_SIZE)
        truncated = bomb[:20]
        decompressor = create_decompressor(Compression.ZSTD)
        try:
            decompressor.decompress(truncated)
        except (zstd.ZstdError, EOFError, RuntimeError):
            pass

    def test_zstd_decompressor_handles_corrupted_body(self) -> None:
        import sys

        if sys.version_info >= (3, 14):
            from compression import zstd
        else:
            from backports import zstd

        bomb = _make_zstd_bomb(1024)
        corrupted = bomb[:4] + b"\xff\xfe\xfd" * 10 + bomb[34:]
        decompressor = create_decompressor(Compression.ZSTD)
        try:
            decompressor.decompress(corrupted)
        except (zstd.ZstdError, EOFError, RuntimeError):
            pass


class TestDecompressorWithRandomData:
    @given(data=st.binary(min_size=3, max_size=200))
    def test_gzip_header_plus_random_body(self, data: bytes) -> None:
        gzip_header = b"\x1f\x8b\x08"
        payload = gzip_header + data
        decompressor = create_decompressor(Compression.GZIP)
        try:
            decompressor.decompress(payload)
        except (zlib.error, EOFError, RuntimeError):
            pass

    @given(data=st.binary(min_size=3, max_size=200))
    def test_bz2_header_plus_random_body(self, data: bytes) -> None:
        bz2_header = b"\x42\x5a\x68"
        payload = bz2_header + data
        decompressor = create_decompressor(Compression.BZ2)
        try:
            decompressor.decompress(payload)
        except (EOFError, OSError, RuntimeError, ValueError):
            pass

    @given(data=st.binary(min_size=6, max_size=200))
    def test_xz_header_plus_random_body(self, data: bytes) -> None:
        xz_header = b"\xfd\x37\x7a\x58\x5a\x00"
        payload = xz_header + data
        decompressor = create_decompressor(Compression.XZ)
        try:
            decompressor.decompress(payload)
        except (lzma.LZMAError, EOFError, RuntimeError):
            pass

    @given(data=st.binary(min_size=4, max_size=200))
    def test_zstd_header_plus_random_body(self, data: bytes) -> None:
        import sys

        if sys.version_info >= (3, 14):
            from compression import zstd
        else:
            from backports import zstd

        zstd_header = b"\x28\xb5\x2f\xfd"
        payload = zstd_header + data
        decompressor = create_decompressor(Compression.ZSTD)
        try:
            decompressor.decompress(payload)
        except (zstd.ZstdError, EOFError, RuntimeError):
            pass


class TestAutoDecompressIteratorRobustness:
    @pytest.mark.anyio
    async def test_gzip_bomb_does_not_crash(self) -> None:
        bomb_data = _make_gzip_bomb(BOMB_SIZE)

        async def source():
            yield bomb_data

        iterator = AutoDecompressIterator(source=source().__aiter__())
        chunks = []
        try:
            async for chunk in iterator:
                chunks.append(chunk)
                if sum(len(c) for c in chunks) > BOMB_SIZE * 2:
                    break
        except (RuntimeError, StopAsyncIteration):
            pass

    @pytest.mark.anyio
    async def test_truncated_gzip_stream(self) -> None:
        bomb_data = _make_gzip_bomb(1024)
        truncated = bomb_data[:15]

        async def source():
            yield truncated

        iterator = AutoDecompressIterator(source=source().__aiter__())
        try:
            async for _ in iterator:
                pass
        except (RuntimeError, StopAsyncIteration, zlib.error):
            pass

    @pytest.mark.anyio
    async def test_empty_stream(self) -> None:
        async def source():
            return
            yield

        iterator = AutoDecompressIterator(source=source().__aiter__())
        chunks = []
        async for chunk in iterator:
            chunks.append(chunk)
        assert chunks == []

    @pytest.mark.anyio
    async def test_uncompressed_passthrough(self) -> None:
        data = b"hello world, this is plain text"

        async def source():
            yield data

        iterator = AutoDecompressIterator(source=source().__aiter__())
        chunks = []
        async for chunk in iterator:
            chunks.append(chunk)
        assert b"".join(chunks) == data

    @pytest.mark.anyio
    async def test_valid_gzip_small_chunks(self) -> None:
        original = b"test data for compression"
        compressed = gzip.compress(original)
        chunk_size = 4

        async def source():
            for i in range(0, len(compressed), chunk_size):
                yield compressed[i : i + chunk_size]

        iterator = AutoDecompressIterator(source=source().__aiter__())
        chunks = []
        try:
            async for chunk in iterator:
                chunks.append(chunk)
        except (RuntimeError, StopAsyncIteration):
            pass
