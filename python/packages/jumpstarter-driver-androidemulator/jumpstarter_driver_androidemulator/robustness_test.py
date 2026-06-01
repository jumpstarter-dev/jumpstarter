import pytest

pytest.importorskip("jumpstarter_driver_androidemulator")

from hypothesis import given
from hypothesis import strategies as st

from .driver import AndroidEmulator, AndroidEmulatorPower
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.testing_strategies import ARBITRARY


class TestAndroidEmulatorRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            AndroidEmulator(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"AndroidEmulator crashed: {type(exc).__name__}: {exc}") from exc


class TestAndroidEmulatorPowerRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            AndroidEmulatorPower(**kwargs)
        except (TypeError, ValueError, ConfigurationError, OSError, RuntimeError):
            pass
        except Exception as exc:
            raise AssertionError(f"AndroidEmulatorPower crashed: {type(exc).__name__}: {exc}") from exc
