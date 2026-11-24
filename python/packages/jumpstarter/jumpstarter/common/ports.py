"""Driver ports (JEP-0016).

A port is a named connection point on a driver. A forward of a lease connects
a port of one exporter to a port of a different exporter. The exporter reports
the name, the direction and the properties of a port. The local addresses
(``listen`` and ``connect``) stay in the exporter configuration.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Self

from jumpstarter_protocol.jumpstarter.v1 import common_pb2
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

# A DNS label, as a role name of JEP-0015.
_PORT_NAME = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_PORT_NAME_MAX = 63


class PortDirection(StrEnum):
    PROVIDES = "provides"
    """A local service listens. The exporter connects to it for each forwarded connection."""
    REQUIRES = "requires"
    """A local driver uses a service of a different member. The exporter opens a local listener for it."""
    CONNECTS = "connects"
    """The port connects to a port of its own type. No end is the service."""

    def to_proto(self) -> common_pb2.PortDirection.ValueType:
        return _DIRECTION_TO_PROTO[self]

    @classmethod
    def from_proto(cls, value: int) -> Self:
        for direction, proto in _DIRECTION_TO_PROTO.items():
            if proto == value:
                return cls(direction)
        raise ValueError(f"port direction {value} is not a direction")


class PortAttachment(StrEnum):
    SOCKET = "socket"
    WIRED = "wired"
    WIRELESS = "wireless"


class PortTransport(StrEnum):
    STREAM = "stream"
    DATAGRAM = "datagram"
    FRAME = "frame"


_DIRECTION_TO_PROTO = {
    PortDirection.PROVIDES: common_pb2.PORT_DIRECTION_PROVIDES,
    PortDirection.REQUIRES: common_pb2.PORT_DIRECTION_REQUIRES,
    PortDirection.CONNECTS: common_pb2.PORT_DIRECTION_CONNECTS,
}
_ATTACHMENT_TO_PROTO = {
    PortAttachment.SOCKET: common_pb2.PORT_ATTACHMENT_SOCKET,
    PortAttachment.WIRED: common_pb2.PORT_ATTACHMENT_WIRED,
    PortAttachment.WIRELESS: common_pb2.PORT_ATTACHMENT_WIRELESS,
}
_TRANSPORT_TO_PROTO = {
    PortTransport.STREAM: common_pb2.PORT_TRANSPORT_STREAM,
    PortTransport.DATAGRAM: common_pb2.PORT_TRANSPORT_DATAGRAM,
    PortTransport.FRAME: common_pb2.PORT_TRANSPORT_FRAME,
}


def parse_port_address(address: str) -> tuple[str, int]:
    """Split ``host:port`` (or ``[v6-host]:port``) into the host and the TCP port number."""
    host, sep, port = address.rpartition(":")
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not sep or not host or not port.isascii() or not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"'{address}' is not a host:port address")
    return host, int(port)


def validate_port_name(name: str) -> str:
    if len(name) > _PORT_NAME_MAX or not _PORT_NAME.fullmatch(name):
        raise ValueError(
            f"port name '{name}' is not a DNS label (lowercase letters, digits and '-', "
            f"a maximum of {_PORT_NAME_MAX} characters)"
        )
    return name


class Port(BaseModel):
    """One port of a driver, as the exporter configuration or the driver declares it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    direction: PortDirection
    protocol: str | None = None
    """Free-text protocol tag. The controller compares it only if the two ends of a forward set it."""
    attachment: PortAttachment = PortAttachment.SOCKET
    transport: PortTransport = PortTransport.STREAM
    listen: str | None = None
    """Local ``host:port`` where the exporter opens a listener for a local client. Not reported."""
    connect: str | None = None
    """Local ``host:port`` of a local service that the exporter connects to. Not reported."""

    @field_validator("name")
    @classmethod
    def _valid_name(cls, name: str) -> str:
        return validate_port_name(name)

    @field_validator("protocol")
    @classmethod
    def _valid_protocol(cls, protocol: str | None) -> str | None:
        if protocol is not None and not 0 < len(protocol) <= _PORT_NAME_MAX:
            raise ValueError(f"a protocol tag has 1 to {_PORT_NAME_MAX} characters")
        return protocol

    @field_validator("listen", "connect")
    @classmethod
    def _valid_address(cls, address: str | None) -> str | None:
        if address is not None:
            parse_port_address(address)
        return address

    @model_validator(mode="after")
    def _valid_addresses(self) -> Self:
        match self.direction:
            case PortDirection.PROVIDES:
                if self.listen is not None:
                    raise ValueError(f"port '{self.name}': a provides port has 'connect', not 'listen'")
            case PortDirection.REQUIRES:
                if self.connect is not None:
                    raise ValueError(f"port '{self.name}': a requires port has 'listen', not 'connect'")
                if self.listen is None and self.forwardable:
                    raise ValueError(f"port '{self.name}': a requires port must have 'listen'")
            case PortDirection.CONNECTS:
                if self.listen is not None and self.connect is not None:
                    raise ValueError(f"port '{self.name}': a connects port has 'listen' or 'connect', not the two")
        return self

    @property
    def forwardable(self) -> bool:
        """A forward accepts only socket ports with the stream transport (DD-8, DD-9)."""
        return self.attachment is PortAttachment.SOCKET and self.transport is PortTransport.STREAM

    @property
    def listen_address(self) -> tuple[str, int] | None:
        return parse_port_address(self.listen) if self.listen is not None else None

    @property
    def connect_address(self) -> tuple[str, int] | None:
        return parse_port_address(self.connect) if self.connect is not None else None

    def to_report(self) -> common_pb2.PortReport:
        """The report of the port. It does not contain the local addresses."""
        return common_pb2.PortReport(
            name=self.name,
            direction=self.direction.to_proto(),
            protocol=self.protocol,
            attachment=_ATTACHMENT_TO_PROTO[self.attachment],
            transport=_TRANSPORT_TO_PROTO[self.transport],
        )


def coerce_ports(ports: Any) -> list[Port]:
    """Validate a ``ports`` value that can contain ``Port`` objects or plain mappings."""
    return [port if isinstance(port, Port) else Port.model_validate(port) for port in ports or []]


def duplicate_port_names(ports: list[Port]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for port in ports:
        if port.name in seen and port.name not in duplicates:
            duplicates.append(port.name)
        seen.add(port.name)
    return duplicates
