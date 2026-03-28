from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


class SomeIpPayload(BaseModel):
    """Hex-encoded SOME/IP payload for safe gRPC transport."""

    data: str

    @field_validator("data")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]*", v):
            raise ValueError(f"data must be a hex string, got {v!r}")
        return v


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
    payload: str  # hex-encoded


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
    payload: str  # hex-encoded
