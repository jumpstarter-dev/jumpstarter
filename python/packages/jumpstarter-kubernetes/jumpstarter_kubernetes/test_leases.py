from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference

from jumpstarter_kubernetes import V1Alpha1Lease, V1Alpha1LeaseSpec, V1Alpha1LeaseStatus

TEST_LEASE = V1Alpha1Lease(
    api_version="jumpstarter.dev/v1alpha1",
    kind="Lease",
    metadata=V1ObjectMeta(
        creation_timestamp="2021-10-01T00:00:00Z",
        generation=1,
        name="test-lease",
        namespace="default",
        resource_version="1",
        uid="7a25eb81-6443-47ec-a62f-50165bffede8",
    ),
    spec=V1Alpha1LeaseSpec(
        client=V1ObjectReference(name="test-client"),
        duration="1h",
        selector={"test": "label", "another": "something"},
    ),
    status=V1Alpha1LeaseStatus(
        begin_time="2021-10-01T00:00:00Z",
        conditions=[
            V1Condition(
                last_transition_time="2021-10-01T00:00:00Z", status="True", type="Active", message="", reason=""
            )
        ],
        end_time="2021-10-01T01:00:00Z",
        ended=False,
        exporter=V1ObjectReference(name="test-exporter"),
    ),
)


def test_lease_dump_json():
    assert (
        TEST_LEASE.dump_json()
        == """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Lease",
    "metadata": {
        "annotations": null,
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "deletionGracePeriodSeconds": null,
        "deletionTimestamp": null,
        "finalizers": null,
        "generateName": null,
        "generation": 1,
        "labels": null,
        "managedFields": null,
        "name": "test-lease",
        "namespace": "default",
        "ownerReferences": null,
        "resourceVersion": "1",
        "selfLink": null,
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8"
    },
    "spec": {
        "client": {
            "apiVersion": null,
            "fieldPath": null,
            "kind": null,
            "name": "test-client",
            "namespace": null,
            "resourceVersion": null,
            "uid": null
        },
        "duration": "1h",
        "selector": {
            "test": "label",
            "another": "something"
        }
    },
    "status": {
        "begin_time": "2021-10-01T00:00:00Z",
        "conditions": [
            {
                "lastTransitionTime": "2021-10-01T00:00:00Z",
                "message": "",
                "observedGeneration": null,
                "reason": "",
                "status": "True",
                "type": "Active"
            }
        ],
        "end_time": "2021-10-01T01:00:00Z",
        "ended": false,
        "exporter": {
            "apiVersion": null,
            "fieldPath": null,
            "kind": null,
            "name": "test-exporter",
            "namespace": null,
            "resourceVersion": null,
            "uid": null
        }
    }
}"""
    )


def test_lease_dump_yaml():
    assert (
        TEST_LEASE.dump_yaml()
        == """apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  annotations: null
  creationTimestamp: '2021-10-01T00:00:00Z'
  deletionGracePeriodSeconds: null
  deletionTimestamp: null
  finalizers: null
  generateName: null
  generation: 1
  labels: null
  managedFields: null
  name: test-lease
  namespace: default
  ownerReferences: null
  resourceVersion: '1'
  selfLink: null
  uid: 7a25eb81-6443-47ec-a62f-50165bffede8
spec:
  client:
    apiVersion: null
    fieldPath: null
    kind: null
    name: test-client
    namespace: null
    resourceVersion: null
    uid: null
  duration: 1h
  selector:
    another: something
    test: label
status:
  begin_time: '2021-10-01T00:00:00Z'
  conditions:
  - lastTransitionTime: '2021-10-01T00:00:00Z'
    message: ''
    observedGeneration: null
    reason: ''
    status: 'True'
    type: Active
  end_time: '2021-10-01T01:00:00Z'
  ended: false
  exporter:
    apiVersion: null
    fieldPath: null
    kind: null
    name: test-exporter
    namespace: null
    resourceVersion: null
    uid: null
"""
    )
