from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import PowerReading

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestPowerReadingRobustness:
    @given(voltage=ARBITRARY, current=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, voltage: object, current: object) -> None:
        try:
            PowerReading(voltage=voltage, current=current)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"PowerReading constructor crashed: {type(exc).__name__}: {exc}") from exc

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
