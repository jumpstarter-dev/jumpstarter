from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference

from jumpstarter_kubernetes import V1Alpha1Lease, V1Alpha1LeaseSelector, V1Alpha1LeaseSpec, V1Alpha1LeaseStatus

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
        selector=V1Alpha1LeaseSelector(match_labels={"test": "label", "another": "something"}),
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
    print(TEST_LEASE.dump_json())
    assert (
        TEST_LEASE.dump_json()
        == """{
    "apiVersion": "jumpstarter.dev/v1alpha1",
    "kind": "Lease",
    "metadata": {
        "creationTimestamp": "2021-10-01T00:00:00Z",
        "generation": 1,
        "name": "test-lease",
        "namespace": "default",
        "resourceVersion": "1",
        "uid": "7a25eb81-6443-47ec-a62f-50165bffede8"
    },
    "spec": {
        "client": {
            "name": "test-client"
        },
        "duration": "1h",
        "selector": {
            "matchLabels": {
                "test": "label",
                "another": "something"
            }
        }
    },
    "status": {
        "beginTime": "2021-10-01T00:00:00Z",
        "conditions": [
            {
                "lastTransitionTime": "2021-10-01T00:00:00Z",
                "message": "",
                "reason": "",
                "status": "True",
                "type": "Active"
            }
        ],
        "endTime": "2021-10-01T01:00:00Z",
        "ended": false,
        "exporter": {
            "name": "test-exporter"
        }
    }
}"""
    )


def test_lease_dump_yaml():
    print(TEST_LEASE.dump_yaml())
    assert (
        TEST_LEASE.dump_yaml()
        == """apiVersion: jumpstarter.dev/v1alpha1
kind: Lease
metadata:
  creationTimestamp: '2021-10-01T00:00:00Z'
  generation: 1
  name: test-lease
  namespace: default
  resourceVersion: '1'
  uid: 7a25eb81-6443-47ec-a62f-50165bffede8
spec:
  client:
    name: test-client
  duration: 1h
  selector:
    matchLabels:
      another: something
      test: label
status:
  beginTime: '2021-10-01T00:00:00Z'
  conditions:
  - lastTransitionTime: '2021-10-01T00:00:00Z'
    message: ''
    reason: ''
    status: 'True'
    type: Active
  endTime: '2021-10-01T01:00:00Z'
  ended: false
  exporter:
    name: test-exporter
"""
    )
