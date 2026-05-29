import pytest

pytest.importorskip("jumpstarter_driver_renode")

from hypothesis import given
from hypothesis import strategies as st

from .driver import RenodeFlasher

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestRenodeFlasherRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            RenodeFlasher(**kwargs)
        except (TypeError, ValueError, FileNotFoundError, OSError, RuntimeError, NotImplementedError):
            pass
        except Exception as exc:
            raise AssertionError(f"RenodeFlasher crashed: {type(exc).__name__}: {exc}") from exc
