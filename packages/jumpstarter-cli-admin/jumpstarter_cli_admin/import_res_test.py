import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from click.testing import CliRunner
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
)

from .import_res import import_res
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.tls import TLSConfigV1Alpha1

# Generate a random client name
CLIENT_NAME = uuid.uuid4().hex
CLIENT_ENDPOINT = "grpc://example.com:443"
CLIENT_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
DRIVER_NAME = "jumpstarter.Testing"
# Create a test client config
UNSAFE_CLIENT_CONFIG = ClientConfigV1Alpha1(
    alias=CLIENT_NAME,
    metadata=ObjectMeta(namespace="default", name=CLIENT_NAME),
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)
CLIENT_CONFIG = ClientConfigV1Alpha1(
    alias=CLIENT_NAME,
    metadata=ObjectMeta(namespace="default", name=CLIENT_NAME),
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    drivers=ClientConfigV1Alpha1Drivers(allow=[DRIVER_NAME], unsafe=False),
)
INSECURE_TLS_CLIENT_CONFIG = ClientConfigV1Alpha1(
    alias=CLIENT_NAME,
    metadata=ObjectMeta(namespace="default", name=CLIENT_NAME),
    endpoint=CLIENT_ENDPOINT,
    token=CLIENT_TOKEN,
    tls=TLSConfigV1Alpha1(insecure=True),
    drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
)


@patch.object(ClientConfigV1Alpha1, "save")
@patch.object(ClientsV1Alpha1Api, "get_client_config")
@patch.object(ClientsV1Alpha1Api, "_load_kube_config")
def test_import_client(_load_kube_config_mock, get_client_config_mock: AsyncMock, save_client_config_mock: Mock):
    runner = CliRunner()

    # Create and save unsafe client config
    get_client_config_mock.return_value = UNSAFE_CLIENT_CONFIG

    # Save with prompts
    result = runner.invoke(import_res, ["client", CLIENT_NAME], input="Y\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(UNSAFE_CLIENT_CONFIG, None)
    save_client_config_mock.reset_mock()

    # Save with nointeractive
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--nointeractive"])
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(UNSAFE_CLIENT_CONFIG, None)
    save_client_config_mock.reset_mock()

    # Test insecure TLS config
    get_client_config_mock.return_value = INSECURE_TLS_CLIENT_CONFIG

    # Save with prompts accept insecure = Y
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--insecure-tls-config"], input="Y\nY\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(INSECURE_TLS_CLIENT_CONFIG, None)
    save_client_config_mock.reset_mock()

    # Save with prompts no interactive prompts and insecure tls cert
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--nointeractive", "--insecure-tls-config"])
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(INSECURE_TLS_CLIENT_CONFIG, None)
    save_client_config_mock.reset_mock()

    # Save with prompts accept insecure = N
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--insecure-tls-config"], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    save_client_config_mock.assert_not_called()
    save_client_config_mock.reset_mock()

    # Reset mock for subsequent tests
    get_client_config_mock.return_value = UNSAFE_CLIENT_CONFIG

    # Save with custom out file
    out = f"/tmp/{CLIENT_NAME}.yaml"
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--unsafe", "--out", out])
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(UNSAFE_CLIENT_CONFIG, str(Path(out).resolve()))
    save_client_config_mock.reset_mock()

    # Save with path output
    out = f"/tmp/{CLIENT_NAME}.yaml"
    save_client_config_mock.return_value = Path(out)
    result = runner.invoke(
        import_res, ["client", CLIENT_NAME, "--nointeractive", "--unsafe", "--out", out, "--output", "path"]
    )
    assert result.exit_code == 0
    assert result.output == f"{out}\n"
    save_client_config_mock.assert_called_once_with(UNSAFE_CLIENT_CONFIG, str(Path(out).resolve()))
    save_client_config_mock.reset_mock()

    # Create and save safe client config
    get_client_config_mock.reset_mock()
    get_client_config_mock.return_value = CLIENT_CONFIG

    # Save with arguments
    result = runner.invoke(import_res, ["client", CLIENT_NAME, "--allow", DRIVER_NAME])
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(CLIENT_CONFIG, None)
    save_client_config_mock.reset_mock()

    # Save with prompts
    result = runner.invoke(import_res, ["client", CLIENT_NAME], input=f"n\n{DRIVER_NAME}\n")
    assert result.exit_code == 0
    assert "Client configuration successfully saved" in result.output
    save_client_config_mock.assert_called_once_with(CLIENT_CONFIG, None)


# Generate a random exporter name
EXPORTER_NAME = uuid.uuid4().hex
EXPORTER_ENDPOINT = "grpc://example.com:443"
EXPORTER_TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
# Create a test exporter config
EXPORTER_CONFIG = ExporterConfigV1Alpha1(
    alias=EXPORTER_NAME,
    metadata=ObjectMeta(namespace="default", name=EXPORTER_NAME),
    endpoint=EXPORTER_ENDPOINT,
    token=EXPORTER_TOKEN,
)
INSECURE_TLS_EXPORTER_CONFIG = ExporterConfigV1Alpha1(
    alias=EXPORTER_NAME,
    metadata=ObjectMeta(namespace="default", name=EXPORTER_NAME),
    endpoint=EXPORTER_ENDPOINT,
    token=EXPORTER_TOKEN,
    tls=TLSConfigV1Alpha1(insecure=True),
)


@patch.object(ExporterConfigV1Alpha1, "save")
@patch.object(ExportersV1Alpha1Api, "get_exporter_config", return_value=EXPORTER_CONFIG)
@patch.object(ExportersV1Alpha1Api, "_load_kube_config")
def test_import_exporter(_load_kube_config_mock, _get_exporter_config_mock, save_exporter_config_mock: Mock):
    runner = CliRunner()

    # Save with prompts
    result = runner.invoke(import_res, ["exporter", EXPORTER_NAME], input="Y\n")
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_config_mock.assert_called_with(EXPORTER_CONFIG, None)
    save_exporter_config_mock.reset_mock()

    # Test insecure TLS config
    _get_exporter_config_mock.return_value = INSECURE_TLS_EXPORTER_CONFIG

    # Save with prompts accept insecure = Y
    result = runner.invoke(import_res, ["exporter", EXPORTER_NAME, "--insecure-tls-config"], input="Y\n")
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_config_mock.assert_called_once_with(INSECURE_TLS_EXPORTER_CONFIG, None)
    save_exporter_config_mock.reset_mock()

    # Save with prompts accept insecure = N
    result = runner.invoke(import_res, ["exporter", EXPORTER_NAME, "--insecure-tls-config"], input="n\n")
    assert result.exit_code == 1
    assert "Aborted" in result.output
    save_exporter_config_mock.assert_not_called()
    save_exporter_config_mock.reset_mock()

    # Reset mock for subsequent tests
    _get_exporter_config_mock.return_value = EXPORTER_CONFIG

    # Save with custom out file
    out = f"/tmp/{EXPORTER_NAME}.yaml"
    result = runner.invoke(import_res, ["exporter", EXPORTER_NAME, "--out", out])
    assert result.exit_code == 0
    assert "Exporter configuration successfully saved" in result.output
    save_exporter_config_mock.assert_called_with(EXPORTER_CONFIG, str(Path(out).resolve()))

    # Save with path output
    out = f"/tmp/{EXPORTER_NAME}.yaml"
    save_exporter_config_mock.return_value = Path(out)
    result = runner.invoke(import_res, ["exporter", EXPORTER_NAME, "--out", out, "--output", "path"])
    assert result.exit_code == 0
    assert result.output == f"{out}\n"
    save_exporter_config_mock.assert_called_with(EXPORTER_CONFIG, str(Path(out).resolve()))
