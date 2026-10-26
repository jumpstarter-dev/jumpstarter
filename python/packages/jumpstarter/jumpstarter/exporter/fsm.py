"""Exporter-specific snapshots and inputs for jumpstarter.fsm."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import IntEnum
from types import MappingProxyType

from anyio import Event

from jumpstarter.fsm import State as FSMState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaseKey:
    name: str
    generation: int

    def __post_init__(self) -> None:
        if not self.name or self.generation < 1:
            raise ValueError("An assigned lease needs a non-empty name and a positive generation")


@dataclass(frozen=True)
class Assignment:
    key: LeaseKey
    client_name: str = ""
    context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


class ShutdownMode(IntEnum):
    DRAIN = 1
    STOP_NOW = 2


@dataclass(frozen=True)
class ShutdownRequest:
    mode: ShutdownMode
    exit_code: int | None = None
    reason: str = ""
    should_unregister: bool = False


@dataclass(frozen=True)
class Snapshot:
    assignment: Assignment | None = None
    shutdown: ShutdownRequest | None = None


type State = FSMState[Snapshot]


class Inputs:
    """Latest assignment, persistent shutdown intent, and a coalescing runner wakeup."""

    def __init__(self) -> None:
        self._snapshot = Snapshot()
        self._changed = Event()
        self._generation = 0

    def snapshot_and_watch(self) -> tuple[Snapshot, Event]:
        return self._snapshot, self._changed

    def wake(self, reason: str = "lease-local input") -> None:
        """Notify after updating lease-local inputs, even if the snapshot is unchanged."""
        # ponytail: event-loop-only updates; marshal thread callbacks onto the loop if needed.
        changed = self._changed
        self._changed = Event()
        changed.set()
        logger.debug("FSM wakeup: %s", reason)

    def _publish(self, snapshot: Snapshot) -> None:
        if snapshot != self._snapshot:
            self._snapshot = snapshot
            logger.debug(
                "FSM inputs changed: assignment=%r shutdown_mode=%s",
                snapshot.assignment.key if snapshot.assignment is not None else None,
                snapshot.shutdown.mode.name if snapshot.shutdown is not None else None,
            )
            self.wake("snapshot changed")

    def publish_assignment(
        self,
        lease_name: str | None,
        client_name: str = "",
        context: Mapping[str, str] | None = None,
    ) -> None:
        """Publish an observation; None means unleased, not a stream interruption."""
        if lease_name == "":
            raise ValueError("An assigned lease needs a non-empty name; use None for unleased")
        previous = self._snapshot.assignment
        previous_name = previous.key.name if previous is not None else None
        generation = self._generation + (previous_name != lease_name)
        assignment = (
            Assignment(LeaseKey(lease_name, generation), client_name, context if context is not None else {})
            if lease_name is not None
            else None
        )
        self._generation = generation
        self._publish(replace(self._snapshot, assignment=assignment))

    def request_shutdown(self, request: ShutdownRequest) -> None:
        """Stop-now dominates; first code/reason wins; skipping controller cleanup is sticky."""
        previous = self._snapshot.shutdown
        if previous is not None:
            request = ShutdownRequest(
                mode=max(previous.mode, request.mode),
                exit_code=previous.exit_code if previous.exit_code is not None else request.exit_code,
                reason=previous.reason or request.reason,
                should_unregister=previous.should_unregister and request.should_unregister,
            )
        self._publish(replace(self._snapshot, shutdown=request))
