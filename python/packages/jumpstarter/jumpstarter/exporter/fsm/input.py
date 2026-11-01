"""Immutable exporter observations/requests and the coalescing input buffer."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from types import MappingProxyType

from anyio import Event
from anyio.lowlevel import checkpoint
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.fsm import State as FSMState

logger = logging.getLogger("jumpstarter.exporter.fsm")


@dataclass(frozen=True)
class LeaseKey:
    """Local identity for one admitted lease assignment: name plus publisher generation."""

    name: str
    generation: int

    def __post_init__(self) -> None:
        if not self.name or self.generation < 1:
            raise ValueError("An assigned lease needs a non-empty name and a positive generation")


@dataclass(frozen=True)
class LeaseAssignment:
    """Latest controller (or standalone) lease-assignment observation."""

    key: LeaseKey
    client_name: str = ""
    context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


class ShutdownMode(IntEnum):
    """Escalating shutdown intent; stronger modes take precedence when merged."""

    DRAIN = 1
    STOP_NOW = 2


@dataclass(frozen=True)
class ShutdownRequest:
    """Persistent shutdown intent carried in every input snapshot once requested."""

    mode: ShutdownMode
    exit_code: int | None = None
    reason: str = ""
    should_unregister: bool = False


@dataclass(frozen=True)
class Inputs:
    """Immutable observations and requests for one state evaluation."""

    lease_assignment: LeaseAssignment | None = None
    shutdown: ShutdownRequest | None = None


type State = FSMState[Inputs]


class InputBuffer:
    """Latest lease assignment, persistent shutdown intent, and a coalescing runner wakeup."""

    def __init__(self) -> None:
        self._snapshot = Inputs()
        self._changed = Event()
        self._generation = 0

    def snapshot(self) -> Inputs:
        """Latest inputs for one-off reads; the runner's watch event is not consumed."""
        return self._snapshot

    def snapshot_and_watch(self) -> tuple[Inputs, Event]:
        return self._snapshot, self._changed

    def wake(self, reason: str = "lease-local input") -> None:
        """Notify after updating lease-local inputs, even if the snapshot is unchanged."""
        changed = self._changed
        self._changed = Event()
        changed.set()
        logger.debug("FSM wakeup: %s", reason)

    def publish_lease_assignment(
        self,
        lease_name: str | None,
        client_name: str = "",
        context: Mapping[str, str] | None = None,
    ) -> None:
        """Publish a lease-assignment observation; None means unleased, not a stream interruption."""
        if lease_name == "":
            raise ValueError("An assigned lease needs a non-empty name; use None for unleased")
        previous = self._snapshot.lease_assignment
        previous_name = previous.key.name if previous is not None else None
        generation = self._generation + (previous_name != lease_name)
        lease_assignment = (
            LeaseAssignment(LeaseKey(lease_name, generation), client_name, context if context is not None else {})
            if lease_name is not None
            else None
        )
        self._generation = generation
        self._publish(replace(self._snapshot, lease_assignment=lease_assignment))

    def request_shutdown(self, request: ShutdownRequest) -> None:
        """Stronger requests take precedence; skipping controller cleanup is sticky."""
        previous = self._snapshot.shutdown
        if previous is not None:
            stronger = request.mode > previous.mode
            request = ShutdownRequest(
                mode=max(previous.mode, request.mode),
                exit_code=(
                    request.exit_code
                    if request.exit_code is not None and (stronger or previous.exit_code is None)
                    else previous.exit_code
                ),
                reason=(request.reason or previous.reason) if stronger else (previous.reason or request.reason),
                should_unregister=previous.should_unregister and request.should_unregister,
            )
        self._publish(replace(self._snapshot, shutdown=request))

    async def send(self, status: jumpstarter_pb2.StatusResponse) -> None:
        """Status-stream adapter; an outage never calls this with a synthetic release."""
        self.publish_lease_assignment(
            status.lease_name if status.leased else None, status.client_name, status.context
        )
        await checkpoint()

    def _publish(self, inputs: Inputs) -> None:
        """Install a new snapshot and wake the runner only when inputs actually changed."""
        if inputs != self._snapshot:
            self._snapshot = inputs
            logger.debug(
                "FSM inputs changed: lease_assignment=%r shutdown_mode=%s",
                inputs.lease_assignment.key if inputs.lease_assignment is not None else None,
                inputs.shutdown.mode.name if inputs.shutdown is not None else None,
            )
            self.wake("snapshot changed")
