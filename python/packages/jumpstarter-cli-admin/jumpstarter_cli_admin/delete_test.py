from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    V1Alpha1Exporter,
    V1Alpha1ExporterStatus,
)
from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from .delete import delete
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1, UserConfigV1Alpha1Config

# Generate a random client name
CLIENT_NAME = "test"
# Default config path
CLIENT_CONFIG_PATH = ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH / (CLIENT_NAME + ".yaml")

CLIENT_ENDPOINT = "grpc://example.com:443"
CLIENT_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"

CLIENT_CONFIG = ClientConfigV1Alpha1(
    alias=CLIENT_NAME,
    metadata=ObjectMeta(namespace="default", name=CLIENT_NAME),
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)

USER_CONFIG_CURRENT = UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=CLIENT_CONFIG))
USER_CONFIG_NOT_CURRENT = UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None))


@patch.object(ClientConfigV1Alpha1, "delete")
@patch.object(ClientConfigV1Alpha1, "exists")
@patch.object(ClientsV1Alpha1Api, "delete_client")
@patch.object(UserConfigV1Alpha1, "load_or_create")
@patch.object(UserConfigV1Alpha1, "save")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
def test_delete_client(
    _mock_load_kube_config,
    mock_save_user_config: Mock,
    mock_load_or_create_user_config: Mock,
    mock_delete_client: AsyncMock,
    mock_config_exists: Mock,
    mock_config_delete: Mock,
):
    runner = CliRunner()

    # Delete client object and config does not exist
    mock_config_exists.return_value = False
    result = runner.invoke(delete, ["client", CLIENT_NAME])
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_load_or_create_user_config.reset_mock()
    mock_config_delete.reset_mock()

    # Delete client object and delete config prompt = n
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="n\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object, not current client config and delete config prompt = Y
    mock_config_exists.return_value = True
    mock_load_or_create_user_config.return_value = USER_CONFIG_NOT_CURRENT
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="Y\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_called_once()
    mock_config_delete.assert_called_once_with(CLIENT_NAME)
    mock_save_user_config.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object, current client config and delete config prompt = Y
    mock_config_exists.return_value = True
    mock_load_or_create_user_config.return_value = USER_CONFIG_CURRENT
    result = runner.invoke(delete, ["client", CLIENT_NAME], input="Y\n")
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_called_once()
    mock_config_delete.assert_called_once_with(CLIENT_NAME)
    # Ensure that the current client config was reset to NONE
    mock_save_user_config.assert_called_once_with(USER_CONFIG_NOT_CURRENT)

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object nointeractive
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME, "--nointeractive"])
    assert result.exit_code == 0
    assert f"Deleted client '{CLIENT_NAME}' in namespace 'default'" in result.output
    assert "Client configuration successfully deleted" not in result.output
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()

    # Delete client object output name
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["client", CLIENT_NAME, "--nointeractive", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == f"client.jumpstarter.dev/{CLIENT_NAME}\n"
    mock_delete_client.assert_called_once_with(CLIENT_NAME)
    mock_load_or_create_user_config.assert_not_called()
    mock_config_delete.assert_not_called()

    mock_load_or_create_user_config.reset_mock()
    mock_config_exists.reset_mock()
    mock_delete_client.reset_mock()
    mock_config_delete.reset_mock()
    mock_save_user_config.reset_mock()


EXPORTER_NAME = "test"
EXPORTER_ENDPOINT = "grpc://example.com:443"
EXPORTER_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
# Default config path
default_config_path = ExporterConfigV1Alpha1.BASE_PATH / (EXPORTER_NAME + ".yaml")
# Create a test exporter config
EXPORTER_OBJECT = V1Alpha1Exporter(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Exporter",
    metadata=V1ObjectMeta(namespace="default", name=EXPORTER_NAME, creation_timestamp="2024-01-01T21:00:00Z"),
    status=V1Alpha1ExporterStatus(
        endpoint=EXPORTER_ENDPOINT, credential=V1ObjectReference(name=f"{EXPORTER_NAME}-credential"), devices=[]
    ),
)
EXPORTER_CONFIG = ExporterConfigV1Alpha1(
    alias=EXPORTER_NAME,
    metadata=ObjectMeta(namespace="default", name=EXPORTER_NAME),
    endpoint=EXPORTER_ENDPOINT,
    token=EXPORTER_TOKEN,
)


@patch.object(ExporterConfigV1Alpha1, "delete")
@patch.object(ExporterConfigV1Alpha1, "exists")
@patch.object(ExportersV1Alpha1Api, "delete_exporter")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_delete_exporter(
    _mock_load_kube_config,
    mock_delete_exporter: AsyncMock,
    mock_config_exists: Mock,
    mock_config_delete: Mock,
):
    runner = CliRunner()

    # Delete exporter object and config does not exist
    mock_config_exists.return_value = False
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME])
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object and config exists, delete = n
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME], input="n\n")
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object and config exists, delete = Y
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME], input="Y\n")
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_called_with(EXPORTER_NAME)

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object nointeractive
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME, "--nointeractive"])
    assert result.exit_code == 0
    assert "Deleted exporter 'test' in namespace 'default'" in result.output
    assert "Exporter configuration successfully deleted" not in result.output
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()

    # Delete exporter object output name
    mock_config_exists.return_value = True
    result = runner.invoke(delete, ["exporter", EXPORTER_NAME, "--nointeractive", "--output", "name"])
    assert result.exit_code == 0
    assert result.output == f"exporter.jumpstarter.dev/{EXPORTER_NAME}\n"
    mock_delete_exporter.assert_called_once_with(EXPORTER_NAME)
    mock_config_delete.assert_not_called()

    mock_config_exists.reset_mock()
    mock_delete_exporter.reset_mock()
    mock_config_delete.reset_mock()
