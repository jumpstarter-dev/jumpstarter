"""Resource handles for the driver byte-stream path — a stdlib (pydantic-free)
reimplementation of the former ``pydantic`` discriminated-union models.

A *resource handle* is a small tagged record that tells the exporter where a
driver's byte stream comes from: a client-side memory stream (``client_stream``)
or a presigned HTTP request (``presigned_request``). It is serialized to JSON,
carried in stream metadata, and parsed back via :func:`parse_resource`, which
dispatches on the ``kind`` discriminator.

The classes keep the small slice of the pydantic API the callers actually use —
keyword construction, ``model_dump(mode=...)``, ``model_dump_json()`` and
positional pattern matching (``case ClientStreamResource(uuid, encoding)``) — so
the driver/client code is unchanged while the dependency on pydantic is dropped.
"""

from __future__ import annotations

import json
from typing import Union
from uuid import UUID

__all__ = [
    "ClientStreamResource",
    "PresignedRequestResource",
    "Resource",
    "ResourceMetadata",
    "parse_resource",
]


class ClientStreamResource:
    """A byte stream served from a client-side memory pipe, addressed by ``uuid``."""

    kind = "client_stream"
    __match_args__ = ("uuid", "x_jmp_content_encoding")

    def __init__(self, *, uuid, x_jmp_content_encoding: str | None = None, kind: str | None = None):
        # pydantic coerced str→UUID; preserve that so handles round-trip from JSON.
        self.uuid: UUID = uuid if isinstance(uuid, UUID) else UUID(str(uuid))
        self.x_jmp_content_encoding = x_jmp_content_encoding

    def model_dump(self, mode: str = "python") -> dict:
        return {
            "kind": self.kind,
            "uuid": str(self.uuid) if mode == "json" else self.uuid,
            "x_jmp_content_encoding": self.x_jmp_content_encoding,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"))


class PresignedRequestResource:
    """A byte stream served by issuing a presigned HTTP ``GET``/``PUT`` request."""

    kind = "presigned_request"
    __match_args__ = ("headers", "url", "method")

    def __init__(self, *, headers: dict[str, str], url: str, method: str, kind: str | None = None):
        self.headers = headers
        self.url = url
        self.method = method

    def model_dump(self, mode: str = "python") -> dict:
        return {
            "kind": self.kind,
            "headers": self.headers,
            "url": self.url,
            "method": self.method,
        }

    def model_dump_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"))


Resource = Union[ClientStreamResource, PresignedRequestResource]

_RESOURCE_TYPES: dict[str, type] = {
    ClientStreamResource.kind: ClientStreamResource,
    PresignedRequestResource.kind: PresignedRequestResource,
}


def parse_resource(data) -> Resource:
    """Parse a resource handle into its concrete type, dispatching on ``kind``.

    Accepts an already-parsed :data:`Resource` instance (returned as-is), a JSON
    string/bytes, or a mapping (e.g. the decoded driver-call argument). Replaces
    the former ``pydantic.TypeAdapter(Resource).validate_python``.
    """
    if isinstance(data, (ClientStreamResource, PresignedRequestResource)):
        return data
    if isinstance(data, (str, bytes, bytearray)):
        data = json.loads(data)
    kind = data.get("kind")
    cls = _RESOURCE_TYPES.get(kind)
    if cls is None:
        raise ValueError(f"unknown resource kind: {kind!r}")
    return cls(**{k: v for k, v in data.items() if k != "kind"})


class ResourceMetadata:
    """Stream metadata: a (JSON-encoded) resource handle plus the negotiated
    accept-encoding. Constructed from the decoded ``initial_metadata`` mapping,
    where ``resource`` is the JSON string the exporter emitted; extra metadata
    keys are ignored (matching pydantic's default ``extra='ignore'``)."""

    def __init__(self, *, resource, x_jmp_accept_encoding: str | None = None, **_extra):
        self.resource: Resource = parse_resource(resource)
        self.x_jmp_accept_encoding = x_jmp_accept_encoding
