import pytest

pytest.importorskip("jumpstarter_driver_dutlink")

from hypothesis import given
from hypothesis import strategies as st

from .driver import DutlinkConfig

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestDutlinkConfigRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            DutlinkConfig(**kwargs)
        except (TypeError, ValueError, FileNotFoundError, RuntimeError, OSError, ImportError):
            pass
        except Exception as exc:
            raise AssertionError(f"DutlinkConfig crashed: {type(exc).__name__}: {exc}") from exc
