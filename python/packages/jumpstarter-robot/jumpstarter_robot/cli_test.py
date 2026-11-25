from __future__ import annotations

from click.testing import CliRunner

from .__main__ import EXIT_ERROR, main
from .cli import robot


def test_commands_have_help():
    runner = CliRunner()
    assert "generate" in runner.invoke(robot, ["--help"]).output
    result = runner.invoke(robot, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--exporter-config" in result.output and "--live" in result.output


def test_conflicting_options_are_usage_errors(capsys):
    assert main(["generate", "--selector", "a", "--lease", "b"]) == EXIT_ERROR
    assert "--selector and --lease cannot be used together" in capsys.readouterr().err
