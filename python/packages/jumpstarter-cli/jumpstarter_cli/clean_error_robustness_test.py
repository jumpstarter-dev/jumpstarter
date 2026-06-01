from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

TRACEBACK_MARKER = "Traceback (most recent call last)"


def _invoke_jmp(args: list[str]):
    from .jmp import jmp

    runner = CliRunner()
    return runner.invoke(jmp, args, catch_exceptions=True)


def _assert_no_traceback(result, command_desc: str) -> None:
    combined = result.output or ""
    assert TRACEBACK_MARKER not in combined, f"{command_desc} leaked a raw traceback to the user:\n{combined}"


class TestCreateLeaseCleanErrors:
    def test_no_args(self) -> None:
        result = _invoke_jmp(["create", "lease"])
        _assert_no_traceback(result, "create lease (no args)")
        assert result.exit_code != 0

    def test_garbage_duration(self) -> None:
        result = _invoke_jmp(["create", "lease", "--duration", "not-a-duration", "-l", "x=y"])
        _assert_no_traceback(result, "create lease (garbage duration)")
        assert result.exit_code != 0

    def test_missing_selector_and_name(self) -> None:
        result = _invoke_jmp(["create", "lease", "--duration", "1h"])
        _assert_no_traceback(result, "create lease (missing selector)")
        assert result.exit_code != 0

    @given(duration=st.text(min_size=1, max_size=30))
    def test_arbitrary_duration_no_traceback(self, duration: str) -> None:
        result = _invoke_jmp(["create", "lease", "--duration", duration, "-l", "x=y"])
        _assert_no_traceback(result, f"create lease --duration {duration!r}")


class TestDeleteLeasesCleanErrors:
    def test_no_args(self) -> None:
        result = _invoke_jmp(["delete", "leases"])
        _assert_no_traceback(result, "delete leases (no args)")
        assert result.exit_code != 0

    def test_nonexistent_lease(self) -> None:
        result = _invoke_jmp(["delete", "leases", "nonexistent-lease-xyz"])
        _assert_no_traceback(result, "delete leases (nonexistent)")

    @given(name=st.text(min_size=1, max_size=50))
    def test_arbitrary_name_no_traceback(self, name: str) -> None:
        result = _invoke_jmp(["delete", "leases", name])
        _assert_no_traceback(result, f"delete leases {name!r}")


class TestGetExportersCleanErrors:
    def test_invalid_selector(self) -> None:
        result = _invoke_jmp(["get", "exporters", "-l", "!!!invalid!!!"])
        _assert_no_traceback(result, "get exporters (invalid selector)")

    @given(selector=st.text(min_size=1, max_size=50))
    def test_arbitrary_selector_no_traceback(self, selector: str) -> None:
        result = _invoke_jmp(["get", "exporters", "-l", selector])
        _assert_no_traceback(result, f"get exporters -l {selector!r}")


class TestGetLeasesCleanErrors:
    def test_invalid_selector(self) -> None:
        result = _invoke_jmp(["get", "leases", "-l", "!!!invalid!!!"])
        _assert_no_traceback(result, "get leases (invalid selector)")

    @given(selector=st.text(min_size=1, max_size=50))
    def test_arbitrary_selector_no_traceback(self, selector: str) -> None:
        result = _invoke_jmp(["get", "leases", "-l", selector])
        _assert_no_traceback(result, f"get leases -l {selector!r}")


class TestShellCleanErrors:
    def test_no_config(self) -> None:
        result = _invoke_jmp(["shell"])
        _assert_no_traceback(result, "shell (no config)")

    @given(args=st.lists(st.text(max_size=30), min_size=1, max_size=5))
    def test_arbitrary_args_no_traceback(self, args: list[str]) -> None:
        result = _invoke_jmp(["shell", *args])
        _assert_no_traceback(result, f"shell {args!r}")


class TestRunCleanErrors:
    def test_no_args(self) -> None:
        result = _invoke_jmp(["run"])
        _assert_no_traceback(result, "run (no args)")
        assert result.exit_code != 0

    @given(args=st.lists(st.text(max_size=30), min_size=1, max_size=5))
    def test_arbitrary_args_no_traceback(self, args: list[str]) -> None:
        result = _invoke_jmp(["run", *args])
        _assert_no_traceback(result, f"run {args!r}")


class TestLoginCleanErrors:
    def test_no_args_shows_help(self) -> None:
        result = _invoke_jmp(["login"])
        _assert_no_traceback(result, "login (no args)")

    @given(endpoint=st.text(min_size=1, max_size=50))
    def test_arbitrary_endpoint_no_traceback(self, endpoint: str) -> None:
        result = _invoke_jmp(["login", endpoint, "--nointeractive"])
        _assert_no_traceback(result, f"login {endpoint!r}")


class TestAuthCleanErrors:
    def test_auth_status_no_config(self) -> None:
        result = _invoke_jmp(["auth", "status"])
        _assert_no_traceback(result, "auth status (no config)")

    def test_auth_refresh_no_config(self) -> None:
        result = _invoke_jmp(["auth", "refresh"])
        _assert_no_traceback(result, "auth refresh (no config)")

    def test_auth_rotate_no_config(self) -> None:
        result = _invoke_jmp(["auth", "rotate"])
        _assert_no_traceback(result, "auth rotate (no config)")


class TestConfigCleanErrors:
    def test_config_client_list(self) -> None:
        result = _invoke_jmp(["config", "client", "list"])
        _assert_no_traceback(result, "config client list")

    def test_config_exporter_list(self) -> None:
        result = _invoke_jmp(["config", "exporter", "list"])
        _assert_no_traceback(result, "config exporter list")

    def test_config_client_create_no_args(self) -> None:
        result = _invoke_jmp(["config", "client", "create"])
        _assert_no_traceback(result, "config client create (no args)")
        assert result.exit_code != 0

    def test_config_exporter_create_no_args(self) -> None:
        result = _invoke_jmp(["config", "exporter", "create"])
        _assert_no_traceback(result, "config exporter create (no args)")
        assert result.exit_code != 0


class TestCompletionCleanErrors:
    def test_completion_no_args(self) -> None:
        result = _invoke_jmp(["completion"])
        _assert_no_traceback(result, "completion (no args)")
        assert result.exit_code != 0

    @given(shell=st.text(min_size=1, max_size=20))
    def test_arbitrary_shell_no_traceback(self, shell: str) -> None:
        result = _invoke_jmp(["completion", shell])
        _assert_no_traceback(result, f"completion {shell!r}")


class TestUpdateLeaseCleanErrors:
    def test_no_args(self) -> None:
        result = _invoke_jmp(["update", "lease"])
        _assert_no_traceback(result, "update lease (no args)")
        assert result.exit_code != 0

    @given(name=st.text(min_size=1, max_size=50))
    def test_arbitrary_name_no_traceback(self, name: str) -> None:
        result = _invoke_jmp(["update", "lease", name])
        _assert_no_traceback(result, f"update lease {name!r}")


class TestUnknownCommandCleanErrors:
    def test_unknown_subcommand(self) -> None:
        result = _invoke_jmp(["nonexistent"])
        _assert_no_traceback(result, "nonexistent command")
        assert result.exit_code != 0

    @given(cmd=st.text(min_size=1, max_size=30))
    def test_arbitrary_command_no_traceback(self, cmd: str) -> None:
        result = _invoke_jmp([cmd])
        _assert_no_traceback(result, f"arbitrary command {cmd!r}")
