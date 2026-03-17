from click.testing import CliRunner

from .jmp import jmp


def test_completion_bash():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "bash"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower()
    assert "jmp" in result.output.lower()


def test_completion_zsh():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "zsh"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "compdef" in result.output.lower()


def test_completion_fish():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "fish"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower()
    assert "--command jmp" in result.output.lower()


def test_completion_no_args():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion"])
    assert result.exit_code == 2
    assert "Missing argument" in result.output or "bash" in result.output


def test_completion_unsupported_shell():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "powershell"])
    assert result.exit_code == 2
    assert "Invalid value" in result.output or "powershell" in result.output
