import click
from click.testing import CliRunner

from .completion import create_completion_command


def _make_test_cli():
    @click.group()
    def test_cli():
        pass

    @test_cli.command()
    def hello():
        pass

    return test_cli


def test_create_completion_command_returns_click_command():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )
    assert isinstance(cmd, click.Command)
    assert cmd.name == "completion"


def test_factory_bash_output():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )

    @click.group()
    def wrapper():
        pass

    wrapper.add_command(cmd)
    runner = CliRunner()
    result = runner.invoke(wrapper, ["completion", "bash"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert "test-cli" in result.output.lower()


def test_factory_zsh_output():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )

    @click.group()
    def wrapper():
        pass

    wrapper.add_command(cmd)
    runner = CliRunner()
    result = runner.invoke(wrapper, ["completion", "zsh"])
    assert result.exit_code == 0
    assert "compdef" in result.output.lower()


def test_factory_fish_output():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )

    @click.group()
    def wrapper():
        pass

    wrapper.add_command(cmd)
    runner = CliRunner()
    result = runner.invoke(wrapper, ["completion", "fish"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert "--command test-cli" in result.output.lower()


def test_factory_missing_arg():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )

    @click.group()
    def wrapper():
        pass

    wrapper.add_command(cmd)
    runner = CliRunner()
    result = runner.invoke(wrapper, ["completion"])
    assert result.exit_code == 2


def test_factory_invalid_shell():
    cmd = create_completion_command(
        cli_group_getter=_make_test_cli,
        prog_name="test-cli",
        complete_var="_TEST_CLI_COMPLETE",
    )

    @click.group()
    def wrapper():
        pass

    wrapper.add_command(cmd)
    runner = CliRunner()
    result = runner.invoke(wrapper, ["completion", "powershell"])
    assert result.exit_code == 2
