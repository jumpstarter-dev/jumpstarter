"""Pydantic models and enums for gPTP/PTP time synchronization."""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, field_validator


class PortState(str, Enum):
    """IEEE 802.1AS / IEEE 1588 port state machine states."""

    INITIALIZING = "INITIALIZING"
    LISTENING = "LISTENING"
    MASTER = "MASTER"
    SLAVE = "SLAVE"
    PASSIVE = "PASSIVE"
    FAULTY = "FAULTY"
    UNCALIBRATED = "UNCALIBRATED"


class ServoState(str, Enum):
    """PTP clock servo synchronization states.

    - ``s0``: unlocked — no synchronization yet.
    - ``s1``: calibrating — frequency adjustment in progress.
    - ``s2``: locked — fully synchronized.
    """

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
"""Valid IEEE 802.1AS port state transitions, keyed by current state."""


class GptpStatus(BaseModel):
    """Current PTP synchronization status snapshot.

    Attributes:
        port_state: Current port state machine state.
        clock_class: PTP clock class (default 248 = slave-only).
        clock_accuracy: PTP clock accuracy enumeration.
        offset_ns: Current offset from master in nanoseconds.
        mean_delay_ns: Mean path delay in nanoseconds.
        gm_identity: Grandmaster clock identity string.
        servo_state: Current servo synchronization state.
    """

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
        """Accept both string and enum values for port_state."""
        if isinstance(v, str):
            return PortState(v)
        return v


class GptpOffset(BaseModel):
    """Clock offset measurement from master.

    Attributes:
        offset_from_master_ns: Clock offset from master in nanoseconds.
        mean_path_delay_ns: Mean path delay in nanoseconds.
        freq_ppb: Frequency adjustment in parts per billion.
        timestamp: Unix timestamp of the measurement.
    """

    offset_from_master_ns: float
    mean_path_delay_ns: float
    freq_ppb: float = 0.0
    timestamp: float = 0.0


class GptpSyncEvent(BaseModel):
    """A single sync status update from ptp4l.

    Attributes:
        event_type: Type of event — ``"sync"``, ``"state_change"``, or ``"fault"``.
        port_state: Current port state (if known).
        servo_state: Current servo state (if known).
        offset_ns: Current offset in nanoseconds.
        path_delay_ns: Current path delay in nanoseconds.
        freq_ppb: Current frequency adjustment in ppb.
        timestamp: Unix timestamp of the event.
    """

    event_type: Literal["sync", "state_change", "fault"]
    port_state: Optional[PortState] = None
    servo_state: Optional[ServoState] = None
    offset_ns: Optional[float] = None
    path_delay_ns: Optional[float] = None
    freq_ppb: Optional[float] = None
    timestamp: float = 0.0


class GptpPortStats(BaseModel):
    """PTP port-level statistics counters.

    Attributes:
        sync_count: Number of sync messages processed.
        followup_count: Number of follow-up messages processed.
        pdelay_req_count: Number of pdelay request messages sent.
        pdelay_resp_count: Number of pdelay response messages received.
        announce_count: Number of announce messages processed.
    """

    sync_count: int = 0
    followup_count: int = 0
    pdelay_req_count: int = 0
    pdelay_resp_count: int = 0
    announce_count: int = 0


class GptpParentInfo(BaseModel):
    """Information about the parent/grandmaster clock.

    Attributes:
        parent_clock_identity: Parent clock identity string.
        grandmaster_identity: Grandmaster clock identity string.
        grandmaster_priority1: Grandmaster priority1 value.
        grandmaster_priority2: Grandmaster priority2 value.
        grandmaster_clock_class: Grandmaster clock class.
        grandmaster_clock_accuracy: Grandmaster clock accuracy.
    """

    parent_clock_identity: str = ""
    grandmaster_identity: str = ""
    grandmaster_priority1: int = 128
    grandmaster_priority2: int = 128
    grandmaster_clock_class: int = 248
    grandmaster_clock_accuracy: int = 0xFE
