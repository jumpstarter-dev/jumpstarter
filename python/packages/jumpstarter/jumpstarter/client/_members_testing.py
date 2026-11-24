"""Test-only builders of member-form lease protobufs (not part of the public API).

The tests of jumpstarter and of jumpstarter-cli use them.
"""

from __future__ import annotations

from google.protobuf import duration_pb2
from jumpstarter_protocol import client_pb2


def members_lease_pb(name="lease-9m2qd", template=None, statuses=None, conditions=()):
    lease = client_pb2.Lease(
        name=f"namespaces/default/leases/{name}",
        client="namespaces/default/clients/ci-runner",
        duration=duration_pb2.Duration(seconds=2700),
        members=[
            client_pb2.LeaseMember(name="phone", selector="device-type=phone", optional=True),
            client_pb2.LeaseMember(name="headunit", selector="device-type=headunit"),
        ],
        constraints=[client_pb2.LeaseConstraint(member_names=["phone", "headunit"], same_label="rf-domain")],
        conditions=list(conditions),
    )
    if template:
        lease.template.template = f"namespaces/default/leasetemplates/{template}"
        lease.template.generation = 3
    for status in (
        statuses
        if statuses is not None
        else [
            client_pb2.LeaseMemberStatus(
                name="phone",
                state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                exporter="namespaces/default/exporters/rack3-phone-4",
            ),
            client_pb2.LeaseMemberStatus(
                name="headunit",
                state=client_pb2.LEASE_MEMBER_STATE_BOUND,
                exporter="namespaces/default/exporters/rack3-hu-2",
            ),
        ]
    ):
        lease.member_statuses.append(status)
    return lease


def template_pb(name="ivi"):
    return client_pb2.LeaseTemplate(
        name=f"namespaces/default/leasetemplates/{name}",
        description="AAOS head unit, phone, and body ECU on one CAN bus",
        docs=["https://wiki.example.com/labs/ivi"],
        image="https://wiki.example.com/labs/ivi.png",
        instructions="Set the head unit to on before you start the phone.\n",
        members=[
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="headunit", selector="device-type=aaos-headunit"), available=2
            ),
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="phone", selector="device-type=phone", optional=True), available=0
            ),
            client_pb2.LeaseTemplateMember(
                member=client_pb2.LeaseMember(name="ecu", exporter_name="ecu-11"), available=1
            ),
        ],
        constraints=[client_pb2.LeaseConstraint(member_names=["headunit", "ecu"], same_label="bus")],
        default_duration=duration_pb2.Duration(seconds=7200),
        default_shared_with=["lab-support"],
        generation=3,
    )
