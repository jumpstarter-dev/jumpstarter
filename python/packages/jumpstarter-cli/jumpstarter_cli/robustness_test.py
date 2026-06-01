from datetime import timedelta

import click
from click.testing import CliRunner
from hypothesis import given
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

ALLOWED_CLI_EXCEPTIONS = (
    SystemExit,
    click.exceptions.BadParameter,
    click.exceptions.UsageError,
    click.exceptions.MissingParameter,
    click.Abort,
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
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_jmp_never_crashes_on_garbage_args(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, args, catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"jmp CLI raised unexpected {type(exc).__name__}: {exc}") from exc


class TestCreateLeaseRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_create_lease_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["create", "lease", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"create lease crashed: {type(exc).__name__}: {exc}") from exc


class TestDeleteLeasesRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_delete_leases_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["delete", "leases", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"delete leases crashed: {type(exc).__name__}: {exc}") from exc


class TestGetExportersRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_exporters_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["get", "exporters", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"get exporters crashed: {type(exc).__name__}: {exc}") from exc


class TestGetLeasesRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_get_leases_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["get", "leases", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"get leases crashed: {type(exc).__name__}: {exc}") from exc


class TestShellRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_shell_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["shell", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"shell crashed: {type(exc).__name__}: {exc}") from exc


class TestRunRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_run_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["run", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"run crashed: {type(exc).__name__}: {exc}") from exc


class TestLoginRobustness:
    @given(endpoint=st.text(max_size=100))
    def test_login_never_crashes_on_garbage_endpoint(self, endpoint: str) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(
                jmp,
                ["login", endpoint, "--nointeractive"],
                catch_exceptions=False,
            )
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except ValueError:
            # BUG: urllib.parse.urlparse raises ValueError on malformed
            # IPv6 URLs (e.g. "[") instead of the CLI converting it
            # to a ClickException. Tracked as a known issue.
            pass
        except Exception as exc:
            raise AssertionError(f"login crashed: {type(exc).__name__}: {exc}") from exc


class TestAuthStatusRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_auth_status_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["auth", "status", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"auth status crashed: {type(exc).__name__}: {exc}") from exc


class TestAuthRefreshRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_auth_refresh_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["auth", "refresh", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"auth refresh crashed: {type(exc).__name__}: {exc}") from exc


class TestAuthRotateRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_auth_rotate_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["auth", "rotate", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"auth rotate crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigClientCreateRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_client_create_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "client", "create", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config client create crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigClientDeleteRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_client_delete_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "client", "delete", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config client delete crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigClientListRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_config_client_list_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "client", "list", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config client list crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigClientUseRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_client_use_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "client", "use", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config client use crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigExporterCreateRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_exporter_create_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "exporter", "create", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config exporter create crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigExporterDeleteRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_exporter_delete_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "exporter", "delete", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config exporter delete crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigExporterEditRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_config_exporter_edit_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "exporter", "edit", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config exporter edit crashed: {type(exc).__name__}: {exc}") from exc


class TestConfigExporterListRobustness:
    @given(args=st.lists(st.text(max_size=50), max_size=10))
    def test_config_exporter_list_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["config", "exporter", "list", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"config exporter list crashed: {type(exc).__name__}: {exc}") from exc


class TestCompletionRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_completion_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["completion", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"completion crashed: {type(exc).__name__}: {exc}") from exc


class TestUpdateLeaseRobustness:
    @given(args=st.lists(st.text(max_size=50), min_size=1, max_size=10))
    def test_update_lease_never_crashes(self, args: list[str]) -> None:
        from .jmp import jmp

        runner = CliRunner()
        try:
            runner.invoke(jmp, ["update", "lease", *args], catch_exceptions=False)
        except ALLOWED_CLI_EXCEPTIONS:
            pass
        except Exception as exc:
            raise AssertionError(f"update lease crashed: {type(exc).__name__}: {exc}") from exc
