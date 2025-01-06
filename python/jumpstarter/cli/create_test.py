import os.path
import uuid
from unittest.mock import patch

import pytest
from asyncclick.testing import CliRunner
from kubernetes_asyncio.client.models import V1ObjectMeta

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Drivers,
    ExporterConfigV1Alpha1,
)
from jumpstarter.k8s import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    V1Alpha1Client,
    V1Alpha1ClientStatus,
    V1Alpha1Exporter,
    V1Alpha1ExporterStatus,
)

from .create import create


@pytest.mark.anyio
async def test_create_client():
    runner = CliRunner()
    # Generate a random client name
    name = uuid.uuid4().hex
    ENDPOINT = "grpc://example.com:443"
    TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    DRIVER_NAME = "jumpstarter.Testing"
    # Default config path
    default_config_path = ClientConfigV1Alpha1.CLIENT_CONFIGS_PATH / (name + ".yaml")
    # Create a test client config
    UNSAFE_CLIENT_CONFIG = f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
endpoint: {ENDPOINT}
tls:
  ca: ''
  insecure: false
token: {TOKEN}
drivers:
  allow: []
  unsafe: true
"""
    CLIENT_CONFIG = f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ClientConfig
endpoint: {ENDPOINT}
tls:
  ca: ''
  insecure: false
token: {TOKEN}
drivers:
  allow:
  - {DRIVER_NAME}
  unsafe: false
"""
    # Create a client config
    with patch.object(
        ClientsV1Alpha1Api,
        "create_client",
        return_value=V1Alpha1Client(
            api_version="jumpstarter.dev/v1alpha1",
            kind="Client",
            metadata=V1ObjectMeta(name=name, namespace="default", creation_timestamp="2024-01-01T21:00:00Z"),
            status=V1Alpha1ClientStatus(endpoint=ENDPOINT, credential=None),
        ),
    ):
        # Don't save client config
        result = await runner.invoke(create, ["client", name], input="n\nY\n")
        assert result.exit_code == 0
        assert "Creating client" in result.output
        assert name in result.output
        assert "Client configuration successfully saved" not in result.output
        assert os.path.isfile(default_config_path) is False

        # Create and save unsafe client config to default path
        with patch.object(
            ClientsV1Alpha1Api,
            "get_client_config",
            return_value=ClientConfigV1Alpha1(
                name=name,
                endpoint=ENDPOINT,
                token=TOKEN,
                drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
            ),
        ):
            # Save with prompts
            result = await runner.invoke(create, ["client", name], input="Y\nY\n")
            assert result.exit_code == 0
            assert "Client configuration successfully saved" in result.output
            assert os.path.isfile(default_config_path)
            with open(default_config_path, "r") as f:
                content = f.read()
                assert content == UNSAFE_CLIENT_CONFIG
            os.unlink(default_config_path)  # Cleanup config file

            # Save with custom output
            out = f"./{name}.yaml"
            result = await runner.invoke(create, ["client", name, "--unsafe", "--out", out], input="\n\n")
            assert result.exit_code == 0
            assert "Client configuration successfully saved" in result.output
            assert os.path.isfile(out)
            with open(out, "r") as f:
                content = f.read()
                assert content == UNSAFE_CLIENT_CONFIG
            os.unlink(out)  # Cleanup config file

        with patch.object(
            ClientsV1Alpha1Api,
            "get_client_config",
            return_value=ClientConfigV1Alpha1(
                name=name,
                endpoint=ENDPOINT,
                token=TOKEN,
                drivers=ClientConfigV1Alpha1Drivers(allow=[], unsafe=True),
            ),
        ):
            # Save with arguments
            result = await runner.invoke(create, ["client", name, "--save", "--allow", DRIVER_NAME], input="n\n")
            assert result.exit_code == 0
            assert "Client configuration successfully saved" in result.output
            assert os.path.isfile(default_config_path)
            with open(default_config_path, "r") as f:
                content = f.read()
                assert content == CLIENT_CONFIG
            os.unlink(default_config_path)  # Cleanup config file

            # Save with prompts
            result = await runner.invoke(create, ["client", name], input=f"Y\nn\n{DRIVER_NAME}\n")
            assert result.exit_code == 0
            assert "Client configuration successfully saved" in result.output
            assert os.path.isfile(default_config_path)
            with open(default_config_path, "r") as f:
                content = f.read()
                assert content == CLIENT_CONFIG
            os.unlink(default_config_path)  # Cleanup config file


@pytest.mark.anyio
async def test_create_exporter():
    runner = CliRunner()
    # Generate a random exporter name
    name = uuid.uuid4().hex
    ENDPOINT = "grpc://example.com:443"
    TOKEN = "dGhpc2lzYXRva2VuLTEyMzQxMjM0MTIzNEyMzQtc2Rxd3Jxd2VycXdlcnF3ZXJxd2VyLTEyMzQxMjM0MTIz"
    # Default config path
    default_config_path = ExporterConfigV1Alpha1.BASE_PATH / (name + ".yaml")
    # Create a test exporter config
    EXPORTER_CONFIG = f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
endpoint: {ENDPOINT}
tls:
  ca: ''
  insecure: false
token: {TOKEN}
export: {{}}
"""
    # Create an exporter config
    with patch.object(
        ExportersV1Alpha1Api,
        "create_exporter",
        return_value=V1Alpha1Exporter(
            api_version="jumpstarter.dev/v1alpha1",
            kind="Exporter",
            metadata=V1ObjectMeta(name=name, namespace="default", creation_timestamp="2024-01-01T21:00:00Z"),
            status=V1Alpha1ExporterStatus(endpoint=ENDPOINT, credential=None, devices=[]),
        ),
    ):
        # Don't save exporter config
        result = await runner.invoke(create, ["exporter", name], input="n\n")
        assert result.exit_code == 0
        assert "Creating exporter" in result.output
        assert name in result.output
        assert "Exporter configuration successfully saved" not in result.output
        assert os.path.isfile(default_config_path) is False

        with patch.object(
            ExportersV1Alpha1Api,
            "get_exporter_config",
            return_value=ExporterConfigV1Alpha1(
                alias=name,
                endpoint=ENDPOINT,
                token=TOKEN,
            ),
        ):
            # Save with prompts
            result = await runner.invoke(create, ["exporter", name], input="Y\n")
            assert result.exit_code == 0
            assert "Exporter configuration successfully saved" in result.output
            assert os.path.isfile(default_config_path)
            with open(default_config_path, "r") as f:
                content = f.read()
                assert content == EXPORTER_CONFIG
            os.unlink(default_config_path)  # Cleanup config file

            # Save with arguments
            result = await runner.invoke(create, ["exporter", name, "--save"])
            assert result.exit_code == 0
            assert "Exporter configuration successfully saved" in result.output
            assert os.path.isfile(default_config_path)
            with open(default_config_path, "r") as f:
                content = f.read()
                assert content == EXPORTER_CONFIG
            os.unlink(default_config_path)  # Cleanup config file

             # Save with arguments and custom path
            out = f"./{name}.yaml"
            result = await runner.invoke(create, ["exporter", name, "--out", out])
            assert result.exit_code == 0
            assert "Exporter configuration successfully saved" in result.output
            assert os.path.isfile(out)
            with open(out, "r") as f:
                content = f.read()
                assert content == EXPORTER_CONFIG
            os.unlink(out)  # Cleanup config file


@pytest.fixture
def anyio_backend():
    return "asyncio"
