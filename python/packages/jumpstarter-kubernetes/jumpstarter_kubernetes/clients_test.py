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
