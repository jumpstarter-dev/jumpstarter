import bz2
import gzip
import lzma
import sys

from hypothesis import given, settings
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
    @settings(max_examples=100)
    def test_non_matching_data_returns_none(self, data: bytes) -> None:
        assert detect_compression_from_signature(data) is None

    @given(
        compression=st.sampled_from(list(Compression)),
        suffix=st.binary(min_size=0, max_size=100),
    )
    @settings(max_examples=50)
    def test_signature_prefix_detects_format(self, compression: Compression, suffix: bytes) -> None:
        sig = next(s.signature for s in COMPRESSION_SIGNATURES if s.compression == compression)
        data = sig + suffix
        assert detect_compression_from_signature(data) == compression


class TestCompressionRealDataDetection:
    @given(payload=st.binary(min_size=1, max_size=500))
    @settings(max_examples=30)
    def test_gzip_compressed_detected(self, payload: bytes) -> None:
        compressed = gzip.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.GZIP

    @given(payload=st.binary(min_size=1, max_size=500))
    @settings(max_examples=30)
    def test_xz_compressed_detected(self, payload: bytes) -> None:
        compressed = lzma.compress(payload, format=lzma.FORMAT_XZ)
        assert detect_compression_from_signature(compressed) == Compression.XZ

    @given(payload=st.binary(min_size=1, max_size=500))
    @settings(max_examples=30)
    def test_bz2_compressed_detected(self, payload: bytes) -> None:
        compressed = bz2.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.BZ2

    @given(payload=st.binary(min_size=1, max_size=500))
    @settings(max_examples=30)
    def test_zstd_compressed_detected(self, payload: bytes) -> None:
        compressed = zstd.compress(payload)
        assert detect_compression_from_signature(compressed) == Compression.ZSTD


class TestCreateDecompressorRoundtrip:
    @given(payload=st.binary(min_size=1, max_size=1000))
    @settings(max_examples=30)
    def test_gzip_decompressor(self, payload: bytes) -> None:
        compressed = gzip.compress(payload)
        decompressor = create_decompressor(Compression.GZIP)
        result = decompressor.decompress(compressed)
        remaining = decompressor.flush()
        assert result + remaining == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    @settings(max_examples=30)
    def test_bz2_decompressor(self, payload: bytes) -> None:
        compressed = bz2.compress(payload)
        decompressor = create_decompressor(Compression.BZ2)
        assert decompressor.decompress(compressed) == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    @settings(max_examples=30)
    def test_xz_decompressor(self, payload: bytes) -> None:
        compressed = lzma.compress(payload, format=lzma.FORMAT_XZ)
        decompressor = create_decompressor(Compression.XZ)
        assert decompressor.decompress(compressed) == payload

    @given(payload=st.binary(min_size=1, max_size=1000))
    @settings(max_examples=30)
    def test_zstd_decompressor(self, payload: bytes) -> None:
        compressed = zstd.compress(payload)
        decompressor = create_decompressor(Compression.ZSTD)
        assert decompressor.decompress(compressed) == payload
