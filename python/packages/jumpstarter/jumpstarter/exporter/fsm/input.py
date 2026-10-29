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

from jumpstarter.exporter.lease_hooks import Restart, decide_restart
from jumpstarter.fsm import State as FSMState

logger = logging.getLogger("jumpstarter.exporter.fsm")


@dataclass(frozen=True)
class LeaseKey:
    """Local identity for one admitted lease assignment: name plus publisher generation."""

    name: str
    generation: int
    uid: str = ""
    """UID of that lease; tells it apart from an earlier lease with the same name"""

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
    status_seen: bool = False
    """The controller's Status stream has delivered at least one observation."""
    hooks_supported: bool = False
    """The controller keeps lease hook records (its Status updates carry lease_hooks)."""
    takeover: Takeover | None = None
    """What the controller's hook record says a previous exporter process left owed."""


@dataclass(frozen=True)
class Takeover:
    """The controller's hook record for the lease a previous process worked on, decided."""

    restart: Restart
    lease_name: str
    lease_uid: str
    client_name: str = ""
    assigned: bool = False
    before: jumpstarter_pb2.LeaseHookState | None = None
    after: jumpstarter_pb2.LeaseHookState | None = None


def _takeover_from_status(status: jumpstarter_pb2.StatusResponse) -> Takeover:
    """Read the controller's hook record against the lease assigned now."""
    record = status.lease_hooks
    assigned = (status.lease_name, status.lease_uid) if status.leased else None
    return Takeover(
        restart=decide_restart(record, assigned),
        lease_name=record.lease_name,
        lease_uid=record.lease_uid,
        client_name=record.client_name,
        assigned=assigned is not None,
        before=record.before_lease if record.HasField("before_lease") else None,
        after=record.after_lease if record.HasField("after_lease") else None,
    )


type State = FSMState[Inputs]


class InputBuffer:
    """Latest lease assignment, persistent shutdown intent, and a coalescing runner wakeup."""

    def __init__(self) -> None:
        self._snapshot = Inputs()
        self._changed = Event()
        self._generation = 0
        self._status_seen = False
        self._takeover: Takeover | None = None
        self._hooks_supported = False

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

    def _publish_lease_assignment(
        self,
        lease_name: str | None,
        client_name: str = "",
        context: Mapping[str, str] | None = None,
        *,
        uid: str = "",
        status_seen: bool = False,
        hooks_supported: bool = False,
    ) -> None:
        if lease_name == "":
            raise ValueError("An assigned lease needs a non-empty name; use None for unleased")
        self._status_seen = self._status_seen or status_seen
        self._hooks_supported = self._hooks_supported or hooks_supported
        previous = self._snapshot.lease_assignment
        previous_id = (previous.key.name, previous.key.uid) if previous is not None else (None, "")
        generation = self._generation + (previous_id != (lease_name, uid))
        lease_assignment = (
            LeaseAssignment(LeaseKey(lease_name, generation, uid), client_name, context if context is not None else {})
            if lease_name is not None
            else None
        )
        self._generation = generation
        self._publish(
            replace(
                self._snapshot,
                lease_assignment=lease_assignment,
                status_seen=self._status_seen,
                hooks_supported=self._hooks_supported,
                takeover=self._takeover,
            )
        )

    def publish_lease_assignment(
        self,
        lease_name: str | None,
        client_name: str = "",
        context: Mapping[str, str] | None = None,
    ) -> None:
        """Publish a lease-assignment observation; None means unleased, not a stream interruption."""
        self._publish_lease_assignment(lease_name, client_name, context, status_seen=True)

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
        hooks_supported = status.HasField("lease_hooks")
        if not self._status_seen:
            self._status_seen = True
            if hooks_supported and self._takeover is None:
                # Latched from the first status only: later records describe this
                # process's own work, not work left owed by a previous one.
                self._takeover = _takeover_from_status(status)
        self._publish_lease_assignment(
            status.lease_name if status.leased else None,
            status.client_name,
            status.context,
            uid=status.lease_uid,
            status_seen=True,
            hooks_supported=hooks_supported,
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
