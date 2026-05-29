import pytest

pytest.importorskip("jumpstarter_driver_tasmota")

from hypothesis import given
from hypothesis import strategies as st

from .driver import TasmotaPower

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestTasmotaPowerRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            TasmotaPower(**kwargs)
        except (TypeError, ValueError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"TasmotaPower crashed: {type(exc).__name__}: {exc}") from exc
