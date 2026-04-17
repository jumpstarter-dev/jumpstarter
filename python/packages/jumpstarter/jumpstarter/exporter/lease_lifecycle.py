from __future__ import annotations

import logging
from enum import Enum

from anyio import Event

logger = logging.getLogger(__name__)


class LeasePhase(Enum):
    CREATED = "created"
    STARTING = "starting"
    BEFORE_LEASE = "before_lease"
    READY = "ready"
    ENDING = "ending"
    AFTER_LEASE = "after_lease"
    RELEASING = "releasing"
    DONE = "done"
    FAILED = "failed"


class InvalidTransitionError(Exception):
    def __init__(self, current: LeasePhase, target: LeasePhase) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition {current.name} -> {target.name}")


_VALID_TRANSITIONS: dict[LeasePhase, frozenset[LeasePhase]] = {
    LeasePhase.CREATED: frozenset({LeasePhase.STARTING, LeasePhase.FAILED}),
    LeasePhase.STARTING: frozenset(
        {LeasePhase.BEFORE_LEASE, LeasePhase.READY, LeasePhase.ENDING, LeasePhase.FAILED}
    ),
    LeasePhase.BEFORE_LEASE: frozenset({LeasePhase.READY, LeasePhase.ENDING, LeasePhase.FAILED}),
    LeasePhase.READY: frozenset({LeasePhase.ENDING, LeasePhase.FAILED}),
    LeasePhase.ENDING: frozenset(
        {LeasePhase.AFTER_LEASE, LeasePhase.RELEASING, LeasePhase.DONE, LeasePhase.FAILED}
    ),
    LeasePhase.AFTER_LEASE: frozenset({LeasePhase.RELEASING, LeasePhase.FAILED}),
    LeasePhase.RELEASING: frozenset({LeasePhase.DONE, LeasePhase.FAILED}),
    LeasePhase.DONE: frozenset(),
    LeasePhase.FAILED: frozenset(),
}


class LeaseLifecycle:
    def __init__(self) -> None:
        self._phase = LeasePhase.CREATED
        self._end_requested = False
        self._skip_after_lease = False
        self._ready_event = Event()
        self._complete_event = Event()
        self._end_event = Event()

    @property
    def phase(self) -> LeasePhase:
        return self._phase

    @property
    def end_requested(self) -> bool:
        return self._end_requested

    @property
    def skip_after_lease(self) -> bool:
        return self._skip_after_lease

    @skip_after_lease.setter
    def skip_after_lease(self, value: bool) -> None:
        self._skip_after_lease = value

    def transition(self, target: LeasePhase) -> None:
        if target not in _VALID_TRANSITIONS[self._phase]:
            raise InvalidTransitionError(self._phase, target)
        old = self._phase
        self._phase = target
        logger.debug("Lease lifecycle transition: %s -> %s", old.name, target.name)
        if target in (LeasePhase.READY, LeasePhase.DONE, LeasePhase.FAILED):
            self._ready_event.set()
        if target in (LeasePhase.DONE, LeasePhase.FAILED):
            self._complete_event.set()
        if target == LeasePhase.ENDING:
            self._end_event.set()

    def request_end(self) -> None:
        self._end_event.set()
        if self._phase == LeasePhase.READY:
            self.transition(LeasePhase.ENDING)
        elif self._phase in (LeasePhase.BEFORE_LEASE, LeasePhase.STARTING):
            self._end_requested = True

    async def wait_ready(self) -> None:
        await self._ready_event.wait()

    async def wait_complete(self) -> None:
        await self._complete_event.wait()

    def is_ready(self) -> bool:
        return self._phase == LeasePhase.READY

    def is_complete(self) -> bool:
        return self._phase in (LeasePhase.DONE, LeasePhase.FAILED)

    def is_end_requested(self) -> bool:
        return self._end_event.is_set()

    async def wait_end_requested(self) -> None:
        await self._end_event.wait()

    def drivers_ready(self) -> bool:
        return self._phase in (
            LeasePhase.READY,
            LeasePhase.ENDING,
            LeasePhase.AFTER_LEASE,
            LeasePhase.RELEASING,
        )
