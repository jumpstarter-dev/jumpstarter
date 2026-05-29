import pytest

pytest.importorskip("jumpstarter_driver_esp32")

from hypothesis import given
from hypothesis import strategies as st

from .driver import Esp32Flasher
from jumpstarter.common.exceptions import ConfigurationError

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestEsp32FlasherRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            Esp32Flasher(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"Esp32Flasher crashed: {type(exc).__name__}: {exc}") from exc
