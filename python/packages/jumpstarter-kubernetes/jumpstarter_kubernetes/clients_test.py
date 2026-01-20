
from kubernetes_asyncio.client.models import V1ObjectMeta

from jumpstarter_kubernetes import V1Alpha1Client, V1Alpha1ClientStatus

TEST_CLIENT = V1Alpha1Client(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Client",
    metadata=V1ObjectMeta(
        creation_timestamp="2021-10-01T00:00:00Z",
        generation=1,
        name="test-client",
        namespace="default",
        resource_version="1",
        uid="7a25eb81-6443-47ec-a62f-50165bffede8",
    ),
    status=V1Alpha1ClientStatus(credential=None, endpoint="https://test-client"),
)


def test_client_dump_json():
    assert (
        TEST_CLIENT.dump_json()
        == """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Client",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 1,
        "name": "test-client",
        "namespace": "default",
        "resourceVersion": "1",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8"
    },
    "status": {
        "credential": null,
        "endpoint": "https://test-client"
    }
}"""
    )


def test_client_dump_yaml():
    assert (
        TEST_CLIENT.dump_yaml()
        == """apiVersion: jumpstarter.dev/v1alpha1
kind: Client
metadata:
  creationTimestamp: '2021-10-01T00:00:00Z'
  generation: 1
  name: test-client
  namespace: default
  resourceVersion: '1'
  uid: 7a25eb81-6443-47ec-a62f-50165bffede8
status:
  credential: null
  endpoint: https://test-client
"""
    )


def test_client_from_dict_with_credential():
    """Test V1Alpha1Client.from_dict with credential"""

    test_dict = {
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
        "status": {"credential": {"name": "test-credential"}, "endpoint": "https://test-client"},
    }
    client = V1Alpha1Client.from_dict(test_dict)
    assert client.metadata.name == "test-client"
    assert client.status.endpoint == "https://test-client"
    assert client.status.credential.name == "test-credential"


def test_client_from_dict_without_credential():
    """Test V1Alpha1Client.from_dict without credential"""
    test_dict = {
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
    client = V1Alpha1Client.from_dict(test_dict)
    assert client.metadata.name == "test-client"
    assert client.status.endpoint == "https://test-client"
    assert client.status.credential is None


def test_client_from_dict_without_status():
    """Test V1Alpha1Client.from_dict without status"""
    test_dict = {
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
    client = V1Alpha1Client.from_dict(test_dict)
    assert client.metadata.name == "test-client"
    assert client.status is None


def test_client_rich_add_columns():
    """Test V1Alpha1Client.rich_add_columns"""
    from unittest.mock import MagicMock

    mock_table = MagicMock()
    V1Alpha1Client.rich_add_columns(mock_table)
    assert mock_table.add_column.call_count == 2
    mock_table.add_column.assert_any_call("NAME", no_wrap=True)
    mock_table.add_column.assert_any_call("ENDPOINT")


def test_client_rich_add_rows_with_status():
    """Test V1Alpha1Client.rich_add_rows with status"""
    from unittest.mock import MagicMock

    mock_table = MagicMock()
    TEST_CLIENT.rich_add_rows(mock_table)
    mock_table.add_row.assert_called_once_with("test-client", "https://test-client")


def test_client_rich_add_rows_without_status():
    """Test V1Alpha1Client.rich_add_rows without status"""
    from unittest.mock import MagicMock

    client = V1Alpha1Client(
        api_version="jumpstarter.dev/v1alpha1",
        kind="Client",
        metadata=V1ObjectMeta(name="test-client", namespace="default"),
        status=None,
    )
    mock_table = MagicMock()
    client.rich_add_rows(mock_table)
    mock_table.add_row.assert_called_once_with("test-client", "")


def test_client_rich_add_names():
    """Test V1Alpha1Client.rich_add_names"""
    names = []
    TEST_CLIENT.rich_add_names(names)
    assert names == ["client.jumpstarter.dev/test-client"]


def test_client_list_from_dict():
    """Test V1Alpha1ClientList.from_dict"""
    from jumpstarter_kubernetes import V1Alpha1ClientList

    test_dict = {
        "items": [
            {
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Client",
                "metadata": {
                    "creationTimestamp": "2021-10-01T00:00:00Z",
                    "generation": 1,
                    "name": "client1",
                    "namespace": "default",
                    "resourceVersion": "1",
                    "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
                },
                "status": {"endpoint": "https://client1"},
            },
            {
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Client",
                "metadata": {
                    "creationTimestamp": "2021-10-01T00:00:00Z",
                    "generation": 1,
                    "name": "client2",
                    "namespace": "default",
                    "resourceVersion": "1",
                    "uid": "8b36fc92-7554-48fd-b73g-61276cggfef9",
                },
                "status": {"endpoint": "https://client2"},
            },
        ]
    }
    client_list = V1Alpha1ClientList.from_dict(test_dict)
    assert len(client_list.items) == 2
    assert client_list.items[0].metadata.name == "client1"
    assert client_list.items[1].metadata.name == "client2"


def test_client_list_rich_add_columns():
    """Test V1Alpha1ClientList.rich_add_columns"""
    from unittest.mock import MagicMock

    from jumpstarter_kubernetes import V1Alpha1ClientList

    mock_table = MagicMock()
    V1Alpha1ClientList.rich_add_columns(mock_table)
    assert mock_table.add_column.call_count == 2


def test_client_list_rich_add_rows():
    """Test V1Alpha1ClientList.rich_add_rows"""
    from unittest.mock import MagicMock

    from jumpstarter_kubernetes import V1Alpha1ClientList

    client_list = V1Alpha1ClientList(items=[TEST_CLIENT])
    mock_table = MagicMock()
    client_list.rich_add_rows(mock_table)
    assert mock_table.add_row.call_count == 1


def test_client_list_rich_add_names():
    """Test V1Alpha1ClientList.rich_add_names"""
    from jumpstarter_kubernetes import V1Alpha1ClientList

    client_list = V1Alpha1ClientList(items=[TEST_CLIENT])
    names = []
    client_list.rich_add_names(names)
    assert names == ["client.jumpstarter.dev/test-client"]
