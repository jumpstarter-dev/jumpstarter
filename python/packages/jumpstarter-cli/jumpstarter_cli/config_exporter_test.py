from pathlib import PosixPath
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from .config_exporter import config_exporter


def test_edit_passes_string_filename_to_click_edit():
    mock_config = MagicMock()
    mock_config.path = PosixPath("/etc/jumpstarter/exporters/default.yaml")

    with patch(
        "jumpstarter_cli.config_exporter.ExporterConfigV1Alpha1"
    ) as mock_exporter_cls, patch(
        "jumpstarter_cli.config_exporter.click.edit"
    ) as mock_edit:
        mock_exporter_cls.load.return_value = mock_config

        runner = CliRunner()
        runner.invoke(config_exporter, ["edit", "default"])

        mock_edit.assert_called_once()
        filename_arg = mock_edit.call_args[1]["filename"]
        assert isinstance(filename_arg, str), (
            f"Expected str but got {type(filename_arg).__name__}"
        )


def test_edit_nonexistent_exporter_shows_error():
    with patch(
        "jumpstarter_cli.config_exporter.ExporterConfigV1Alpha1"
    ) as mock_exporter_cls:
        mock_exporter_cls.load.side_effect = FileNotFoundError

        runner = CliRunner()
        result = runner.invoke(config_exporter, ["edit", "nonexistent"])

        assert result.exit_code != 0
        assert "does not exist" in result.output
