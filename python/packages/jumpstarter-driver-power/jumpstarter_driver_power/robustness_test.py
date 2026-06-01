from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import PowerReading
from jumpstarter.testing_strategies import ARBITRARY


class TestPowerReadingRobustness:
    @given(voltage=ARBITRARY, current=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, voltage: object, current: object) -> None:
        try:
            reading = cast(Any, PowerReading)(voltage=voltage, current=current)
        except (TypeError, ValueError, ValidationError):
            return
        except Exception as exc:
            raise AssertionError(f"PowerReading constructor crashed: {type(exc).__name__}: {exc}") from exc
        assert isinstance(reading.voltage, (int, float))
        assert isinstance(reading.current, (int, float))

    @given(voltage=st.floats(), current=st.floats())
    def test_constructor_accepts_floats(self, voltage: float, current: float) -> None:
        try:
            reading = PowerReading(voltage=voltage, current=current)
            assert isinstance(reading.voltage, float)
            assert isinstance(reading.current, float)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"PowerReading constructor crashed: {type(exc).__name__}: {exc}") from exc

    @given(
        voltage=st.floats(allow_nan=False, allow_infinity=False),
        current=st.floats(allow_nan=False, allow_infinity=False),
    )
    def test_apparent_power_never_crashes(self, voltage: float, current: float) -> None:
        try:
            reading = PowerReading(voltage=voltage, current=current)
            power = reading.apparent_power
            assert isinstance(power, float)
        except (TypeError, ValueError, ValidationError, OverflowError):
            pass
        except Exception as exc:
            raise AssertionError(f"apparent_power crashed: {type(exc).__name__}: {exc}") from exc
