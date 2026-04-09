from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel
from udsoncan import DidCodec
from udsoncan.configs import default_client_config


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


class RoutineControlResponse(BaseModel):
    """Response from a RoutineControl request."""

    routine_id: int
    control_type: str
    success: bool
    status_record: str | None = None
    nrc: int | None = None
    nrc_name: str | None = None


class AuthenticationResponse(BaseModel):
    """Response from an Authentication request."""

    authentication_task: int
    return_value: int
    success: bool
    challenge_server: str | None = None
    certificate_server: str | None = None
    proof_of_ownership_server: str | None = None
    session_key_info: str | None = None
    algorithm_indicator: str | None = None
    needed_additional_parameter: str | None = None
    nrc: int | None = None
    nrc_name: str | None = None


class FileTransferResponse(BaseModel):
    """Response from a RequestFileTransfer request."""

    moop: int
    success: bool
    max_length: int | None = None
    filesize_uncompressed: int | None = None
    filesize_compressed: int | None = None
    dirinfo_length: int | None = None
    nrc: int | None = None
    nrc_name: str | None = None


class RawDidCodec(DidCodec):
    """Pass-through codec that treats DID values as raw bytes.

    Used as the default codec so that any DID can be read/written without
    requiring per-DID configuration in the udsoncan client.
    """

    def encode(self, value: Any) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        if isinstance(value, str):
            return value.encode("utf-8")
        raise TypeError(f"Cannot encode {type(value)} as DID payload")

    def decode(self, payload: bytes) -> bytes:
        return payload

    def __len__(self) -> int:
        raise DidCodec.ReadAllRemainingData()


def make_uds_client_config(request_timeout: float = 5.0) -> dict:
    """Build a udsoncan client config with a raw default DID codec."""
    config = dict(default_client_config)
    config["data_identifiers"] = {"default": RawDidCodec()}
    config["request_timeout"] = request_timeout
    return config
