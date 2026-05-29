import pytest

pytest.importorskip("jumpstarter_driver_sdwire")

from hypothesis import given
from hypothesis import strategies as st

from .driver import SDWire

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestSDWireRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            SDWire(**kwargs)
        except (TypeError, ValueError, FileNotFoundError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"SDWire crashed: {type(exc).__name__}: {exc}") from exc
