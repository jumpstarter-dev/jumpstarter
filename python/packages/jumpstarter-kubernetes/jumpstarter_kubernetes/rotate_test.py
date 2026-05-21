import base64
from unittest.mock import AsyncMock, patch

import pytest
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.client.models import V1Secret

from jumpstarter_kubernetes.clients import ClientsV1Alpha1Api

CLIENT_DICT = {
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Client",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 1,
        "name": "test-client",
        "namespace": "default",
        "resourceVersion": "1",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
    },
    "status": {
        "credential": {"name": "test-client-client"},
        "endpoint": "https://test-client",
    },
}

CLIENT_DICT_NO_CREDENTIAL = {
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Client",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 1,
        "name": "test-client",
        "namespace": "default",
        "resourceVersion": "1",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
    },
    "status": {"endpoint": "https://test-client"},
}


@pytest.mark.asyncio
async def test_rotate_client_token_success():
    """Rotate deletes secret, waits for regeneration, returns new token."""
    api = ClientsV1Alpha1Api(namespace="default")
    api.api = AsyncMock()
    api.core_api = AsyncMock()

    api.api.get_namespaced_custom_object = AsyncMock(return_value=CLIENT_DICT)

    new_token = "new-rotated-token-value"
    mock_secret = V1Secret(data={"token": base64.b64encode(new_token.encode()).decode()})
    api.core_api.delete_namespaced_secret = AsyncMock()
    api.core_api.read_namespaced_secret = AsyncMock(return_value=mock_secret)

    result = await api.rotate_client_token("test-client")

    assert result == new_token
    api.core_api.delete_namespaced_secret.assert_called_once_with("test-client-client", "default")
    api.core_api.read_namespaced_secret.assert_called_with("test-client-client", "default")


@pytest.mark.asyncio
async def test_rotate_client_token_no_credential():
    """Rotate raises when client has no credential secret."""
    api = ClientsV1Alpha1Api(namespace="default")
    api.api = AsyncMock()
    api.core_api = AsyncMock()

    api.api.get_namespaced_custom_object = AsyncMock(return_value=CLIENT_DICT_NO_CREDENTIAL)

    with pytest.raises(Exception, match="has no credential secret"):
        await api.rotate_client_token("test-client")


@pytest.mark.asyncio
@patch("jumpstarter_kubernetes.clients.asyncio.sleep", new_callable=AsyncMock)
async def test_rotate_client_token_timeout(mock_sleep):
    """Rotate raises on timeout when secret never regenerates (404 loop)."""
    api = ClientsV1Alpha1Api(namespace="default")
    api.api = AsyncMock()
    api.core_api = AsyncMock()

    api.api.get_namespaced_custom_object = AsyncMock(return_value=CLIENT_DICT)

    api.core_api.delete_namespaced_secret = AsyncMock()
    api.core_api.read_namespaced_secret = AsyncMock(
        side_effect=ApiException(status=404, reason="Not Found")
    )

    with pytest.raises(Exception, match="Timeout waiting for token regeneration"):
        await api.rotate_client_token("test-client")


@pytest.mark.asyncio
async def test_rotate_client_token_non_404_raises():
    """Non-404 ApiException is not swallowed by polling loop."""
    api = ClientsV1Alpha1Api(namespace="default")
    api.api = AsyncMock()
    api.core_api = AsyncMock()

    api.api.get_namespaced_custom_object = AsyncMock(return_value=CLIENT_DICT)

    api.core_api.delete_namespaced_secret = AsyncMock()
    api.core_api.read_namespaced_secret = AsyncMock(
        side_effect=ApiException(status=403, reason="Forbidden")
    )

    with pytest.raises(ApiException) as exc_info:
        await api.rotate_client_token("test-client")
    assert exc_info.value.status == 403


@pytest.mark.asyncio
async def test_rotate_client_token_no_status():
    """Rotate raises when client has no status."""
    api = ClientsV1Alpha1Api(namespace="default")
    api.api = AsyncMock()
    api.core_api = AsyncMock()

    api.api.get_namespaced_custom_object = AsyncMock(
        return_value={
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Client",
            "metadata": {
                "creationTimestamp": "2021-10-01T00:00:00Z",
                "generation": 1,
                "name": "test-client",
                "namespace": "default",
                "resourceVersion": "1",
                "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
            },
        }
    )

    with pytest.raises(Exception, match="has no credential secret"):
        await api.rotate_client_token("test-client")
