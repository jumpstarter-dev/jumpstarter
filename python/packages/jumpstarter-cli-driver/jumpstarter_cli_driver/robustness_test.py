import click
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

ALLOWED_CLI_EXCEPTIONS = (
    SystemExit,
    click.exceptions.BadParameter,
    click.exceptions.UsageError,
    click.exceptions.MissingParameter,
    click.Abort,
)


class TestDriverListRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_driver_list_never_crashes(self, args: list[str]) -> None:
        from . import driver

        runner = CliRunner()
        try:
            runner.invoke(driver, ["list", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"driver list crashed: {type(exc).__name__}: {exc}") from exc


class TestDriverVersionRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_driver_version_never_crashes(self, args: list[str]) -> None:
        from . import driver

        runner = CliRunner()
        try:
            runner.invoke(driver, ["version", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"driver version crashed: {type(exc).__name__}: {exc}") from exc


class TestDriverTopLevelRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_driver_never_crashes_on_garbage(self, args: list[str]) -> None:
        from . import driver

        runner = CliRunner()
        try:
            runner.invoke(driver, args, catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"driver CLI crashed: {type(exc).__name__}: {exc}") from exc
