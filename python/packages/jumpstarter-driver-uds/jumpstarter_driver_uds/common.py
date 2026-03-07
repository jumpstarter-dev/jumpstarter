from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class UdsSessionType(str, Enum):
    """UDS diagnostic session types (ISO-14229)."""

    DEFAULT = "default"
    PROGRAMMING = "programming"
    EXTENDED = "extended"
    SAFETY = "safety"


class UdsResetType(str, Enum):
    """UDS ECU reset types (ISO-14229)."""

    HARD = "hard"
    KEY_OFF_ON = "key_off_on"
    SOFT = "soft"


class UdsResponse(BaseModel):
    """Generic UDS service response."""

    service: str
    success: bool
    data: str | None = None
    nrc: int | None = None
    nrc_name: str | None = None


class DidValue(BaseModel):
    """A single DID (Data Identifier) value."""

    did: int
    value: str | int | float | None


class DtcInfo(BaseModel):
    """A single DTC (Diagnostic Trouble Code) entry."""

    dtc_id: int
    status: int
    severity: int | None = None


class SecuritySeedResponse(BaseModel):
    """Response from a SecurityAccess seed request."""

    seed: str
    success: bool = True
    nrc: int | None = None
    nrc_name: str | None = None
