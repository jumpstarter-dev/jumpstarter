import bz2
import gzip
import lzma
import sys

from hypothesis import given
from hypothesis import strategies as st

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd

from .encoding import COMPRESSION_SIGNATURES, Compression, create_decompressor, detect_compression_from_signature

known_signatures: list[bytes] = [sig.signature for sig in COMPRESSION_SIGNATURES]


def _starts_with_any_signature(data: bytes) -> bool:
    return any(data.startswith(sig) for sig in known_signatures)


class TestDetectCompressionFromSignatureProperties:
    @given(data=st.binary(min_size=0, max_size=200).filter(lambda b: not _starts_with_any_signature(b)))
    def test_non_matching_data_returns_none(self, data: bytes) -> None:
        assert detect_compression_from_signature(data) is None

    @given(
        compression=st.sampled_from(list(Compression)),
        suffix=st.binary(min_size=0, max_size=100),
    )
    def test_signature_prefix_detects_format(self, compression: Compression, suffix: bytes) -> None:
        sig = next(s.signature for s in COMPRESSION_SIGNATURES if s.compression == compression)
        data = sig + suffix
        assert detect_compression_from_signature(data) == compression


class TestCompressionRealDataDetection:
    @given(payload=st.binary(min_size=1, max_size=500))
    def test_gzip_compressed_detected(self, payload: bytes) -> None:
        compressed = gzip.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.GZIP

    @given(payload=st.binary(min_size=1, max_size=500))
    def test_xz_compressed_detected(self, payload: bytes) -> None:
        compressed = lzma.compress(payload, format=lzma.FORMAT_XZ)
        assert detect_compression_from_signature(compressed) == Compression.XZ

    @given(payload=st.binary(min_size=1, max_size=500))
    def test_bz2_compressed_detected(self, payload: bytes) -> None:
        compressed = bz2.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.BZ2

    @given(payload=st.binary(min_size=1, max_size=500))
    def test_zstd_compressed_detected(self, payload: bytes) -> None:
        compressed = zstd.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.ZSTD


class TestCorruptedCompressedData:
    @given(
        compression=st.sampled_from(list(Compression)),
        payload=st.binary(min_size=1, max_size=500),
        truncate_bytes=st.integers(min_value=1, max_value=100),
    )
    def test_truncated_data_raises_clean_error(
        self, compression: Compression, payload: bytes, truncate_bytes: int
    ) -> None:
        compressors = {
            Compression.GZIP: gzip.compress,
            Compression.XZ: lambda d: lzma.compress(d, format=lzma.FORMAT_XZ),
            Compression.BZ2: bz2.compress,
            Compression.ZSTD: zstd.compress,
        }
        compressed = compressors[compression](payload)
        if truncate_bytes >= len(compressed):
            return
        truncated = compressed[: len(compressed) - truncate_bytes]
        decompressor = create_decompressor(compression)
        try:
            decompressor.decompress(truncated)
            if hasattr(decompressor, "flush"):
                decompressor.flush()
        except Exception:
            pass

    @given(
        compression=st.sampled_from(list(Compression)),
        payload=st.binary(min_size=1, max_size=500),
        corruption_offset=st.integers(min_value=0),
        corruption_byte=st.binary(min_size=1, max_size=1),
    )
    def test_corrupted_byte_raises_clean_error(
        self,
        compression: Compression,
        payload: bytes,
        corruption_offset: int,
        corruption_byte: bytes,
    ) -> None:
        compressors = {
            Compression.GZIP: gzip.compress,
            Compression.XZ: lambda d: lzma.compress(d, format=lzma.FORMAT_XZ),
            Compression.BZ2: bz2.compress,
            Compression.ZSTD: zstd.compress,
        }
        compressed = compressors[compression](payload)
        if len(compressed) == 0:
            return
        offset = corruption_offset % len(compressed)
        corrupted = compressed[:offset] + corruption_byte + compressed[offset + 1 :]
        decompressor = create_decompressor(compression)
        try:
            decompressor.decompress(corrupted)
            if hasattr(decompressor, "flush"):
                decompressor.flush()
        except Exception:
            pass

    @given(
        compression=st.sampled_from(list(Compression)),
        random_data=st.binary(min_size=1, max_size=200),
    )
    def test_random_bytes_do_not_crash_decompressor(self, compression: Compression, random_data: bytes) -> None:
        decompressor = create_decompressor(compression)
        try:
            decompressor.decompress(random_data)
        except Exception:
            pass


class TestCreateDecompressorRoundtrip:
    @given(payload=st.binary(min_size=1, max_size=1000))
    def test_gzip_decompressor(self, payload: bytes) -> None:
        compressed = gzip.compress(payload)
        decompressor = create_decompressor(Compression.GZIP)
        result = decompressor.decompress(compressed)
        remaining = decompressor.flush()
        assert result + remaining == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    def test_bz2_decompressor(self, payload: bytes) -> None:
        compressed = bz2.compress(payload)
        decompressor = create_decompressor(Compression.BZ2)
        assert decompressor.decompress(compressed) == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    def test_xz_decompressor(self, payload: bytes) -> None:
        compressed = lzma.compress(payload, format=lzma.FORMAT_XZ)
        decompressor = create_decompressor(Compression.XZ)
        assert decompressor.decompress(compressed) == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    def test_zstd_decompressor(self, payload: bytes) -> None:
        compressed = zstd.compress(payload)
        decompressor = create_decompressor(Compression.ZSTD)
        assert decompressor.decompress(compressed) == payload
