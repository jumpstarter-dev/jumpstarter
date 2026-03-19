from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel


class XcpTransport(str, Enum):
    """XCP transport layer type."""

    ETH = "ETH"
    CAN = "CAN"
    USB = "USB"
    SXI = "SXI"


class XcpEthProtocol(str, Enum):
    """Ethernet protocol for XCP over Ethernet transport."""

    TCP = "TCP"
    UDP = "UDP"


class XcpConnectionInfo(BaseModel):
    """Information returned after a successful XCP CONNECT."""

    max_cto: int
    max_dto: int
    byte_order: str
    supports_pgm: bool = False
    supports_stim: bool = False
    supports_daq: bool = False
    supports_calpag: bool = False
    protocol_layer_version: int = 0
    transport_layer_version: int = 0
    address_granularity: str = ""
    slave_block_mode: bool = False


class XcpStatusResponse(BaseModel):
    """Response from GET_STATUS command."""

    session_status: dict[str, Any] = {}
    resource_protection: dict[str, bool] = {}


class XcpDaqInfo(BaseModel):
    """DAQ configuration information."""

    processor: dict[str, Any] = {}
    resolution: dict[str, Any] = {}
    channels: list[dict[str, Any]] = []


class XcpProgramInfo(BaseModel):
    """Information returned by PROGRAM_START."""

    comm_mode_pgm: int = 0
    max_cto_pgm: int = 0
    max_bs_pgm: int = 0
    min_st_pgm: int = 0
    queue_size_pgm: int = 0


class XcpChecksum(BaseModel):
    """Result of BUILD_CHECKSUM command."""

    checksum_type: int
    checksum_value: int


class XcpIdentifier(BaseModel):
    """Slave identifier result."""

    id_type: int
    identifier: str
