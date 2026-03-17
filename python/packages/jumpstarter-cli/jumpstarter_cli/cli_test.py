from click.testing import CliRunner

from .jmp import jmp


def test_cli():
    runner = CliRunner()
    result = runner.invoke(jmp, [])
    for subcommand in [
        "config",
        "create",
        "delete",
        "driver",
        "get",
        "login",
        "run",
        "shell",
        "update",
        "version",
    ]:
        assert subcommand in result.output


class TestDeleteLeasesShortFlags:
    def test_delete_leases_accepts_short_a_flag(self):
        from .delete import delete_leases

        all_option = next(
            param for param in delete_leases.params if param.name == "delete_all"
        )
        assert "-a" in all_option.opts


class TestAuthStatusShortFlags:
    def test_token_status_accepts_short_v_flag(self):
        from .auth import token_status

        verbose_option = next(
            param for param in token_status.params if param.name == "verbose"
        )
        assert "-v" in verbose_option.opts
