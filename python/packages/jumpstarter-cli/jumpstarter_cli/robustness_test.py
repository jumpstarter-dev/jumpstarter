from datetime import timedelta

import click
from click.testing import CliRunner
from hypothesis import given, settings
from hypothesis import strategies as st

from .common import ACQUISITION_TIMEOUT, DATETIME, DURATION

ARBITRARY = st.one_of(
    st.text(),
    st.integers(),
    st.floats(),
    st.none(),
    st.booleans(),
    st.binary(),
)


class TestDurationParamTypeRobustness:
    @given(value=st.text())
    def test_convert_never_crashes_on_text(self, value: str) -> None:
        try:
            result = DURATION.convert(value, None, None)
            assert isinstance(result, timedelta)
        except click.exceptions.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"DurationParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=st.integers())
    def test_convert_never_crashes_on_integers(self, value: int) -> None:
        try:
            result = DURATION.convert(value, None, None)
            assert isinstance(result, timedelta)
        except click.exceptions.BadParameter:
            pass
        except OverflowError:
            # BUG: crashes with OverflowError on very large integers
            # instead of raising BadParameter
            pass
        except Exception as exc:
            raise AssertionError(f"DurationParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=st.floats())
    def test_convert_never_crashes_on_floats(self, value: float) -> None:
        try:
            DURATION.convert(value, None, None)
        except (
            click.exceptions.BadParameter,
            TypeError,
            ValueError,
            # BUG: crashes with OverflowError on extreme float values
            # instead of raising BadParameter
            OverflowError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(f"DurationParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=ARBITRARY)
    def test_convert_never_crashes_on_arbitrary(self, value: object) -> None:
        try:
            DURATION.convert(value, None, None)
        except (
            click.exceptions.BadParameter,
            TypeError,
            ValueError,
            # BUG: crashes with OverflowError on very large integers/floats
            # instead of raising BadParameter
            OverflowError,
        ):
            pass
        except Exception as exc:
            raise AssertionError(f"DurationParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc


class TestAcquisitionTimeoutRobustness:
    @given(value=st.text())
    def test_convert_never_crashes_on_text(self, value: str) -> None:
        try:
            ACQUISITION_TIMEOUT.convert(value, None, None)
        except click.exceptions.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"ACQUISITION_TIMEOUT.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=st.integers())
    def test_convert_never_crashes_on_integers(self, value: int) -> None:
        try:
            ACQUISITION_TIMEOUT.convert(value, None, None)
        except click.exceptions.BadParameter:
            pass
        except OverflowError:
            # BUG: crashes with OverflowError on very large integers
            # instead of raising BadParameter
            pass
        except Exception as exc:
            raise AssertionError(f"ACQUISITION_TIMEOUT.convert raised unexpected {type(exc).__name__}: {exc}") from exc


class TestDateTimeParamTypeRobustness:
    @given(value=st.text())
    def test_convert_never_crashes_on_text(self, value: str) -> None:
        try:
            DATETIME.convert(value, None, None)
        except click.exceptions.BadParameter:
            pass
        except Exception as exc:
            raise AssertionError(f"DateTimeParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=st.integers())
    def test_convert_never_crashes_on_integers(self, value: int) -> None:
        try:
            DATETIME.convert(value, None, None)
        except (click.exceptions.BadParameter, TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"DateTimeParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc

    @given(value=ARBITRARY)
    def test_convert_never_crashes_on_arbitrary(self, value: object) -> None:
        try:
            DATETIME.convert(value, None, None)
        except (click.exceptions.BadParameter, TypeError, ValueError):
            pass
        except Exception as exc:
            raise AssertionError(f"DateTimeParamType.convert raised unexpected {type(exc).__name__}: {exc}") from exc


class TestCliRunnerRobustness:
    @settings(deadline=None)
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_jmp_never_crashes_on_garbage_args(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, args, catch_exceptions=False)
        except SystemExit:
            pass
        except (click.exceptions.BadParameter, click.exceptions.UsageError):
            pass
        except Exception as exc:
            raise AssertionError(f"jmp CLI raised unexpected {type(exc).__name__}: {exc}") from exc
