import pytest
from kubernetes_asyncio.client.models import V1Condition, V1ObjectMeta, V1ObjectReference

from jumpstarter_kubernetes import V1Alpha1Lease, V1Alpha1LeaseSelector, V1Alpha1LeaseSpec, V1Alpha1LeaseStatus

TEST_LEASE = V1Alpha1Lease(
    apiVersion="jumpstarter.dev/v1alpha1",
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
        selector=V1Alpha1LeaseSelector(matchLabels={"test": "label", "another": "something"}),
    ),
    status=V1Alpha1LeaseStatus(
        beginTime="2021-10-01T00:00:00Z",
        conditions=[
            V1Condition(
                last_transition_time="2021-10-01T00:00:00Z", status="True", type="Active", message="", reason=""
            )
        ],
        endTime="2021-10-01T01:00:00Z",
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


def test_lease_from_dict_without_match_labels_name_targeted():
    lease = V1Alpha1Lease.from_dict(
        {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {
                "creationTimestamp": "2021-10-01T00:00:00Z",
                "generation": 1,
                "managedFields": [],
                "name": "test-lease",
                "namespace": "default",
                "resourceVersion": "1",
                "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
            },
            "spec": {
                "clientRef": {"name": "test-client"},
                "duration": "1h",
                "selector": {},
                "exporterRef": {"name": "test-exporter"},
            },
            "status": {
                "ended": False,
                "conditions": [
                    {
                        "lastTransitionTime": "2021-10-01T00:00:00Z",
                        "message": "",
                        "observedGeneration": 1,
                        "reason": "",
                        "status": "True",
                        "type": "Active",
                    }
                ],
            },
        }
    )

    assert lease.spec.selector.match_labels == {}


def test_from_dict_raises_type_error_when_spec_is_a_list():
    with pytest.raises(TypeError):
        V1Alpha1Lease.from_dict({"spec": []})


def test_from_dict_raises_type_error_when_spec_is_a_string():
    with pytest.raises(TypeError):
        V1Alpha1Lease.from_dict({"spec": "invalid"})


def test_from_dict_raises_type_error_when_spec_is_an_integer():
    with pytest.raises(TypeError):
        V1Alpha1Lease.from_dict({"spec": 42})


def test_from_dict_raises_type_error_when_spec_is_none():
    with pytest.raises(TypeError):
        V1Alpha1Lease.from_dict({"spec": None})


def test_from_dict_raises_type_error_when_spec_is_a_boolean():
    with pytest.raises(TypeError):
        V1Alpha1Lease.from_dict({"spec": True})


def test_from_dict_with_empty_spec_raises_key_error_not_type_error():
    with pytest.raises(KeyError):
        V1Alpha1Lease.from_dict({"spec": {}})


def lease_dict(spec: dict, status: dict) -> dict:
    return {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "Lease",
        "metadata": {
            "creationTimestamp": "2021-10-01T00:00:00Z",
            "generation": 1,
            "managedFields": [],
            "name": "ivi-4fj2k",
            "namespace": "default",
            "resourceVersion": "7",
            "uid": "7a25eb81-6443-47ec-a62f-50165bffede8",
        },
        "spec": {"clientRef": {"name": "test-client"}, "duration": "4h", "selector": {}} | spec,
        "status": {"ended": False} | status,
    }


def condition(type: str, status: str, reason: str) -> dict:
    return {
        "lastTransitionTime": "2021-10-01T00:00:00Z",
        "message": "",
        "observedGeneration": 1,
        "reason": reason,
        "status": status,
        "type": type,
    }


MEMBER_LEASE = lease_dict(
    spec={
        "members": [
            {"name": "headunit", "selector": {"matchLabels": {"device-type": "headunit"}}},
            {"name": "phone", "selector": {"matchLabels": {"device-type": "phone"}}, "optional": True},
            {"name": "ecu", "exporterRef": {"name": "ecu-2"}, "allowDisabled": True},
        ],
        "constraints": [{"members": ["headunit", "ecu"], "sameLabel": "bus"}],
        "templateRef": {"name": "ivi", "generation": 3},
    },
    status={
        "beginTime": "2021-10-01T00:00:05Z",
        "bindAttempt": 2,
        "exclusionGroups": ["bench-7"],
        "members": [
            {"name": "headunit", "state": "Bound", "exporterRef": {"name": "hu-1"}, "priority": 10},
            {"name": "phone", "state": "Omitted", "reason": "Excluded"},
            {
                "name": "ecu",
                "state": "Lost",
                "reason": "Offline",
                "message": "the exporter went offline",
                "exporterRef": {"name": "ecu-2"},
            },
        ],
        "conditions": [condition("Ready", "False", "MemberLost"), condition("Degraded", "True", "MemberLost")],
    },
)


def test_member_form_lease_from_dict():
    lease = V1Alpha1Lease.from_dict(MEMBER_LEASE)

    assert lease.is_member_form()
    # A member-form lease has no exporter of its own: each member has one.
    assert lease.status.exporter is None
    assert [m.name for m in lease.spec.members] == ["headunit", "phone", "ecu"]
    assert lease.spec.members[0].selector.match_labels == {"device-type": "headunit"}
    assert lease.spec.members[1].optional is True
    assert lease.spec.members[2].exporter_ref.name == "ecu-2"
    assert lease.spec.members[2].allow_disabled is True
    assert lease.spec.constraints[0].members == ["headunit", "ecu"]
    assert lease.spec.constraints[0].same_label == "bus"
    assert lease.spec.template_ref.name == "ivi"
    assert lease.spec.template_ref.generation == 3
    assert [(m.name, m.state) for m in lease.status.members] == [
        ("headunit", "Bound"),
        ("phone", "Omitted"),
        ("ecu", "Lost"),
    ]
    assert lease.status.members[0].exporter_ref.name == "hu-1"
    assert lease.status.members[1].exporter_ref is None
    assert lease.status.members[2].reason == "Offline"
    assert lease.status.exclusion_groups == ["bench-7"]
    assert lease.status.bind_attempt == 2


def test_member_form_lease_dumps_the_fields_the_cluster_stores():
    dumped = V1Alpha1Lease.from_dict(MEMBER_LEASE).model_dump(mode="json", by_alias=True)

    assert dumped["spec"]["members"] == MEMBER_LEASE["spec"]["members"]
    assert dumped["spec"]["constraints"] == MEMBER_LEASE["spec"]["constraints"]
    assert dumped["spec"]["templateRef"] == MEMBER_LEASE["spec"]["templateRef"]
    assert dumped["status"]["members"] == MEMBER_LEASE["status"]["members"]
    assert dumped["status"]["exclusionGroups"] == ["bench-7"]
    assert dumped["status"]["bindAttempt"] == 2
    assert dumped["status"]["exporter"] is None


def test_member_form_lease_survives_a_yaml_round_trip():
    import yaml

    lease = V1Alpha1Lease.from_dict(MEMBER_LEASE)
    dumped = yaml.safe_load(lease.dump_yaml())

    assert dumped["status"]["members"][2] == MEMBER_LEASE["status"]["members"][2]
    assert dumped["spec"]["members"][1] == MEMBER_LEASE["spec"]["members"][1]


def test_member_form_lease_row_shows_each_role():
    from unittest.mock import MagicMock

    table = MagicMock()
    V1Alpha1Lease.from_dict(MEMBER_LEASE).rich_add_rows(table)

    row = table.add_row.call_args[0]
    assert row[2] == "template=ivi"
    # Only a member that is not bound carries its state.
    assert row[3] == "headunit=hu-1 phone=(Omitted) ecu=ecu-2(Lost)"
    assert row[6] == "MemberLost"


def test_member_form_lease_without_template_shows_what_each_role_selects():
    from unittest.mock import MagicMock

    data = lease_dict(
        spec={
            "members": [
                {
                    "name": "phone",
                    "selector": {
                        "matchLabels": {"device-type": "phone"},
                        "matchExpressions": [{"key": "os", "operator": "In", "values": ["android", "ios"]}],
                    },
                },
                {"name": "ecu", "exporterRef": {"name": "ecu-2"}},
            ]
        },
        # The controller has not reconciled the lease yet.
        status={},
    )
    lease = V1Alpha1Lease.from_dict(data)
    table = MagicMock()
    lease.rich_add_rows(table)

    row = table.add_row.call_args[0]
    assert lease.status.members is None
    assert lease.status.conditions == []
    assert row[2] == "phone=device-type:phone,os In android,ios ecu=ecu-2"
    assert row[3] == "phone=(Pending) ecu=(Pending)"


def test_scalar_lease_from_dict_has_no_member_fields():
    data = lease_dict(
        spec={"selector": {"matchLabels": {"board": "rpi4"}}},
        status={"exporterRef": {"name": "rpi-1"}, "conditions": [condition("Ready", "True", "Ready")]},
    )
    lease = V1Alpha1Lease.from_dict(data)
    dumped = lease.model_dump(mode="json", by_alias=True)

    assert not lease.is_member_form()
    assert lease.status.exporter.name == "rpi-1"
    assert lease.spec.members is None
    assert lease.status.members is None
    # A scalar lease prints exactly the fields it printed before.
    assert set(dumped["spec"]) == {"client", "duration", "selector"}
    assert set(dumped["status"]) == {"beginTime", "conditions", "endTime", "ended", "exporter"}
