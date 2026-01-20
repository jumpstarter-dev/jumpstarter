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
