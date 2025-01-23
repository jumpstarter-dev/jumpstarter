import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from asyncclick.testing import CliRunner
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    V1Alpha1Client,
    V1Alpha1ClientStatus,
    V1Alpha1Exporter,
    V1Alpha1ExporterStatus,
)
from kubernetes_asyncio.client.models import V1ObjectMeta

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Drivers,
    ExporterConfigV1Alpha1,
)

from .create import create

# Generate a random client name
CLIENT_NAME = uuid.uuid4().hex
# Default config path
CLIENT_CONFIG_PATH = ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH / (CLIENT_NAME + ".yaml")

CLIENT_ENDPOINT = "grpc://example.com:443"
CLIENT_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
DRIVER_NAME = "jumpstarter.Testing"

CLIENT_OBJECT = V1Alpha1Client(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Client",
    metadata=V1ObjectMeta(name=CLIENT_NAME, namespace="default", creation_timestamp="2024-01-01T21:00:00Z"),
    status=V1Alpha1ClientStatus(endpoint=CLIENT_ENDPOINT, credential=None),
)

UNSAFE_CLIENT_CONFIG = ClientConfigV1Alpha1(
    name=CLIENT_NAME,
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)

CLIENT_CONFIG = ClientConfigV1Alpha1(
    name=CLIENT_NAME,
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)


@pytest.mark.anyio
@patch.object(ClientConfigV1Alpha1, "save")
@patch.object(ClientsV1Alpha1Api, "get_client_config")
@patch.object(ClientsV1Alpha1Api, "create_client", return_value=CLIENT_OBJECT)
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
async def test_create_client(
    _mock_load_kube_config, _mock_create_client, mock_get_client_config: AsyncMock, mock_save_client: Mock
):
    runner = CliRunner()

    # Don't save client config save = n
    result = await runner.invoke(create, ["client", CLIENT_NAME], input="n\n")
    assert result.exit_code == 0
    assert "Creating client" in result.output
    assert CLIENT_NAME in result.output
    assert "Client configuration successfully saved" not in result.output
    mock_save_client.assert_not_called()
    mock_save_client.reset_mock()

    # Unsafe client config is returned
    mock_get_client_config.return_value = UNSAFE_CLIENT_CONFIG

    # Save with prompts save = Y, unsafe = Y
    result = await runner.invoke(create, ["client", CLIENT_NAME], input="Y\nY\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    mock_save_client.assert_called_once_with(UNSAFE_CLIENT_CONFIG, None)
    mock_save_client.reset_mock()

    # Save with unsafe with custom output file
    out = f"/tmp/{CLIENT_NAME}.yaml"
    result = await runner.invoke(create, ["client", CLIENT_NAME, "--unsafe", "--out", out], input="\n\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    mock_save_client.assert_called_once_with(UNSAFE_CLIENT_CONFIG, out)
    mock_save_client.reset_mock()

    # Regular client config is returned
    mock_get_client_config.return_value = CLIENT_CONFIG

    # Save with arguments
    result = await runner.invoke(create, ["client", CLIENT_NAME, "--save", "--allow", DRIVER_NAME], input="n\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    mock_save_client.assert_called_once_with(CLIENT_CONFIG, None)
    mock_save_client.reset_mock()

    # Save with prompts, save = Y, unsafe = n, allow = DRIVER_NAME
    result = await runner.invoke(create, ["client", CLIENT_NAME], input=f"Y\nn\n{DRIVER_NAME}\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    mock_save_client.assert_called_once_with(CLIENT_CONFIG, None)
    mock_save_client.reset_mock()


# Generate a random exporter name
EXPORTER_NAME = uuid.uuid4().hex
EXPORTER_ENDPOINT = "grpc://example.com:443"
EXPORTER_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
# Default config path
default_config_path = ExporterConfigV1Alpha1.BASE_PATH / (EXPORTER_NAME + ".yaml")
# Create a test exporter config
EXPORTER_OBJECT = V1Alpha1Exporter(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Exporter",
    metadata=V1ObjectMeta(name=EXPORTER_NAME, namespace="default", creation_timestamp="2024-01-01T21:00:00Z"),
    status=V1Alpha1ExporterStatus(endpoint=EXPORTER_ENDPOINT, credential=None, devices=[]),
)
EXPORTER_CONFIG = ExporterConfigV1Alpha1(
    alias=EXPORTER_NAME,
    endpoint=EXPORTER_ENDPOINT,
    token=EXPORTER_TOKEN,
)


@pytest.mark.anyio
@patch.object(ExporterConfigV1Alpha1, "save")
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
@patch.object(ExportersV1Alpha1Api, "create_exporter", return_value=EXPORTER_OBJECT)
@patch.object(ExportersV1Alpha1Api, "get_exporter_config", return_value=EXPORTER_CONFIG)
async def test_create_exporter(
    _get_exporter_config_mock, _create_exporter_mock, _load_kube_config_mock, save_exporter_mock: Mock
):
    runner = CliRunner()

    # Don't save exporter config
    result = await runner.invoke(create, ["exporter", EXPORTER_NAME], input="n\n")
    assert result.exit_code == 0
    assert "Creating exporter" in result.output
    assert EXPORTER_NAME in result.output
    assert "Exporter configuration successfully saved" not in result.output
    save_exporter_mock.assert_not_called()
    save_exporter_mock.reset_mock()

    # Save with prompts
    result = await runner.invoke(create, ["exporter", EXPORTER_NAME], input="Y\n")
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_mock.assert_called_once_with(EXPORTER_CONFIG, None)
    save_exporter_mock.reset_mock()

    # Save with arguments
    result = await runner.invoke(create, ["exporter", EXPORTER_NAME, "--save"])
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_mock.assert_called_once_with(EXPORTER_CONFIG, None)
    save_exporter_mock.reset_mock()

    # Save with arguments and custom path
    out = f"/tmp/{EXPORTER_NAME}.yaml"
    result = await runner.invoke(create, ["exporter", EXPORTER_NAME, "--out", out])
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_mock.assert_called_once_with(EXPORTER_CONFIG, out)
    save_exporter_mock.reset_mock()


@pytest.fixture
def anyio_backend():
    return "asyncio"
