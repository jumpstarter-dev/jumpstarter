import pytest

pytest.importorskip("jumpstarter_driver_ridesx")

from hypothesis import given
from hypothesis import strategies as st

from .driver import RideSXDriver, RideSXPowerDriver
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.testing_strategies import ARBITRARY


class TestRideSXDriverRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            RideSXDriver(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"RideSXDriver crashed: {type(exc).__name__}: {exc}") from exc


class TestRideSXPowerDriverRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            RideSXPowerDriver(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"RideSXPowerDriver crashed: {type(exc).__name__}: {exc}") from exc
