"""Test-only builders of member-form lease protobufs, with forwards and ports (not part of the public API).

The tests of jumpstarter and of jumpstarter-cli use them.
"""

from __future__ import annotations

from google.protobuf import duration_pb2
from jumpstarter_protocol import client_pb2

_FORWARD_STATES = {
    "Pending": client_pb2.LEASE_FORWARD_STATE_PENDING,
    "Disabled": client_pb2.LEASE_FORWARD_STATE_DISABLED,
    "Listening": client_pb2.LEASE_FORWARD_STATE_LISTENING,
    "Connected": client_pb2.LEASE_FORWARD_STATE_CONNECTED,
    "Failed": client_pb2.LEASE_FORWARD_STATE_FAILED,
}
_FORWARD_END_STATES = {
    "Pending": client_pb2.LEASE_FORWARD_END_STATE_PENDING,
    "Ready": client_pb2.LEASE_FORWARD_END_STATE_READY,
    "Connected": client_pb2.LEASE_FORWARD_END_STATE_CONNECTED,
    "Failed": client_pb2.LEASE_FORWARD_END_STATE_FAILED,
}
_PORT_DIRECTIONS = {"provides": 1, "requires": 2, "connects": 3}
_PORT_ATTACHMENTS = {"socket": 1, "wired": 2, "wireless": 3}
_PORT_TRANSPORTS = {"stream": 1, "datagram": 2, "frame": 3}


def forward_pb(name="bt", *endpoints):
    """A forward. endpoints are `member.port` strings; the default connects headunit.rootcanal and phone.controller."""
    forward = client_pb2.LeaseForward(name=name)
    for endpoint in endpoints or ("headunit.rootcanal", "phone.controller"):
        member, _, port = endpoint.partition(".")
        forward.endpoints.append(client_pb2.ForwardEndpoint(member_name=member, port_name=port))
    return forward


def forward_end_status_pb(member="headunit", state="Ready", message=None):
    """The state of one end of a forward."""
    end = client_pb2.LeaseForwardEndStatus(member_name=member, state=_FORWARD_END_STATES[state])
    if message is not None:
        end.message = message
    return end


def forward_status_pb(name="bt", state="Listening", message=None, ends=()):
    """The state of a forward. ends are (member, state[, message]) tuples or `forward_end_status_pb` messages."""
    status = client_pb2.LeaseForwardStatus(name=name, state=_FORWARD_STATES[state])
    if message is not None:
        status.message = message
    for end in ends:
        status.ends.append(end if isinstance(end, client_pb2.LeaseForwardEndStatus) else forward_end_status_pb(*end))
    return status


def exporter_pb(name="rack3-hu-2", ports=(), labels=None):
    """An exporter. ports are (name, direction[, protocol[, attachment[, transport]]]) tuples."""
    exporter = client_pb2.Exporter(name=f"namespaces/default/exporters/{name}", labels=labels or {}, online=True)
    for name_, direction, *rest in ports:
        port = exporter.ports.add()
        port.name = name_
        port.direction = _PORT_DIRECTIONS[direction]
        if len(rest) > 0 and rest[0]:
            port.protocol = rest[0]
        if len(rest) > 1:
            port.attachment = _PORT_ATTACHMENTS[rest[1]]
        if len(rest) > 2:
            port.transport = _PORT_TRANSPORTS[rest[2]]
    return exporter


def members_lease_pb(
    name="lease-9m2qd", template=None, statuses=None, conditions=(), forwards=(), forward_statuses=()
):
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
    lease.forwards.extend(forwards)
    lease.forward_statuses.extend(forward_statuses)
    return lease


def template_pb(name="ivi", forwards=()):
    return client_pb2.LeaseTemplate(
        forwards=list(forwards),
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
