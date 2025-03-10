from kubernetes_asyncio.client.models import V1ObjectMeta, V1ObjectReference

from jumpstarter_kubernetes.exporters import V1Alpha1Exporter, V1Alpha1ExporterDevice, V1Alpha1ExporterStatus

TEST_EXPORTER = V1Alpha1Exporter(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Exporter",
    metadata=V1ObjectMeta(
        creation_timestamp="2021-10-01T00:00:00Z",
        generation=1,
        name="test-exporter",
        namespace="default",
        resource_version="1",
        uid="7a25eb81-6443-47ec-a62f-50165bffede8",
    ),
    status=V1Alpha1ExporterStatus(
        credential=V1ObjectReference(name="test-credential"),
        devices=[V1Alpha1ExporterDevice(labels={"test": "label"}, uuid="f4cf49ab-fc64-46c6-94e7-a40502eb77b1")],
        endpoint="https://test-exporter",
    ),
)


def test_exporter_dump_json():
    assert (
        TEST_EXPORTER.dump_json()
        == """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Exporter",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 1,
        "name": "test-exporter",
        "namespace": "default",
        "resourceVersion": "1",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8"
    },
    "status": {
        "credential": {
            "name": "test-credential"
        },
        "devices": [
            {
                "labels": {
                    "test": "label"
                },
                "uuid": "f4cf49ab-fc64-46c6-94e7-a40502eb77b1"
            }
        ],
        "endpoint": "https://test-exporter"
    }
}"""
    )


def test_exporter_dump_yaml():
    assert (
        TEST_EXPORTER.dump_yaml()
        == """apiVersion: jumpstarter.dev/v1alpha1
kind: Exporter
metadata:
  creationTimestamp: '2021-10-01T00:00:00Z'
  generation: 1
  name: test-exporter
  namespace: default
  resourceVersion: '1'
  uid: 7a25eb81-6443-47ec-a62f-50165bffede8
status:
  credential:
    name: test-credential
  devices:
  - labels:
      test: label
    uuid: f4cf49ab-fc64-46c6-94e7-a40502eb77b1
  endpoint: https://test-exporter
"""
    )
