from unittest.mock import patch

import pytest
from asyncclick.testing import CliRunner
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from jumpstarter.k8s import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    V1Alpha1Client,
    V1Alpha1ClientStatus,
    V1Alpha1Exporter,
    V1Alpha1ExporterDevice,
    V1Alpha1ExporterStatus,
)

from .get import get


class MockResponse:
    status: int
    reason: str
    data: str

    def __init__(self, status: int, reason: str, body: str):
        self.status = status
        self.reason = reason
        self.data = body

    def getheaders(self):
        return {}


@pytest.mark.anyio
async def test_get_client():
    runner = CliRunner()
    # Returns client
    with patch.object(
        ClientsV1Alpha1Api,
        "get_client",
        return_value=V1Alpha1Client(
            api_version="jumpstarter.dev/v1alpha1",
            kind="Client",
            metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
            status=V1Alpha1ClientStatus(endpoint="grpc://example.com:443", credential="asdfb123423"),
        ),
    ):
        result = await runner.invoke(get, ["client", "test"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "grpc://example.com:443" in result.output
    # No client found
    with patch.object(ClientsV1Alpha1Api, "get_client", return_value=None) as mock_get_client:
        mock_get_client.side_effect = ApiException(
            http_resp=MockResponse(
                404,
                "Not Found",
                '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"clients.jumpstarter.dev "test" not found","reason":"NotFound","details":{"name":"hello","group":"jumpstarter.dev","kind":"clients"},"code":404}',  # noqa: E501
            )
        )
        result = await runner.invoke(get, ["client", "hello"])
        assert result.exit_code == 1
        assert "NotFound" in result.output


@pytest.mark.anyio
async def test_get_clients():
    runner = CliRunner()
    # Found clients
    with patch.object(
        ClientsV1Alpha1Api,
        "list_clients",
        return_value=[
            V1Alpha1Client(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Client",
                metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ClientStatus(endpoint="grpc://example.com:443", credential="asdfb123423"),
            ),
            V1Alpha1Client(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Client",
                metadata=V1ObjectMeta(name="another", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ClientStatus(endpoint="grpc://example.com:443", credential="asdfb123423"),
            ),
        ],
    ):
        result = await runner.invoke(get, ["clients"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "another" in result.output
        assert "grpc://example.com:443" in result.output
    # No clients found
    with patch.object(
        ClientsV1Alpha1Api,
        "list_clients",
        return_value=[],
    ):
        result = await runner.invoke(get, ["clients"])
        assert result.exit_code == 1
        assert "No resources found" in result.output


@pytest.mark.anyio
async def test_get_exporter():
    runner = CliRunner()
    # Returns exporter
    with patch.object(
        ExportersV1Alpha1Api,
        "get_exporter",
        return_value=V1Alpha1Exporter(
            api_version="jumpstarter.dev/v1alpha1",
            kind="Exporter",
            metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
            status=V1Alpha1ExporterStatus(
                endpoint="grpc://example.com:443", credential=V1ObjectReference(name="test-credential"), devices=[]
            ),
        ),
    ):
        result = await runner.invoke(get, ["exporter", "test"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "grpc://example.com:443" in result.output
    # No exporter found
    with patch.object(ExportersV1Alpha1Api, "get_exporter", return_value=None) as mock_get_client:
        mock_get_client.side_effect = ApiException(
            http_resp=MockResponse(
                404,
                "Not Found",
                '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"exporters.jumpstarter.dev "test" not found","reason":"NotFound","details":{"name":"hello","group":"jumpstarter.dev","kind":"exporters"},"code":404}',  # noqa: E501
            )
        )
        result = await runner.invoke(get, ["exporter", "hello"])
        assert result.exit_code == 1
        assert "NotFound" in result.output


@pytest.mark.anyio
async def test_get_exporter_devices():
    runner = CliRunner()
    # Returns exporter
    with patch.object(
        ExportersV1Alpha1Api,
        "get_exporter",
        return_value=V1Alpha1Exporter(
            api_version="jumpstarter.dev/v1alpha1",
            kind="Exporter",
            metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
            status=V1Alpha1ExporterStatus(
                endpoint="grpc://example.com:443",
                credential=V1ObjectReference(name="test-credential"),
                devices=[
                    V1Alpha1ExporterDevice(labels={"hardware": "rpi4"}, uuid="82a8ac0d-d7ff-4009-8948-18a3c5c607b1"),
                    V1Alpha1ExporterDevice(labels={"hardware": "rpi4"}, uuid="f7cd30ac-64a3-42c6-ba31-b25f033b97c1"),
                ],
            ),
        ),
    ):
        result = await runner.invoke(get, ["exporter", "test", "--devices"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "grpc://example.com:443" in result.output
        assert "hardware:rpi4" in result.output
        assert "82a8ac0d-d7ff-4009-8948-18a3c5c607b1" in result.output
        assert "f7cd30ac-64a3-42c6-ba31-b25f033b97c1" in result.output
    # No exporter found
    with patch.object(ExportersV1Alpha1Api, "get_exporter", return_value=None) as mock_get_client:
        mock_get_client.side_effect = ApiException(
            http_resp=MockResponse(
                404,
                "Not Found",
                '{"kind":"Status","apiVersion":"v1","metadata":{},"status":"Failure","message":"exporters.jumpstarter.dev "test" not found","reason":"NotFound","details":{"name":"hello","group":"jumpstarter.dev","kind":"exporters"},"code":404}',  # noqa: E501
            )
        )
        result = await runner.invoke(get, ["exporter", "hello", "--devices"])
        assert result.exit_code == 1
        assert "NotFound" in result.output


@pytest.mark.anyio
async def test_get_exporters():
    runner = CliRunner()
    # Found clients
    with patch.object(
        ExportersV1Alpha1Api,
        "list_exporters",
        return_value=[
            V1Alpha1Exporter(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Exporter",
                metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ExporterStatus(
                    endpoint="grpc://example.com:443", credential=V1ObjectReference(name="test-credential"), devices=[]
                ),
            ),
            V1Alpha1Exporter(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Exporter",
                metadata=V1ObjectMeta(name="another", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ExporterStatus(
                    endpoint="grpc://example.com:443",
                    credential=V1ObjectReference(name="another-credential"),
                    devices=[],
                ),
            ),
        ],
    ):
        result = await runner.invoke(get, ["exporters"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "another" in result.output
    # No clients found
    with patch.object(
        ExportersV1Alpha1Api,
        "list_exporters",
        return_value=[],
    ):
        result = await runner.invoke(get, ["exporters"])
        assert result.exit_code == 1
        assert "No resources found" in result.output


@pytest.mark.anyio
async def test_get_exporters_devices():
    runner = CliRunner()
    # Found clients
    with patch.object(
        ExportersV1Alpha1Api,
        "list_exporters",
        return_value=[
            V1Alpha1Exporter(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Exporter",
                metadata=V1ObjectMeta(name="test", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ExporterStatus(
                    endpoint="grpc://example.com:443",
                    credential=V1ObjectReference(name="test-credential"),
                    devices=[
                        V1Alpha1ExporterDevice(labels={"hardware": "rpi4"}, uuid="82a8ac0d-d7ff-4009-8948-18a3c5c607b1")
                    ],
                ),
            ),
            V1Alpha1Exporter(
                api_version="jumpstarter.dev/v1alpha1",
                kind="Exporter",
                metadata=V1ObjectMeta(name="another", namespace="testing", creation_timestamp="2024-01-01T21:00:00Z"),
                status=V1Alpha1ExporterStatus(
                    endpoint="grpc://example.com:443",
                    credential=V1ObjectReference(name="another-credential"),
                    devices=[
                        V1Alpha1ExporterDevice(
                            labels={"hardware": "rpi4"}, uuid="f7cd30ac-64a3-42c6-ba31-b25f033b97c1"
                        ),
                    ],
                ),
            ),
        ],
    ):
        result = await runner.invoke(get, ["exporters", "--devices"])
        assert result.exit_code == 0
        assert "test" in result.output
        assert "another" in result.output
        assert "hardware:rpi4" in result.output
        assert "82a8ac0d-d7ff-4009-8948-18a3c5c607b1" in result.output
        assert "f7cd30ac-64a3-42c6-ba31-b25f033b97c1" in result.output
    # No clients found
    with patch.object(
        ExportersV1Alpha1Api,
        "list_exporters",
        return_value=[],
    ):
        result = await runner.invoke(get, ["exporters", "--devices"])
        assert result.exit_code == 1
        assert "No resources found" in result.output


@pytest.fixture
def anyio_backend():
    return "asyncio"
