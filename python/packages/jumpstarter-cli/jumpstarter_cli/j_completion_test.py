from unittest.mock import AsyncMock, MagicMock, patch

from anyio import run
from click.testing import CliRunner

from .j import _j_shell_complete, j_completion


def test_j_completion_bash_produces_script():
    runner = CliRunner()
    result = runner.invoke(j_completion, ["bash"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert "_J_COMPLETE" in result.output


def test_j_completion_zsh_produces_compdef():
    runner = CliRunner()
    result = runner.invoke(j_completion, ["zsh"])
    assert result.exit_code == 0
    assert "compdef" in result.output.lower()


def test_j_completion_fish_produces_complete_command():
    runner = CliRunner()
    result = runner.invoke(j_completion, ["fish"])
    assert result.exit_code == 0
    assert "complete" in result.output.lower()
    assert "--command j" in result.output.lower()


def test_j_completion_no_args_exits_with_error():
    runner = CliRunner()
    result = runner.invoke(j_completion, [])
    assert result.exit_code == 2


def test_j_completion_unsupported_shell_exits_with_error():
    runner = CliRunner()
    result = runner.invoke(j_completion, ["powershell"])
    assert result.exit_code == 2


def test_j_shell_complete_handles_system_exit_cleanly():
    mock_cli_group = MagicMock()
    mock_cli_group.side_effect = SystemExit(0)
    mock_client = MagicMock()
    mock_client.cli.return_value = mock_cli_group

    with patch("jumpstarter_cli.j.env_async") as mock_env:
        mock_env.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_env.return_value.__aexit__ = AsyncMock(return_value=False)
        run(_j_shell_complete)
        mock_client.cli.assert_called_once()
