from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_HEX_RE = re.compile(r"[0-9a-fA-F]*")


def _validate_hex_string(v: str) -> str:
    if not _HEX_RE.fullmatch(v):
        raise ValueError(f"payload must be a hex string, got {v!r}")
    return v


class SomeIpPayload(BaseModel):
    """Hex-encoded SOME/IP payload for safe gRPC transport."""

    data: str

    @field_validator("data")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        return _validate_hex_string(v)


class SomeIpMessageResponse(BaseModel):
    """A received SOME/IP message."""

    service_id: int
    method_id: int
    client_id: int
    session_id: int
    protocol_version: int = 1
    interface_version: int = 1
    message_type: int
    return_code: int
    payload: str

    @field_validator("payload")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        return _validate_hex_string(v)


class SomeIpServiceEntry(BaseModel):
    """A SOME/IP service instance (for SD results)."""

    service_id: int
    instance_id: int
    major_version: int = 1
    minor_version: int = 0


class SomeIpEventNotification(BaseModel):
    """A SOME/IP event notification."""

    service_id: int
    event_id: int
    payload: str

    @field_validator("payload")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        return _validate_hex_string(v)
