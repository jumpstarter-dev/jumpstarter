import pytest

pytest.importorskip("jumpstarter_driver_energenie")

from hypothesis import given
from hypothesis import strategies as st

from .driver import EnerGenie
from jumpstarter.common.exceptions import ConfigurationError

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestEnerGenieRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            EnerGenie(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"EnerGenie crashed: {type(exc).__name__}: {exc}") from exc
