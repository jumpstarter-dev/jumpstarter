from click.testing import CliRunner

from .jmp import jmp


def test_completion_bash():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "bash"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower() or "comp" in result.output.lower()


def test_completion_zsh():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "zsh"])
    assert result.exit_code == 0
    assert len(result.output) > 0


def test_completion_fish():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "fish"])
    assert result.exit_code == 0
    assert len(result.output) > 0
    assert "complete" in result.output.lower()


def test_completion_no_args():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion"])
    assert result.exit_code == 2


def test_completion_unsupported_shell():
    runner = CliRunner()
    result = runner.invoke(jmp, ["completion", "powershell"])
    assert result.exit_code == 2
