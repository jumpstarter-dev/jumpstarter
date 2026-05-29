import pytest

pytest.importorskip("jumpstarter_driver_pi_pico")

from hypothesis import given
from hypothesis import strategies as st

from .driver import PiPicoFlasher

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestPiPicoFlasherRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            PiPicoFlasher(**kwargs)
        except (TypeError, ValueError, FileNotFoundError, OSError, RuntimeError, NotImplementedError):
            pass
        except Exception as exc:
            raise AssertionError(f"PiPicoFlasher crashed: {type(exc).__name__}: {exc}") from exc
