from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from .config_exporter import config_exporter
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


def _write_system_config(path: Path, name: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: {name}
endpoint: "jumpstarter.my-lab.com:1443"
token: "test-token"
""",
        encoding="utf-8",
    )


@pytest.mark.parametrize("bad_alias", ["../evil", "/tmp/evil", "a/b", ".", ".."])
def test_invalid_alias_rejected_by_cli(bad_alias: str):
    """Commands that accept an alias reject traversal/invalid aliases with a Click error, not a traceback."""
    runner = CliRunner()
    for subcmd in (["create", bad_alias], ["delete", bad_alias], ["edit", bad_alias]):
        result = runner.invoke(config_exporter, subcmd)
        assert result.exit_code != 0, f"{subcmd} should fail for alias {bad_alias!r}"
        assert "Invalid exporter alias" in result.output, f"Expected error message in output for {subcmd}"


def test_create_shadows_system_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Creating a user config is allowed over a system config, and blocked when a user config already exists."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    # A config exists only in the system location.
    _write_system_config(system_path / "myexporter.yaml", "myexporter")

    runner = CliRunner()
    # Creating a user-level config of the same alias is allowed (it shadows the system one).
    result = runner.invoke(
        config_exporter,
        ["create", "myexporter"],
        input="default\nmyexporter\njumpstarter.my-lab.com:1443\ntest-token\n",
    )
    assert result.exit_code == 0, result.output
    assert (user_path / "myexporter.yaml").exists()
    assert ExporterConfigV1Alpha1.load("myexporter").path == user_path / "myexporter.yaml"

    # A second create now fails because a user-level config already exists.
    result = runner.invoke(
        config_exporter,
        ["create", "myexporter"],
        input="default\nmyexporter\njumpstarter.my-lab.com:1443\ntest-token\n",
    )
    assert result.exit_code != 0
    assert "exists" in result.output


def test_delete_system_only_shows_click_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Deleting an alias that exists only in the system location exits non-zero with a user-facing error."""
    user_path = tmp_path / "user"
    system_path = tmp_path / "system"
    monkeypatch.setattr(ExporterConfigV1Alpha1, "BASE_PATH", user_path)
    monkeypatch.setattr(ExporterConfigV1Alpha1, "SYSTEM_CONFIG_PATH", system_path)

    _write_system_config(system_path / "sys.yaml", "sys")

    runner = CliRunner()
    result = runner.invoke(config_exporter, ["delete", "sys"])

    assert result.exit_code != 0
    assert "system location" in result.output


def test_edit_passes_string_filename_to_click_edit():
    mock_config = MagicMock()
    mock_config.path = Path("/etc/jumpstarter/exporters/default.yaml")

    with patch(
        "jumpstarter_cli.config_exporter.ExporterConfigV1Alpha1"
    ) as mock_exporter_cls, patch(
        "jumpstarter_cli.config_exporter.click.edit"
    ) as mock_edit:
        mock_exporter_cls.load.return_value = mock_config

        runner = CliRunner()
        result = runner.invoke(config_exporter, ["edit", "default"])

        assert result.exit_code == 0
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
