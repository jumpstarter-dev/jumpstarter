from __future__ import annotations

import subprocess
import sys

import pytest
from click.testing import CliRunner

from .__main__ import EXIT_ERROR, main
from .cli import robot


def test_commands_have_help():
    runner = CliRunner()
    assert "generate" in runner.invoke(robot, ["--help"]).output
    result = runner.invoke(robot, ["generate", "--help"])
    assert result.exit_code == 0
    assert "--exporter-config" in result.output and "--capture" in result.output


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--capture", "--check"], "--capture and --check cannot be used together"),
        (["--selector", "a", "--tree", "t.json"], "--selector and --tree cannot be used together"),
    ],
)
def test_conflicting_options_are_usage_errors(argv, message, capsys):
    assert main(["generate", *argv]) == EXIT_ERROR
    assert message in capsys.readouterr().err


def test_importing_the_cli_does_not_load_robot_framework():
    """`jmp` imports the group at start; Robot Framework loads only when a command runs."""
    code = "import sys, jumpstarter_robot.cli; print('robot' in sys.modules)"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "False"


def test_jmp_has_the_robot_group():
    jmp = pytest.importorskip("jumpstarter_cli.jmp").jmp
    result = CliRunner().invoke(jmp, ["robot", "render", "--help"])
    assert result.exit_code == 0, result.output
    assert "device tree JSON" in result.output
