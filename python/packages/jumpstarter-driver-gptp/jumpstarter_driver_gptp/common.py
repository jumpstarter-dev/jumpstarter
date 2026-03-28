from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class PortState(str, Enum):
    INITIALIZING = "INITIALIZING"
    LISTENING = "LISTENING"
    MASTER = "MASTER"
    SLAVE = "SLAVE"
    PASSIVE = "PASSIVE"
    FAULTY = "FAULTY"
    UNCALIBRATED = "UNCALIBRATED"


class ServoState(str, Enum):
    UNLOCKED = "s0"
    CALIBRATING = "s1"
    LOCKED = "s2"


VALID_PORT_TRANSITIONS: dict[str, set[str]] = {
    "INITIALIZING": {"LISTENING", "FAULTY"},
    "LISTENING": {"MASTER", "SLAVE", "PASSIVE", "FAULTY"},
    "MASTER": {"LISTENING", "SLAVE", "PASSIVE", "FAULTY"},
    "SLAVE": {"LISTENING", "MASTER", "PASSIVE", "FAULTY", "UNCALIBRATED"},
    "PASSIVE": {"LISTENING", "MASTER", "SLAVE", "FAULTY"},
    "FAULTY": {"INITIALIZING", "LISTENING"},
    "UNCALIBRATED": {"SLAVE", "FAULTY", "LISTENING"},
}


class GptpStatus(BaseModel):
    """Current PTP synchronization status."""

    port_state: PortState
    clock_class: int = 248
    clock_accuracy: int = 0xFE
    offset_ns: float = 0.0
    mean_delay_ns: float = 0.0
    gm_identity: str = ""
    servo_state: ServoState = ServoState.UNLOCKED

    @field_validator("port_state", mode="before")
    @classmethod
    def _coerce_port_state(cls, v: str | PortState) -> PortState:
        if isinstance(v, str):
            return PortState(v)
        return v


class GptpOffset(BaseModel):
    """Clock offset measurement from master."""

    offset_from_master_ns: float
    mean_path_delay_ns: float
    freq_ppb: float = 0.0
    timestamp: float = 0.0


class GptpSyncEvent(BaseModel):
    """A single sync status update from ptp4l."""

    event_type: Literal["sync", "state_change", "fault"]
    port_state: Optional[PortState] = None
    servo_state: Optional[ServoState] = None
    offset_ns: Optional[float] = None
    path_delay_ns: Optional[float] = None
    freq_ppb: Optional[float] = None
    timestamp: float = 0.0


class GptpPortStats(BaseModel):
    """PTP port-level statistics."""

    sync_count: int = 0
    followup_count: int = 0
    pdelay_req_count: int = 0
    pdelay_resp_count: int = 0
    announce_count: int = 0


class GptpParentInfo(BaseModel):
    """Information about the parent/grandmaster clock."""

    parent_clock_identity: str = ""
    grandmaster_identity: str = ""
    grandmaster_priority1: int = 128
    grandmaster_priority2: int = 128
    grandmaster_clock_class: int = 248
    grandmaster_clock_accuracy: int = 0xFE
