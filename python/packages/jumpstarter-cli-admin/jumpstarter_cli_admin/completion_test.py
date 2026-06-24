from click.testing import CliRunner

from . import admin


def test_completion_bash_produces_script_with_jmp_admin():
    runner = CliRunner()
    result = runner.invoke(admin, ["completion", "bash"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower()
    assert "jmp-admin" in result.output.lower()


def test_completion_zsh_produces_compdef_for_jmp_admin():
    runner = CliRunner()
    result = runner.invoke(admin, ["completion", "zsh"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "compdef" in result.output.lower()


def test_completion_fish_produces_complete_command_for_jmp_admin():
    runner = CliRunner()
    result = runner.invoke(admin, ["completion", "fish"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower()
    assert "--command jmp-admin" in result.output.lower()


def test_completion_missing_argument_exits_with_error():
    runner = CliRunner()
    result = runner.invoke(admin, ["completion"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output or "bash" in result.output


def test_completion_unsupported_shell_exits_with_error():
    runner = CliRunner()
    result = runner.invoke(admin, ["completion", "powershell"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output or "powershell" in result.output
