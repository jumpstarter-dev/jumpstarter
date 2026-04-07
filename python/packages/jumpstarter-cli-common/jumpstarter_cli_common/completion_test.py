import click
from click.testing import CliRunner

from .completion import make_completion_command

PROG_NAME = "testcli"
COMPLETE_VAR = "_TESTCLI_COMPLETE"


def _make_test_group():
    @click.group()
    def cli():
        pass

    return cli


def _make_test_cli_with_completion():
    @click.group()
    def cli():
        pass

    cli.add_command(make_completion_command(_make_test_group, PROG_NAME, COMPLETE_VAR))
    return cli


def test_completion_bash_produces_completion_script():
    cli = _make_test_cli_with_completion()
    runner = CliRunner()
    result = runner.invoke(cli, ["completion", "bash"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert PROG_NAME in result.output.lower()


def test_completion_zsh_produces_compdef():
    cli = _make_test_cli_with_completion()
    runner = CliRunner()
    result = runner.invoke(cli, ["completion", "zsh"])
    assert result.exit_code == 0
    assert "compdef" in result.output.lower()


def test_completion_fish_produces_complete_command():
    cli = _make_test_cli_with_completion()
    runner = CliRunner()
    result = runner.invoke(cli, ["completion", "fish"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert f"--command {PROG_NAME}" in result.output.lower()


def test_completion_missing_argument_exits_with_error():
    cli = _make_test_cli_with_completion()
    runner = CliRunner()
    result = runner.invoke(cli, ["completion"])
    assert result.exit_code == 2


def test_completion_unsupported_shell_exits_with_error():
    cli = _make_test_cli_with_completion()
    runner = CliRunner()
    result = runner.invoke(cli, ["completion", "powershell"])
    assert result.exit_code == 2
