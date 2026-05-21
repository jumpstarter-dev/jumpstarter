from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner
from jumpstarter_kubernetes import ClientsV1Alpha1Api

from .rotate import rotate


@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
def test_rotate_client_basic(_mock_rotate, _mock_kube):
    """Basic rotate prints confirmation."""
    runner = CliRunner()
    result = runner.invoke(rotate, ["client", "my-client"])
    assert result.exit_code == 0
    assert "Rotating token" in result.output
    assert "Token rotated" in result.output


@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
def test_rotate_client_no_name(_mock_rotate, _mock_kube):
    """Rotate without name errors."""
    runner = CliRunner()
    result = runner.invoke(rotate, ["client"])
    assert result.exit_code != 0


@patch("jumpstarter_cli_admin.rotate.ClientConfigV1Alpha1")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
@patch.object(ClientsV1Alpha1Api, "get_client_config", new_callable=AsyncMock)
def test_rotate_client_save_existing_config(mock_get_config, mock_rotate, _mock_kube, mock_config_cls):
    """--save updates existing local config with new token."""
    mock_config = Mock()
    mock_config.token = "old-token"
    mock_config_cls.exists.return_value = True
    mock_config_cls.load.return_value = mock_config

    runner = CliRunner()
    result = runner.invoke(rotate, ["client", "my-client", "--save"])
    assert result.exit_code == 0
    assert "updated with new token" in result.output
    assert mock_config.token == "new-token-value"
    mock_config_cls.save.assert_called_once()


@patch("jumpstarter_cli_admin.rotate.ClientConfigV1Alpha1")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
@patch.object(ClientsV1Alpha1Api, "get_client_config", new_callable=AsyncMock)
def test_rotate_client_save_no_existing_config(mock_get_config, mock_rotate, _mock_kube, mock_config_cls):
    """--save fetches config from cluster when no local config exists."""
    mock_config_cls.exists.return_value = False
    mock_client_config = Mock()
    mock_get_config.return_value = mock_client_config

    runner = CliRunner()
    result = runner.invoke(rotate, ["client", "my-client", "--save"])
    assert result.exit_code == 0
    assert "updated with new token" in result.output
    mock_config_cls.save.assert_called_once_with(mock_client_config, None)


@patch("jumpstarter_cli_admin.rotate.ClientConfigV1Alpha1")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
@patch.object(ClientsV1Alpha1Api, "get_client_config", new_callable=AsyncMock)
def test_rotate_client_out_file(mock_get_config, mock_rotate, _mock_kube, mock_config_cls):
    """--out saves config to specified file path."""
    mock_config_cls.exists.return_value = False
    mock_client_config = Mock()
    mock_get_config.return_value = mock_client_config

    runner = CliRunner()
    result = runner.invoke(rotate, ["client", "my-client", "--out", "/tmp/test-config.yaml"])
    assert result.exit_code == 0
    # Click resolve_path=True resolves /tmp → /private/tmp on macOS
    call_args = mock_config_cls.save.call_args
    assert call_args[0][0] is mock_client_config
    assert call_args[0][1].endswith("/tmp/test-config.yaml")


@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
@patch.object(ClientsV1Alpha1Api, "rotate_client_token", new_callable=AsyncMock, return_value="new-token-value")
def test_rotate_client_name_only_output(mock_rotate, _mock_kube):
    """--output name prints only name."""
    runner = CliRunner()
    result = runner.invoke(rotate, ["client", "my-client", "--output", "name"])
    assert result.exit_code == 0
    assert "Rotating" not in result.output
