from __future__ import annotations

import json
import logging
import re
import sys
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass, field
from datetime import datetime, timedelta
from functools import partial
from types import SimpleNamespace

from google.protobuf import duration_pb2, field_mask_pb2, json_format, timestamp_pb2
from grpc import ChannelConnectivity, StatusCode
from grpc.aio import AioRpcError, Channel
from jumpstarter_protocol import client_pb2, client_pb2_grpc, jumpstarter_pb2_grpc, kubernetes_pb2, router_pb2_grpc
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_serializer, model_serializer

from jumpstarter.client.exceptions import ControllerTooOldError, LeaseError
from jumpstarter.client.selectors import extract_match_labels_filter, selector_contains
from jumpstarter.client.status import status_icon
from jumpstarter.common import ExporterStatus
from jumpstarter.common.grpc import translate_grpc_exceptions

logger = logging.getLogger(__name__)


@dataclass
class WithOptions:
    show_online: bool = False
    show_leases: bool = False
    show_status: bool = False
    show_disabled: bool = False
    show_ports: bool = False


def add_display_columns(table, options: WithOptions | None = None):
    if options is None:
        options = WithOptions()
    table.add_column("NAME")
    if not options.show_status:
        table.add_column(" ")
    if options.show_disabled:
        table.add_column("ENABLED")
    if options.show_online:
        table.add_column("ONLINE")
    if options.show_status:
        table.add_column("STATUS")
    table.add_column("LABELS")
    if options.show_ports:
        table.add_column("PORTS")
    if options.show_leases:
        table.add_column("LEASED BY")
        table.add_column("LEASE STATUS")
        table.add_column("RELEASE TIME")


def add_exporter_row(
    table, exporter, options: WithOptions | None = None, lease_info: tuple[str, str, str] | None = None
):
    if options is None:
        options = WithOptions()
    row_data = []
    row_data.append(exporter.name)
    if not options.show_status:
        row_data.append(exporter.status_icon())
    if options.show_disabled:
        row_data.append("yes" if exporter.enabled else "no")
    if options.show_online:
        row_data.append("yes" if exporter.online else "no")
    if options.show_status:
        status_str = str(exporter.status) if exporter.status else "UNKNOWN"
        row_data.append(status_str)
    labels = exporter.labels
    row_data.append(",".join((f"{k}={v}" for k, v in sorted(labels.items()))))
    if options.show_ports:
        row_data.append(",\n".join(port.summary() for port in exporter.ports))
    if options.show_leases:
        if lease_info:
            lease_client, lease_status, expected_release = lease_info
        else:
            lease_client, lease_status, expected_release = "", "Available", ""
        row_data.extend([lease_client, lease_status, expected_release])

    table.add_row(*row_data)


def parse_identifier(identifier: str, kind: str) -> tuple[str, str]:
    segments = identifier.split("/")
    if len(segments) != 4:
        raise ValueError(f"incorrect number of segments in identifier, expecting 4, got {len(segments)}")
    if segments[0] != "namespaces":
        raise ValueError(f"incorrect first segment in identifier, expecting namespaces, got {segments[0]}")
    if segments[2] != kind:
        raise ValueError(f"incorrect third segment in identifier, expecting {kind}, got {segments[2]}")
    return segments[1], segments[3]


def parse_client_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "clients")


def parse_exporter_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "exporters")


def parse_lease_identifier(identifier: str) -> tuple[str, str]:
    return parse_identifier(identifier, "leases")


def _short_name(identifier: str) -> str:
    """The object name of a resource name, or the value itself when it is already bare."""
    return identifier.rsplit("/", 1)[-1]


_MEMBER_STATES = {
    client_pb2.LEASE_MEMBER_STATE_PENDING: "Pending",
    client_pb2.LEASE_MEMBER_STATE_BOUND: "Bound",
    client_pb2.LEASE_MEMBER_STATE_OMITTED: "Omitted",
    client_pb2.LEASE_MEMBER_STATE_LOST: "Lost",
}

_FORWARD_STATES = {
    client_pb2.LEASE_FORWARD_STATE_PENDING: "Pending",
    client_pb2.LEASE_FORWARD_STATE_DISABLED: "Disabled",
    client_pb2.LEASE_FORWARD_STATE_LISTENING: "Listening",
    client_pb2.LEASE_FORWARD_STATE_CONNECTED: "Connected",
    client_pb2.LEASE_FORWARD_STATE_FAILED: "Failed",
}

# The enums of jumpstarter.v1.PortReport, by number. The zero value of the
# attachment means socket, and the zero value of the transport means stream.
_PORT_DIRECTIONS = {1: "provides", 2: "requires", 3: "connects"}
_PORT_ATTACHMENTS = {0: "socket", 1: "socket", 2: "wired", 3: "wireless"}
_PORT_TRANSPORTS = {0: "stream", 1: "stream", 2: "datagram", 3: "frame"}

# A forward name and a port name are DNS labels, as a role name is.
_DNS_LABEL = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_MAX_DNS_LABEL = 63
MAX_FORWARDS = 16
MIN_FORWARD_ENDPOINTS = 2
MAX_FORWARD_ENDPOINTS = 8

# The answer of a controller that predates lease members to a request that
# has neither `selector` nor `exporter_name`.
_OLD_CONTROLLER_CREATE_DETAILS = "one of selector or exporter_name is required"

_TOO_OLD_MEMBERS = (
    "the controller is too old for leases with members or lease templates: "
    "upgrade the controller, or request one exporter with a selector"
)
_TOO_OLD_TEMPLATES = "the controller is too old for lease templates: upgrade the controller"
_DELETE_UNUSABLE_LEASE_TIMEOUT = 15.0
_TOO_OLD_FORWARDS = (
    "the controller is too old for port forwards: it created the lease without its forwards. "
    "Upgrade the controller, or request the members without forwards"
)


def is_dns_label(value: str) -> bool:
    """Whether value can be a role name, a forward name or a port name."""
    return len(value) <= _MAX_DNS_LABEL and bool(_DNS_LABEL.match(value))


def _arrows() -> tuple[str, str]:
    """The signs between the endpoints of a forward, in ASCII where the output cannot show the arrows."""
    try:
        "\u2192\u2194".encode(getattr(sys.stdout, "encoding", None) or "utf-8")
    except (UnicodeError, LookupError):
        return "->", "<->"
    return "\u2192", "\u2194"


class PortReport(BaseModel):
    """A port that a driver of an exporter declares. It has no local address."""

    name: str
    direction: str  # provides | requires | connects
    protocol: str | None = None
    attachment: str = "socket"  # socket | wired | wireless
    transport: str = "stream"  # stream | datagram | frame

    @classmethod
    def from_protobuf(cls, data) -> PortReport:
        return cls(
            name=data.name,
            direction=_PORT_DIRECTIONS.get(data.direction, "unspecified"),
            protocol=data.protocol if data.HasField("protocol") and data.protocol else None,
            attachment=_PORT_ATTACHMENTS.get(data.attachment, "socket"),
            transport=_PORT_TRANSPORTS.get(data.transport, "stream"),
        )

    @model_serializer(mode="wrap")
    def _omit_defaults(self, handler):
        # A port prints as its declaration: without the values that it does not set.
        data = handler(self)
        if self.protocol is None:
            data.pop("protocol", None)
        if self.attachment == "socket":
            data.pop("attachment", None)
        if self.transport == "stream":
            data.pop("transport", None)
        return data

    @property
    def forwardable(self) -> bool:
        """Whether a forward can use the port: only a socket that carries a TCP stream (DD-8, DD-9)."""
        return self.attachment == "socket" and self.transport == "stream"

    def summary(self) -> str:
        """`name (direction, protocol)`, with the attachment and the transport that a forward cannot use."""
        details = [self.direction]
        if self.protocol:
            details.append(self.protocol)
        if self.attachment != "socket":
            details.append(self.attachment)
        if self.transport != "stream":
            details.append(self.transport)
        return f"{self.name} ({', '.join(details)})"


class ForwardEndpoint(BaseModel):
    """One end of a forward: a member of the lease and a port of its exporter."""

    member_name: str
    port_name: str

    @classmethod
    def from_protobuf(cls, data: client_pb2.ForwardEndpoint) -> ForwardEndpoint:
        return cls(member_name=data.member_name, port_name=data.port_name)

    def to_protobuf(self) -> client_pb2.ForwardEndpoint:
        return client_pb2.ForwardEndpoint(member_name=self.member_name, port_name=self.port_name)

    def __str__(self) -> str:
        return f"{self.member_name}.{self.port_name}"


class LeaseForward(BaseModel):
    """A forward of a lease or of a lease template: it connects ports on different members."""

    name: str
    endpoints: list[ForwardEndpoint]

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseForward) -> LeaseForward:
        return cls(name=data.name, endpoints=[ForwardEndpoint.from_protobuf(e) for e in data.endpoints])

    def to_protobuf(self) -> client_pb2.LeaseForward:
        return client_pb2.LeaseForward(name=self.name, endpoints=[e.to_protobuf() for e in self.endpoints])

    def describe(self, directions: Mapping[tuple[str, str], str] | None = None) -> str:
        """The endpoints of the forward, for display.

        directions gives the direction of a port by (member, port). With
        them, an arrow points from the `provides` port to each `requires`
        port, and a double arrow connects the two ends of a path. Without
        them, the endpoints are in the order of the lease.
        """
        known = [(directions or {}).get((e.member_name, e.port_name)) for e in self.endpoints]
        arrow, both = _arrows()
        if len(self.endpoints) == 2 and known == ["connects", "connects"]:
            return f"{self.endpoints[0]} {both} {self.endpoints[1]}"
        if len(known) > 1 and known.count("provides") == 1 and known.count("requires") == len(known) - 1:
            source = self.endpoints[known.index("provides")]
            targets = [str(e) for e, direction in zip(self.endpoints, known, strict=True) if direction == "requires"]
            return f"{source} {arrow} {', '.join(targets)}"
        return ", ".join(str(e) for e in self.endpoints)


class LeaseForwardStatus(BaseModel):
    name: str
    state: str  # Pending | Disabled | Listening | Connected | Failed
    message: str | None = None

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseForwardStatus) -> LeaseForwardStatus:
        return cls(
            name=data.name,
            state=_FORWARD_STATES.get(data.state, "Unknown"),
            message=data.message if data.HasField("message") and data.message else None,
        )

    @property
    def failed(self) -> bool:
        return self.state == "Failed"

    @property
    def usable(self) -> bool:
        """Whether the setup of the forward is complete: it is Listening or Connected."""
        return self.state in ("Listening", "Connected")

    def describe_failure(self) -> str:
        """Why a Failed or Disabled forward does not operate, for an error message."""
        # The name is not known when only the condition of the lease says that a forward failed.
        subject = f"forward '{self.name}'" if self.name else "a forward"
        text = f"{subject} {'is disabled' if self.state == 'Disabled' else 'failed'}"
        if self.message:
            text += f": {self.message}"
        return text


def validate_forwards(forwards: Iterable[LeaseForward], member_names: Iterable[str]) -> None:  # noqa: C901
    """Check the forwards of a request against its members, as the admission rules of the Lease do.

    :raises ValueError: with the forward and the rule that it breaks
    """
    forwards = list(forwards)
    members = list(member_names)
    if len(forwards) > MAX_FORWARDS:
        raise ValueError(f"too many forwards (max {MAX_FORWARDS})")
    seen: set[str] = set()
    for forward in forwards:
        if not is_dns_label(forward.name):
            raise ValueError(
                f"invalid forward name {forward.name!r} (lowercase letters, digits and '-', at most "
                f"{_MAX_DNS_LABEL} characters)"
            )
        if forward.name in seen:
            raise ValueError(f"the forward '{forward.name}' is given more than once")
        seen.add(forward.name)
        if forward.name in members:
            raise ValueError(f"'{forward.name}' is a member: a forward and a member cannot have the same name")
        if not MIN_FORWARD_ENDPOINTS <= len(forward.endpoints) <= MAX_FORWARD_ENDPOINTS:
            raise ValueError(
                f"the forward '{forward.name}' has {len(forward.endpoints)} endpoints: a forward connects "
                f"{MIN_FORWARD_ENDPOINTS} to {MAX_FORWARD_ENDPOINTS} endpoints"
            )
        used: set[str] = set()
        for endpoint in forward.endpoints:
            if endpoint.member_name not in members:
                raise ValueError(
                    f"the forward '{forward.name}' names the member '{endpoint.member_name}', which the request "
                    f"does not define (members: {', '.join(members) or 'none'})"
                )
            if endpoint.member_name in used:
                raise ValueError(
                    f"the forward '{forward.name}' has two endpoints on the member '{endpoint.member_name}': "
                    "each endpoint is on a different member"
                )
            used.add(endpoint.member_name)
            if not is_dns_label(endpoint.port_name):
                raise ValueError(
                    f"the forward '{forward.name}' has the invalid port name {endpoint.port_name!r} (lowercase "
                    f"letters, digits and '-', at most {_MAX_DNS_LABEL} characters)"
                )


class LeaseMember(BaseModel):
    """One role of a member-form lease, or of a lease template."""

    name: str
    selector: str | None = None
    exporter_name: str | None = None
    optional: bool = False
    allow_disabled: bool = False
    excluded: bool = False

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseMember) -> LeaseMember:
        return cls(
            name=data.name,
            selector=data.selector if data.HasField("selector") else None,
            exporter_name=data.exporter_name if data.HasField("exporter_name") else None,
            optional=data.optional,
            allow_disabled=data.allow_disabled,
            excluded=data.excluded,
        )

    def to_protobuf(self) -> client_pb2.LeaseMember:
        member = client_pb2.LeaseMember(name=self.name, optional=self.optional, allow_disabled=self.allow_disabled)
        if self.exporter_name:
            member.exporter_name = self.exporter_name
        elif self.selector:
            member.selector = self.selector
        return member

    def selection(self) -> str:
        """How the member finds its exporter, for display."""
        if self.exporter_name:
            return f"exporter {self.exporter_name}"
        return self.selector or ""


class LeaseConstraint(BaseModel):
    member_names: list[str]
    same_label: str | None = None

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseConstraint) -> LeaseConstraint:
        return cls(
            member_names=list(data.member_names),
            same_label=data.same_label if data.HasField("same_label") else None,
        )

    def to_protobuf(self) -> client_pb2.LeaseConstraint:
        constraint = client_pb2.LeaseConstraint(member_names=self.member_names)
        if self.same_label:
            constraint.same_label = self.same_label
        return constraint


class LeaseTemplateUse(BaseModel):
    """The template a lease was made from. The inputs of the request are not echoed back."""

    template: str
    generation: int = 0

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseTemplateUse) -> LeaseTemplateUse:
        return cls(template=_short_name(data.template), generation=data.generation)


class LeaseMemberStatus(BaseModel):
    name: str
    state: str
    exporter: str | None = None
    reason: str | None = None
    message: str | None = None
    priority: int = 0
    spot_access: bool = False

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseMemberStatus) -> LeaseMemberStatus:
        return cls(
            name=data.name,
            state=_MEMBER_STATES.get(data.state, "Unknown"),
            exporter=_short_name(data.exporter) if data.HasField("exporter") and data.exporter else None,
            reason=data.reason if data.HasField("reason") else None,
            message=data.message if data.HasField("message") else None,
            priority=data.priority,
            spot_access=data.spot_access,
        )

    @property
    def bound(self) -> bool:
        return self.state == "Bound" and bool(self.exporter)

    @property
    def lost(self) -> bool:
        return self.state == "Lost"

    def describe_loss(self) -> str:
        """Why a Lost member has no exporter, for an error message."""
        text = f"member '{self.name}' lost its exporter"
        if self.exporter:
            text += f" {self.exporter}"
        if self.reason:
            text += f" ({self.reason})"
        if self.message:
            text += f": {self.message}"
        return text


class _class_or_instance_method:
    """A method that gets the class when it is called on the class, and the instance otherwise."""

    def __init__(self, function):
        self._function = function

    def __get__(self, instance, owner):
        return partial(self._function, owner if instance is None else instance)


class Exporter(BaseModel):
    namespace: str
    name: str
    labels: dict[str, str]
    online: bool = False
    status: ExporterStatus | None = None
    enabled: bool = True
    lease: Lease | None = None
    deprecated_labels: dict[str, str] = Field(default_factory=dict)
    # The ports that the drivers of the exporter declare. A forward of a lease connects them.
    ports: list[PortReport] = Field(default_factory=list)

    @field_serializer("status", when_used="json")
    def serialize_status(self, status: ExporterStatus | None):
        # emit the status name instead of the raw protobuf integer
        return status.name if status is not None else None

    @model_serializer(mode="wrap")
    def _omit_ports_of_exporter_without_ports(self, handler):
        # An exporter that declares no ports prints as it did before ports existed.
        data = handler(self)
        if not self.ports:
            data.pop("ports", None)
        return data

    def port(self, name: str) -> PortReport | None:
        for port in self.ports:
            if port.name == name:
                return port
        return None

    @classmethod
    def from_protobuf(cls, data: client_pb2.Exporter) -> Exporter:
        namespace, name = parse_exporter_identifier(data.name)
        status = None
        if hasattr(data, "status") and data.status:
            status = ExporterStatus.from_proto(data.status)
        return cls(
            namespace=namespace,
            name=name,
            labels=dict(data.labels),
            online=data.online,
            status=status,
            enabled=data.enabled if data.HasField("enabled") else True,
            deprecated_labels=dict(data.deprecated_labels),
            ports=[PortReport.from_protobuf(port) for port in data.ports],
        )

    @classmethod
    def rich_add_columns(cls, table, options: WithOptions | None = None):
        add_display_columns(table, options)

    def rich_add_rows(self, table, options: WithOptions | None = None):
        lease_info = None
        if options and options.show_leases and self.lease:
            lease_client = self.lease.client
            lease_status = self.lease.get_status()
            release_time = ""
            if self.lease.effective_end_time:
                # Ended: use actual end time
                release_time = self.lease.effective_end_time.strftime("%Y-%m-%d %H:%M:%S")
            elif self.lease.effective_begin_time:
                # Active: calculate expected end
                release_time = self.lease.effective_begin_time + self.lease.duration
                release_time = release_time.strftime("%Y-%m-%d %H:%M:%S")
            elif self.lease.begin_time:
                # Scheduled: calculate expected end
                release_time = self.lease.begin_time + self.lease.duration
                release_time = release_time.strftime("%Y-%m-%d %H:%M:%S")
            lease_info = (lease_client, lease_status, release_time)
        elif options and options.show_leases:
            lease_info = ("", "Available", "")
        add_exporter_row(table, self, options, lease_info)

    def status_icon(self) -> str:
        """Return an icon representing the exporter's runtime status.

        Delegates to :func:`jumpstarter.client.status.status_icon` which
        selects emoji or ASCII based on terminal capabilities.
        """
        return status_icon(self.status)

    def rich_add_names(self, names):
        names.append(self.name)


class Lease(BaseModel):
    namespace: str
    name: str
    selector: str
    exporter_name: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    context: dict[str, str] = Field(default_factory=dict)
    duration: timedelta
    effective_duration: timedelta | None = None
    begin_time: datetime | None = None
    client: str
    exporter: str
    conditions: list[kubernetes_pb2.Condition]
    effective_begin_time: datetime | None = None
    effective_end_time: datetime | None = None
    deprecated_labels: dict[str, str] = Field(default_factory=dict)
    # The owner's desired sharing intent (Lease.spec.sharedWith). May include names
    # that were denied by exporter access policy or that don't exist. Use this only
    # when surfacing intent (e.g. `jmp share list`); route access decisions through
    # effective_shared_with / is_accessible_by instead.
    shared_with: list[str] = Field(default_factory=list)
    # The controller-derived set actually granted access (Lease.status.sharedWith),
    # after policy/existence filtering. A name in shared_with but not here was denied.
    effective_shared_with: list[str] = Field(default_factory=list)
    # Set for a member-form lease only. `exporter` and `selector` are then empty,
    # and member_statuses has the exporter of each role.
    members: list[LeaseMember] = Field(default_factory=list)
    constraints: list[LeaseConstraint] = Field(default_factory=list)
    template: LeaseTemplateUse | None = None
    member_statuses: list[LeaseMemberStatus] = Field(default_factory=list)
    # Set only for a member-form lease with forwards: what connects its members,
    # and the state of each forward.
    forwards: list[LeaseForward] = Field(default_factory=list)
    forward_statuses: list[LeaseForwardStatus] = Field(default_factory=list)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ser_json_timedelta="float",
        ignored_types=(_class_or_instance_method,),
    )

    @field_serializer("conditions")
    def serialize_conditions(self, conditions: list[kubernetes_pb2.Condition], _info):
        return [json_format.MessageToDict(condition) for condition in conditions]

    @model_serializer(mode="wrap")
    def _omit_member_fields_of_scalar_lease(self, handler):
        data = handler(self)
        if not self.members:
            for key in ("members", "constraints", "template", "member_statuses"):
                if not data.get(key):
                    data.pop(key, None)
        if not self.forwards:
            # A lease without forwards prints as it did before forwards existed.
            for key in ("forwards", "forward_statuses"):
                if not data.get(key):
                    data.pop(key, None)
        return data

    @property
    def is_member_form(self) -> bool:
        return bool(self.members)

    def member_status(self, role: str) -> LeaseMemberStatus | None:
        for status in self.member_statuses:
            if status.name == role:
                return status
        return None

    def member_exporters(self) -> dict[str, str | None]:
        """The exporter of each role, in the order of the request. None for a role without one."""
        exporters: dict[str, str | None] = {}
        for member in self.members:
            status = self.member_status(member.name)
            exporters[member.name] = status.exporter if status is not None and status.bound else None
        return exporters

    def lost_members(self) -> list[LeaseMemberStatus]:
        return [status for status in self.member_statuses if status.lost]

    def forward(self, name: str) -> LeaseForward | None:
        for forward in self.forwards:
            if forward.name == name:
                return forward
        return None

    def forward_status(self, name: str) -> LeaseForwardStatus | None:
        for status in self.forward_statuses:
            if status.name == name:
                return status
        return None

    def forward_state(self, name: str) -> str:
        """The state of a forward. Pending for a forward that the controller did not report yet."""
        status = self.forward_status(name)
        return status.state if status is not None else "Pending"

    def failed_forwards(self) -> list[LeaseForwardStatus]:
        return [status for status in self.forward_statuses if status.failed]

    def holds_exporter(self, exporter: str) -> bool:
        """Whether this lease has the exporter, as its one exporter or as a bound member."""
        if self.exporter:
            return self.exporter == exporter
        return any(status.bound and status.exporter == exporter for status in self.member_statuses)

    @classmethod
    def from_protobuf(cls, data: client_pb2.Lease) -> Lease:
        namespace, name = parse_lease_identifier(data.name)

        _, client = parse_client_identifier(data.client)
        if data.exporter != "":
            _, exporter = parse_exporter_identifier(data.exporter)
        else:
            exporter = ""

        effective_duration = None
        if data.HasField("effective_duration"):
            effective_duration = data.effective_duration.ToTimedelta()

        begin_time = None
        if data.HasField("begin_time"):
            begin_time = data.begin_time.ToDatetime(
                tzinfo=datetime.now().astimezone().tzinfo,
            )

        effective_begin_time = None
        if data.HasField("effective_begin_time"):
            effective_begin_time = data.effective_begin_time.ToDatetime(
                tzinfo=datetime.now().astimezone().tzinfo,
            )

        effective_end_time = None
        if data.HasField("effective_end_time"):
            effective_end_time = data.effective_end_time.ToDatetime(
                tzinfo=datetime.now().astimezone().tzinfo,
            )

        return cls(
            namespace=namespace,
            name=name,
            selector=data.selector,
            exporter_name=data.exporter_name if data.exporter_name else None,
            tags=dict(data.tags) if data.tags else {},
            context=dict(data.context) if data.context else {},
            duration=data.duration.ToTimedelta(),
            effective_duration=effective_duration,
            begin_time=begin_time,
            client=client,
            exporter=exporter,
            effective_begin_time=effective_begin_time,
            effective_end_time=effective_end_time,
            conditions=list(data.conditions),
            deprecated_labels=dict(data.deprecated_labels),
            shared_with=list(data.shared_with),
            effective_shared_with=list(data.effective_shared_with),
            members=[LeaseMember.from_protobuf(member) for member in data.members],
            constraints=[LeaseConstraint.from_protobuf(constraint) for constraint in data.constraints],
            template=LeaseTemplateUse.from_protobuf(data.template) if data.HasField("template") else None,
            member_statuses=[LeaseMemberStatus.from_protobuf(status) for status in data.member_statuses],
            forwards=[LeaseForward.from_protobuf(forward) for forward in data.forwards],
            forward_statuses=[LeaseForwardStatus.from_protobuf(status) for status in data.forward_statuses],
        )

    def is_accessible_by(self, client_name: str) -> bool:
        """Whether client_name may actually connect to this lease.

        Mirrors the controller's Lease.IsAccessibleBy: the owner always has
        access, and shared clients have access only if they survived policy
        filtering (i.e. appear in effective_shared_with, not merely the owner's
        desired shared_with intent).
        """
        return client_name == self.client or client_name in self.effective_shared_with

    @staticmethod
    def _add_columns(table, show_template: bool, show_forwards: bool = False):
        table.add_column("NAME", no_wrap=True)
        table.add_column("SELECTOR")
        if show_template:
            table.add_column("TEMPLATE")
        table.add_column("EXPIRES AT")
        table.add_column("REMAINING")
        table.add_column("CLIENT")
        table.add_column("EXPORTER")
        if show_forwards:
            table.add_column("FORWARDS")
        table.add_column("TAGS")
        table.add_column("SHARED WITH")

    # The TEMPLATE column is shown only for a lease from a template, and the
    # FORWARDS column only for a lease with forwards, which the class alone
    # cannot know: `Lease.rich_add_columns(table)` still works.
    @_class_or_instance_method
    def rich_add_columns(self, table, **kwargs):
        Lease._add_columns(
            table,
            show_template=isinstance(self, Lease) and self.template is not None,
            show_forwards=isinstance(self, Lease) and bool(self.forwards),
        )

    def _forwards_cell(self) -> str:
        """The FORWARDS column: one `forward=State` line for each forward."""
        return ",\n".join(f"{forward.name}={self.forward_state(forward.name)}" for forward in self.forwards)

    def _exporter_cell(self) -> str:
        """The EXPORTER column: the exporter, or one `role=exporter` line for each member."""
        if not self.members:
            return self.exporter
        lines = []
        for member in self.members:
            status = self.member_status(member.name)
            if status is not None and status.bound:
                value = status.exporter
            elif status is not None and status.lost:
                value = f"{status.exporter or ''}(lost)"
            elif status is not None and status.state == "Omitted":
                value = "(omitted)"
            else:
                value = "(pending)"
            lines.append(f"{member.name}={value}")
        return ",\n".join(lines)

    def _compute_expires_at(self):
        if self.effective_end_time:
            return self.effective_end_time
        if self.effective_begin_time and self.duration:
            return self.effective_begin_time + self.duration
        if self.begin_time and self.duration:
            return self.begin_time + self.duration
        return None

    @staticmethod
    def _format_remaining(expires_at):
        if expires_at is None:
            return ""
        now = datetime.now(tz=expires_at.tzinfo)
        remaining = expires_at - now
        if remaining.total_seconds() <= 0:
            return "expired"
        days = remaining.days
        hours = remaining.seconds // 3600
        minutes = (remaining.seconds % 3600) // 60
        parts = []
        if days:
            parts.append(f"{days}d")
        if hours:
            parts.append(f"{hours}h")
        if minutes or not parts:
            parts.append(f"{minutes}m")
        return " ".join(parts)

    def rich_add_rows(
        self,
        table,
        viewer: str | None = None,
        show_template: bool | None = None,
        show_forwards: bool | None = None,
    ):
        if show_template is None:
            show_template = self.template is not None
        if show_forwards is None:
            show_forwards = bool(self.forwards)
        expires_at = self._compute_expires_at()
        expires_at_str = expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else ""
        remaining_str = self._format_remaining(expires_at)

        tags_str = ",".join(f"{k}={v}" for k, v in sorted(self.tags.items()))
        # Show the effective (granted) set here: this overview should reflect who
        # actually has access, not unfiltered intent. `jmp share list` renders both.
        shared_str = ",".join(self.effective_shared_with)

        client_str = self.client
        if viewer and viewer != self.client and self.is_accessible_by(viewer):
            client_str = f"{self.client} (shared)"

        template_cell = [self.template.template if self.template else ""] if show_template else []
        table.add_row(
            self.name,
            self.selector,
            *template_cell,
            expires_at_str,
            remaining_str,
            client_str,
            self._exporter_cell(),
            *([self._forwards_cell()] if show_forwards else []),
            tags_str,
            shared_str,
        )

    def rich_add_names(self, names):
        names.append(self.name)

    @computed_field
    @property
    def status(self) -> str:
        """Derived lease status, also included in serialized output"""
        return self.get_status()

    def get_status(self) -> str:
        """Get the lease status based on conditions"""
        # Check if lease has ended (effective_end_time is set)
        if self.effective_end_time:
            return "Ended"

        if not self.conditions:
            return "Unknown"

        if self.members and any(c.type == "Degraded" and c.status == "True" for c in self.conditions):
            return "Degraded"

        latest_condition = self.conditions[-1]

        if latest_condition.type == "Ready" and latest_condition.status == "True":
            return "In-Use"
        elif latest_condition.type == "Ready" and latest_condition.status == "False":
            return "Waiting"
        elif latest_condition.type == "Expired":
            return "Expired"
        else:
            return latest_condition.reason if latest_condition.reason else "Unknown"


class ExporterList(BaseModel):
    exporters: list[Exporter]
    next_page_token: str | None = Field(exclude=True)
    include_online: bool = Field(default=False, exclude=True)
    include_leases: bool = Field(default=False, exclude=True)
    include_status: bool = Field(default=False, exclude=True)
    include_disabled: bool = Field(default=False, exclude=True)
    include_ports: bool = Field(default=False, exclude=True)

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListExportersResponse) -> ExporterList:
        return cls(
            exporters=list(map(Exporter.from_protobuf, data.exporters)),
            next_page_token=data.next_page_token,
        )

    def _visible_exporters(self) -> list[Exporter]:
        """Return exporters filtered by enabled status unless include_disabled is set."""
        if self.include_disabled:
            return self.exporters
        return [e for e in self.exporters if e.enabled]

    def _display_options(self) -> WithOptions:
        return WithOptions(
            show_online=self.include_online,
            show_leases=self.include_leases,
            show_status=self.include_status,
            show_disabled=self.include_disabled,
            show_ports=self.include_ports,
        )

    def rich_add_columns(self, table):
        Exporter.rich_add_columns(table, self._display_options())

    def rich_add_rows(self, table):
        options = self._display_options()
        for exporter in self._visible_exporters():
            exporter.rich_add_rows(table, options)

    def rich_add_names(self, names):
        for exporter in self._visible_exporters():
            exporter.rich_add_names(names)

    def _dump_exporter(self, exporter, exclude_fields, **kwargs) -> dict:
        data = exporter.model_dump(exclude=exclude_fields, **kwargs)
        if self.include_ports:
            # With the ports asked for, an exporter without ports says so.
            data.setdefault("ports", [])
        return data

    def _excluded_fields(self) -> set[str]:
        hidden = {
            "lease": not self.include_leases,
            "online": not self.include_online,
            "status": not self.include_status,
            "enabled": not self.include_disabled,
            "ports": not self.include_ports,
        }
        return {name for name, excluded in hidden.items() if excluded}

    def model_dump_json(self, **kwargs):
        json_kwargs = {k: v for k, v in kwargs.items() if k in {"indent", "separators", "sort_keys", "ensure_ascii"}}

        exclude_fields = self._excluded_fields()

        data = {
            "exporters": [
                self._dump_exporter(exporter, exclude_fields, mode="json")
                for exporter in self._visible_exporters()
            ]
        }
        return json.dumps(data, **json_kwargs)

    def model_dump(self, **kwargs):
        exclude_fields = self._excluded_fields()

        caller_exclude = kwargs.pop("exclude", None)
        if caller_exclude:
            exclude_fields |= set(caller_exclude)

        return {
            "exporters": [
                self._dump_exporter(exporter, exclude_fields, **kwargs)
                for exporter in self._visible_exporters()
            ]
        }


class LeaseList(BaseModel):
    leases: list[Lease]
    next_page_token: str | None = Field(exclude=True)

    model_config = ConfigDict(ignored_types=(_class_or_instance_method,))

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListLeasesResponse) -> LeaseList:
        return cls(
            leases=list(map(Lease.from_protobuf, data.leases)),
            next_page_token=data.next_page_token,
        )

    def _show_template(self) -> bool:
        return any(lease.template is not None for lease in self.leases)

    def _show_forwards(self) -> bool:
        return any(lease.forwards for lease in self.leases)

    @_class_or_instance_method
    def rich_add_columns(self, table, **kwargs):
        Lease._add_columns(
            table,
            show_template=isinstance(self, LeaseList) and self._show_template(),
            show_forwards=isinstance(self, LeaseList) and self._show_forwards(),
        )

    def rich_add_rows(self, table, viewer: str | None = None):
        show_template = self._show_template()
        show_forwards = self._show_forwards()
        for lease in self.leases:
            lease.rich_add_rows(table, viewer=viewer, show_template=show_template, show_forwards=show_forwards)

    def rich_add_names(self, names):
        for lease in self.leases:
            lease.rich_add_names(names)

    def filter_by_selector(self, filter_selector: str | None) -> LeaseList:
        """Filter leases client-side by matching against their selector.

        This handles matchExpressions that are not supported by the current server-side filtering.
        Note: Pagination token is cleared since server pagination becomes invalid after client-side filtering.
        """
        if not filter_selector:
            return self
        filtered = []
        for lease in self.leases:
            try:
                if lease.members:
                    # A member-form lease has no selector of its own: it matches
                    # when the selector of one of its roles does.
                    if any(m.selector and selector_contains(m.selector, filter_selector) for m in lease.members):
                        filtered.append(lease)
                elif selector_contains(lease.selector, filter_selector):
                    filtered.append(lease)
            except ValueError as error:
                logger.warning(
                    "skipping lease %s: cannot evaluate filter %r against selector %r: %s",
                    lease.name,
                    filter_selector,
                    lease.selector,
                    error,
                )
        return LeaseList(leases=filtered, next_page_token=None)

    def filter_by_client(self, client_name: str) -> LeaseList:
        filtered = [lease for lease in self.leases if lease.is_accessible_by(client_name)]
        return LeaseList(leases=filtered, next_page_token=None)


class LeaseTemplateMember(BaseModel):
    member: LeaseMember
    # The number of exporters that the client can lease now for this member.
    available: int = 0


class LeaseTemplate(BaseModel):
    namespace: str
    name: str
    description: str = ""
    docs: list[str] = Field(default_factory=list)
    image: str | None = None
    instructions: str | None = None
    members: list[LeaseTemplateMember] = Field(default_factory=list)
    constraints: list[LeaseConstraint] = Field(default_factory=list)
    default_duration: timedelta | None = None
    default_shared_with: list[str] = Field(default_factory=list)
    generation: int = 0
    # What connects the members of a lease from this template. A request cannot change them.
    forwards: list[LeaseForward] = Field(default_factory=list)

    model_config = ConfigDict(ser_json_timedelta="float", ignored_types=(_class_or_instance_method,))

    @model_serializer(mode="wrap")
    def _omit_forwards_of_template_without_forwards(self, handler):
        data = handler(self)
        if not self.forwards:
            data.pop("forwards", None)
        return data

    def forward(self, name: str) -> LeaseForward | None:
        for forward in self.forwards:
            if forward.name == name:
                return forward
        return None

    @classmethod
    def from_protobuf(cls, data: client_pb2.LeaseTemplate) -> LeaseTemplate:
        namespace, name = parse_identifier(data.name, "leasetemplates")
        return cls(
            namespace=namespace,
            name=name,
            description=data.description,
            docs=list(data.docs),
            image=data.image if data.HasField("image") else None,
            instructions=data.instructions if data.HasField("instructions") else None,
            members=[
                LeaseTemplateMember(member=LeaseMember.from_protobuf(m.member), available=m.available)
                for m in data.members
            ],
            constraints=[LeaseConstraint.from_protobuf(constraint) for constraint in data.constraints],
            default_duration=data.default_duration.ToTimedelta() if data.HasField("default_duration") else None,
            default_shared_with=list(data.default_shared_with),
            generation=data.generation,
            forwards=[LeaseForward.from_protobuf(forward) for forward in data.forwards],
        )

    def member(self, role: str) -> LeaseMember | None:
        for entry in self.members:
            if entry.member.name == role:
                return entry.member
        return None

    def role_names(self) -> list[str]:
        return [entry.member.name for entry in self.members]

    def available_summary(self) -> str:
        """`n/m`: the members with at least one exporter that can be leased now, of all members."""
        return f"{sum(1 for entry in self.members if entry.available > 0)}/{len(self.members)}"

    @staticmethod
    def _add_columns(table, show_forwards: bool):
        table.add_column("NAME", no_wrap=True)
        table.add_column("MEMBERS")
        if show_forwards:
            table.add_column("FORWARDS")
        table.add_column("AVAILABLE")
        table.add_column("DESCRIPTION", no_wrap=True, overflow="ellipsis", max_width=60)

    # The FORWARDS column is shown only for a template with forwards, which the
    # class alone cannot know: `LeaseTemplate.rich_add_columns(table)` still works.
    @_class_or_instance_method
    def rich_add_columns(self, table, **kwargs):
        LeaseTemplate._add_columns(table, show_forwards=isinstance(self, LeaseTemplate) and bool(self.forwards))

    def rich_add_rows(self, table, show_forwards: bool | None = None, **kwargs):
        if show_forwards is None:
            show_forwards = bool(self.forwards)
        table.add_row(
            self.name,
            ", ".join(f"{entry.member.name}{'*' if entry.member.optional else ''}" for entry in self.members),
            *([", ".join(forward.name for forward in self.forwards)] if show_forwards else []),
            self.available_summary(),
            self.description.splitlines()[0] if self.description else "",
        )

    def rich_add_names(self, names):
        names.append(self.name)


class LeaseTemplateList(BaseModel):
    lease_templates: list[LeaseTemplate]
    next_page_token: str | None = Field(exclude=True)

    model_config = ConfigDict(ignored_types=(_class_or_instance_method,))

    @classmethod
    def from_protobuf(cls, data: client_pb2.ListLeaseTemplatesResponse) -> LeaseTemplateList:
        return cls(
            lease_templates=list(map(LeaseTemplate.from_protobuf, data.lease_templates)),
            next_page_token=data.next_page_token,
        )

    def _show_forwards(self) -> bool:
        return any(template.forwards for template in self.lease_templates)

    @_class_or_instance_method
    def rich_add_columns(self, table, **kwargs):
        LeaseTemplate._add_columns(
            table, show_forwards=isinstance(self, LeaseTemplateList) and self._show_forwards()
        )

    def rich_add_rows(self, table, **kwargs):
        show_forwards = self._show_forwards()
        for template in self.lease_templates:
            template.rich_add_rows(table, show_forwards=show_forwards)

    def rich_add_names(self, names):
        for template in self.lease_templates:
            template.rich_add_names(names)


def _raise_for_template_rpc(error: AioRpcError, name: str | None = None):
    """Give the errors of the template RPCs that have a clear cause their own message."""
    if error.code() == StatusCode.UNIMPLEMENTED:
        raise ControllerTooOldError(_TOO_OLD_TEMPLATES) from None
    if error.code() == StatusCode.NOT_FOUND and name is not None:
        raise LeaseError(f"lease template '{name}' not found") from None
    raise error


def _raise_for_member_form_create(error: AioRpcError):
    """Give the answers to a request with members or a template their own message.

    A controller that predates lease members sees a request without a
    selector and without an exporter name, and rejects it.
    """
    details = error.details() or ""
    if error.code() == StatusCode.UNIMPLEMENTED or (
        error.code() == StatusCode.INVALID_ARGUMENT and _OLD_CONTROLLER_CREATE_DETAILS in details
    ):
        raise ControllerTooOldError(_TOO_OLD_MEMBERS) from None
    if error.code() in (StatusCode.INVALID_ARGUMENT, StatusCode.NOT_FOUND, StatusCode.PERMISSION_DENIED):
        raise LeaseError(f"the lease request was rejected: {details}") from None
    raise error


@dataclass(kw_only=True, slots=True)
class ClientService:
    channel: Channel
    namespace: str
    stub: client_pb2_grpc.ClientServiceStub = field(init=False)

    def __post_init__(self):
        self.stub = client_pb2_grpc.ClientServiceStub(channel=self.channel)

    async def GetExporter(self, *, name: str, show_hidden_labels: bool = False):
        with translate_grpc_exceptions():
            exporter = await self.stub.GetExporter(
                client_pb2.GetExporterRequest(
                    name=f"namespaces/{self.namespace}/exporters/{name}",
                    show_hidden_labels=show_hidden_labels,
                )
            )
        return Exporter.from_protobuf(exporter)

    async def ListExporters(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
        show_hidden_labels: bool = False,
    ):
        with translate_grpc_exceptions():
            exporters = await self.stub.ListExporters(
                client_pb2.ListExportersRequest(  # type: ignore[call-arg]
                    parent=f"namespaces/{self.namespace}",
                    page_size=page_size,  # ty: ignore[invalid-argument-type]
                    page_token=page_token,  # ty: ignore[invalid-argument-type]
                    filter=filter,  # ty: ignore[invalid-argument-type]
                    show_hidden_labels=show_hidden_labels,
                )
            )
        return ExporterList.from_protobuf(exporters)

    async def GetLease(self, *, name: str, timeout: float | None = None):
        with translate_grpc_exceptions():
            lease = await self.stub.GetLease(
                client_pb2.GetLeaseRequest(
                    name=f"namespaces/{self.namespace}/leases/{name}",
                ),
                timeout=timeout,
            )
        return Lease.from_protobuf(lease)

    async def ListLeases(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
        only_active: bool = True,
        tag_filter: str | None = None,
    ):
        with translate_grpc_exceptions():
            leases = await self.stub.ListLeases(
                client_pb2.ListLeasesRequest(  # type: ignore[call-arg]
                    parent=f"namespaces/{self.namespace}",
                    page_size=page_size,  # ty: ignore[invalid-argument-type]
                    page_token=page_token,  # ty: ignore[invalid-argument-type]
                    filter=extract_match_labels_filter(filter),  # ty: ignore[invalid-argument-type]
                    only_active=only_active,
                    tag_filter=tag_filter or "",
                )
            )
        return LeaseList.from_protobuf(leases)

    async def GetLeaseTemplate(self, *, name: str, timeout: float | None = None):
        with translate_grpc_exceptions():
            try:
                template = await self.stub.GetLeaseTemplate(
                    client_pb2.GetLeaseTemplateRequest(
                        name=f"namespaces/{self.namespace}/leasetemplates/{name}",
                    ),
                    timeout=timeout,
                )
            except AioRpcError as e:
                _raise_for_template_rpc(e, name)
        return LeaseTemplate.from_protobuf(template)

    async def ListLeaseTemplates(
        self,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ):
        with translate_grpc_exceptions():
            try:
                templates = await self.stub.ListLeaseTemplates(
                    client_pb2.ListLeaseTemplatesRequest(  # type: ignore[call-arg]
                        parent=f"namespaces/{self.namespace}",
                        page_size=page_size,  # ty: ignore[invalid-argument-type]
                        page_token=page_token,  # ty: ignore[invalid-argument-type]
                    )
                )
            except AioRpcError as e:
                _raise_for_template_rpc(e)
        return LeaseTemplateList.from_protobuf(templates)

    async def CreateLease(  # noqa: C901
        self,
        *,
        selector: str | None = None,
        duration: timedelta | None,
        exporter_name: str | None = None,
        begin_time: datetime | None = None,
        lease_id: str | None = None,
        tags: dict[str, str] | None = None,
        allow_disabled: bool = False,
        context: dict[str, str] | None = None,
        shared_with: list[str] | None = None,
        template: str | None = None,
        excluded_members: list[str] | None = None,
        member_selectors: dict[str, str] | None = None,
        members: list[LeaseMember] | None = None,
        constraints: list[LeaseConstraint] | None = None,
        forwards: list[LeaseForward] | None = None,
    ):
        """Create a lease in one of the three request forms.

        Scalar: `selector`, `exporter_name`, or both. Template: `template`, with
        optional `excluded_members` and `member_selectors`. Members: `members`,
        with optional `constraints` and `forwards`. `duration` can be None only
        for a template that has a default duration.

        Only the members form takes `forwards`: a template has its own. A
        controller that predates forwards creates the lease without them. This
        method then deletes that lease and raises `ControllerTooOldError`.
        """
        scalar = bool(selector or exporter_name)
        if sum((scalar, template is not None, bool(members))) > 1:
            raise ValueError("a lease request sets only one of selector/exporter_name, template, or members")
        if template is None and (excluded_members or member_selectors):
            raise ValueError("excluded_members and member_selectors need a template")
        if not members and constraints:
            raise ValueError("constraints need members")
        if forwards:
            if template is not None:
                raise ValueError("forwards cannot be used with a template: the template has the forwards")
            if not members:
                raise ValueError("forwards need members: a forward connects the members of the request")
            validate_forwards(forwards, [member.name for member in members])
        if duration is None and template is None:
            raise ValueError("duration is required")
        member_form = template is not None or bool(members)

        lease_pb = client_pb2.Lease(
            selector=selector or "",
            exporter_name=exporter_name or "",
            allow_disabled=allow_disabled,
        )
        if duration is not None:
            lease_pb.duration.FromTimedelta(duration)

        if template is not None:
            lease_pb.template.template = template
            lease_pb.template.excluded_members.extend(excluded_members or [])
            for role, role_selector in (member_selectors or {}).items():
                lease_pb.template.member_selectors[role] = role_selector
        for member in members or []:
            lease_pb.members.append(member.to_protobuf())
        for constraint in constraints or []:
            lease_pb.constraints.append(constraint.to_protobuf())
        for forward in forwards or []:
            lease_pb.forwards.append(forward.to_protobuf())

        if tags:
            for k, v in tags.items():
                lease_pb.tags[k] = v

        if context:
            for k, v in context.items():
                lease_pb.context[k] = v

        if shared_with:
            lease_pb.shared_with.extend(shared_with)

        if begin_time:
            timestamp_pb = timestamp_pb2.Timestamp()
            timestamp_pb.FromDatetime(begin_time)
            lease_pb.begin_time.CopyFrom(timestamp_pb)

        with translate_grpc_exceptions():
            try:
                lease = await self.stub.CreateLease(
                    client_pb2.CreateLeaseRequest(
                        parent=f"namespaces/{self.namespace}",
                        lease=lease_pb,
                        lease_id=lease_id or "",
                    )
                )
            except AioRpcError as e:
                if not member_form:
                    raise
                _raise_for_member_form_create(e)
        created = Lease.from_protobuf(lease)
        if forwards and not created.forwards:
            # A controller without forwards ignores them. Only a members request
            # sends forwards, so a template request never gets here.
            await self._delete_lease_without_forwards(created.name)
            raise ControllerTooOldError(_TOO_OLD_FORWARDS)
        return created

    async def _delete_lease_without_forwards(self, name: str) -> None:
        """Remove the lease that a controller without forwards made from a request with forwards."""
        try:
            await self.DeleteLease(name=name, timeout=_DELETE_UNUSABLE_LEASE_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Could not delete lease %s, which the controller created without its forwards: %s. "
                "Delete it with 'jmp delete leases %s'",
                name,
                e,
                name,
            )

    async def UpdateLease(
        self,
        *,
        name: str,
        duration: timedelta | None = None,
        begin_time: datetime | None = None,
        client: str | None = None,
        add_shared_with: list[str] | None = None,
        remove_shared_with: list[str] | None = None,
    ):
        lease_pb = client_pb2.Lease(
            name=f"namespaces/{self.namespace}/leases/{name}",
        )

        update_fields = []

        if duration is not None:
            duration_pb = duration_pb2.Duration()
            duration_pb.FromTimedelta(duration)
            lease_pb.duration.CopyFrom(duration_pb)
            update_fields.append("duration")

        if begin_time is not None:
            timestamp_pb = timestamp_pb2.Timestamp()
            timestamp_pb.FromDatetime(begin_time)
            lease_pb.begin_time.CopyFrom(timestamp_pb)
            update_fields.append("begin_time")

        if client is not None:
            lease_pb.client = client
            update_fields.append("client")

        has_share_changes = bool(add_shared_with) or bool(remove_shared_with)

        if not update_fields and not has_share_changes:
            raise ValueError(
                "At least one of duration, begin_time, client, add_shared_with, or remove_shared_with must be provided"
            )

        update_mask = field_mask_pb2.FieldMask()
        if update_fields:
            update_mask.FromJsonString(",".join(update_fields))

        req = client_pb2.UpdateLeaseRequest(
            lease=lease_pb,
            update_mask=update_mask,
        )
        if add_shared_with:
            req.add_shared_with.extend(add_shared_with)
        if remove_shared_with:
            req.remove_shared_with.extend(remove_shared_with)

        with translate_grpc_exceptions():
            lease = await self.stub.UpdateLease(req)
        return Lease.from_protobuf(lease)

    async def DeleteLease(self, *, name: str, timeout: float | None = None):
        with translate_grpc_exceptions():
            await self.stub.DeleteLease(
                client_pb2.DeleteLeaseRequest(
                    name=f"namespaces/{self.namespace}/leases/{name}",
                ),
                timeout=timeout,
            )

    async def RotateToken(self) -> str:
        with translate_grpc_exceptions():
            response = await self.stub.RotateToken(
                client_pb2.RotateTokenRequest(
                    parent=f"namespaces/{self.namespace}",
                )
            )
        return response.token


@dataclass(frozen=True, slots=True)
class MultipathExporterStub:
    """
    Multipath ExporterServiceStub

    Connecting to exporter service using multiple channels.
    All channels are tried in sequence, and the first one ready
    is used, prioritizing channels in the front.
    """

    channels: InitVar[list[Channel]]

    __stubs: dict[Channel, SimpleNamespace] = field(init=False, default_factory=OrderedDict)

    def __post_init__(self, channels):
        for channel in channels:
            stub = SimpleNamespace()
            jumpstarter_pb2_grpc.ExporterServiceStub.__init__(stub, channel)  # type: ignore[arg-type]  # ty: ignore[too-many-positional-arguments]
            router_pb2_grpc.RouterServiceStub.__init__(stub, channel)  # type: ignore[arg-type]  # ty: ignore[too-many-positional-arguments]
            self.__stubs[channel] = stub

    def __getattr__(self, name):
        for channel, stub in self.__stubs.items():
            # find the first channel that's ready
            if channel.get_state(try_to_connect=True) == ChannelConnectivity.READY:
                return getattr(stub, name)
        # or fallback to the last channel (via router)
        return getattr(next(reversed(self.__stubs.values())), name)
