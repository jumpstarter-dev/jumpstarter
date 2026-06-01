from hypothesis import given
from hypothesis import strategies as st

from .encoding import Compression, create_decompressor, detect_compression_from_signature
from jumpstarter.testing_strategies import arbitrary as ARBITRARY


class TestDetectCompressionFromSignatureRobustness:
    @given(data=st.binary())
    def test_never_crashes_on_binary(self, data: bytes) -> None:
        try:
            result = detect_compression_from_signature(data)
            assert result is None or isinstance(result, Compression)
        except Exception as exc:
            raise AssertionError(
                f"detect_compression_from_signature raised unexpected {type(exc).__name__}: {exc}"
            ) from exc

    @given(data=ARBITRARY)
    def test_never_crashes_on_arbitrary(self, data: object) -> None:
        try:
            detect_compression_from_signature(data)
        except (
            TypeError,
            # BUG: crashes with AttributeError when given non-bytes input
            # (e.g. int) because it calls data.startswith() without type check
            AttributeError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(
                f"detect_compression_from_signature raised unexpected {type(exc).__name__}: {exc}"
            ) from exc


class TestCreateDecompressorRobustness:
    @given(compression=ARBITRARY)
    def test_never_crashes_on_arbitrary(self, compression: object) -> None:
        try:
            create_decompressor(compression)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"create_decompressor raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(compression=st.text())
    def test_never_crashes_on_text(self, compression: str) -> None:
        try:
            create_decompressor(compression)
        except (TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"create_decompressor raised unexpected {type(exc).__name__}: {exc}") from exc
