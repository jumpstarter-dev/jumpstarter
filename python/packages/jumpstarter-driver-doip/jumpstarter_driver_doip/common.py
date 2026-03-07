from __future__ import annotations

import re

from pydantic import BaseModel, field_validator


class EntityStatusResponse(BaseModel):
    """DoIP entity status response."""

    node_type: int
    max_open_sockets: int
    currently_open_sockets: int
    max_data_size: int | None = None


class AliveCheckResponse(BaseModel):
    """DoIP alive check response."""

    source_address: int


class DiagnosticPowerModeResponse(BaseModel):
    """DoIP diagnostic power mode response."""

    diagnostic_power_mode: int


class RoutingActivationResponse(BaseModel):
    """DoIP routing activation response."""

    client_logical_address: int
    logical_address: int
    response_code: int
    vm_specific: int | None = None


class VehicleIdentificationResponse(BaseModel):
    """DoIP vehicle identification response."""

    vin: str
    logical_address: int
    eid: str
    gid: str
    further_action: int
    sync_status: int | None = None


class DiagnosticPayload(BaseModel):
    """Wrapper for raw diagnostic payload bytes for gRPC serialization.

    The ``data`` field is a hex-encoded string representation of the raw bytes,
    which is safe for JSON/protobuf transport.
    """

    data: str

    @field_validator("data")
    @classmethod
    def _validate_hex(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]*", v):
            raise ValueError(f"data must be a hex string, got {v!r}")
        return v
