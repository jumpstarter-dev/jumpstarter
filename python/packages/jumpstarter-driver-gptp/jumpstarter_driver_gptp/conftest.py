from __future__ import annotations

import pytest

from .common import VALID_PORT_TRANSITIONS
from .driver import MockGptp, MockGptpBackend
from jumpstarter.common.utils import serve


class PtpNotStartedError(RuntimeError):
    pass


class PtpAlreadyRunningError(RuntimeError):
    pass


class PtpStateError(RuntimeError):
    pass


class StatefulPtp4l(MockGptpBackend):
    """Drop-in replacement for MockGptpBackend that enforces
    IEEE 802.1AS port state rules.

    Tracks:
    - Process lifecycle (started/stopped)
    - Port state machine: INITIALIZING -> LISTENING -> {MASTER, SLAVE, PASSIVE} -> FAULTY
    - Servo state: s0 (unlocked) -> s1 (calibrating) -> s2 (locked)
    - Sync offset convergence (simulated)
    - Priority1 changes and BMCA re-evaluation
    """

    def __init__(self):
        super().__init__()
        self._call_log: list[str] = []

    def require_started(self):
        if not self._started:
            raise PtpNotStartedError("ptp4l not started -- call start() first")

    def start(self):
        if self._started:
            raise PtpAlreadyRunningError("ptp4l already running")
        self._started = True
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._offset_ns = 999_999.0
        self._priority1 = 128
        self._transition_to("LISTENING")
        self._call_log.append("start")

    def stop(self):
        self.require_started()
        self._started = False
        self._port_state = "INITIALIZING"
        self._servo_state = "s0"
        self._call_log.append("stop")

    def _transition_to(self, new_state: str):
        valid = VALID_PORT_TRANSITIONS.get(self._port_state, set())
        if new_state not in valid:
            raise PtpStateError(
                f"Invalid transition: {self._port_state} -> {new_state}"
            )
        self._port_state = new_state

    def simulate_sync_convergence(self):
        """Simulate the typical LISTENING -> SLAVE -> servo lock sequence."""
        self.require_started()
        if self._port_state == "LISTENING":
            self._transition_to("SLAVE")
        self._servo_state = "s1"
        self._offset_ns = 50_000.0
        self._servo_state = "s2"
        self._offset_ns = -23.0

    def simulate_fault(self):
        self.require_started()
        self._transition_to("FAULTY")
        self._servo_state = "s0"

    def simulate_recovery_from_fault(self):
        self.require_started()
        if self._port_state != "FAULTY":
            raise PtpStateError(
                f"Operation requires state FAULTY, current: {self._port_state}"
            )
        self._transition_to("LISTENING")
        self._transition_to("SLAVE")
        self._servo_state = "s1"

    def set_priority1(self, value: int):
        self.require_started()
        self._priority1 = value
        if value < 128 and self._port_state in ("SLAVE", "LISTENING", "PASSIVE"):
            if self._port_state != "MASTER":
                self._transition_to("MASTER")
        self._call_log.append(f"set_priority1({value})")


@pytest.fixture
def stateful_ptp4l():
    return StatefulPtp4l()


@pytest.fixture
def stateful_client(stateful_ptp4l):
    """Create a MockGptp driver backed by StatefulPtp4l and serve it.

    The MockGptp @export methods remain intact and delegate to
    the stateful backend, so gRPC routing works correctly.
    """
    driver = MockGptp(backend=stateful_ptp4l)
    with serve(driver) as client:
        yield client, stateful_ptp4l
