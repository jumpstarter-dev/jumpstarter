"""Typed, sequential state machines with coalescing input notifications."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from anyio import Event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitFSM:
    """Explicit terminal result; return only after lifecycle cleanup is complete."""


class InputSource[SnapshotT](Protocol):
    def snapshot_and_watch(self) -> tuple[SnapshotT, Event]:
        """Capture current input and its change event without an intervening await."""
        ...


class State[SnapshotT](Protocol):
    async def run(self, snapshot: SnapshotT) -> State[SnapshotT] | ExitFSM | None:
        """Return a successor, ExitFSM to exit, or None to wait for changed inputs."""
        ...


class FSMRunner[SnapshotT]:
    """Single-use runner with externally readable state and named debug tracing."""

    def __init__(
        self,
        initial: State[SnapshotT] | ExitFSM,
        inputs: InputSource[SnapshotT],
        *,
        name: str = "fsm",
    ) -> None:
        self._state = initial
        self._inputs = inputs
        self._name = name
        self._started = False

    @property
    def current_state(self) -> State[SnapshotT] | ExitFSM:
        """The initial/current/terminal object; observers must not mutate it."""
        return self._state

    async def run(self) -> None:
        """Run once; preserve the last state and propagate failures/cancellation."""
        if self._started:
            raise RuntimeError("FSMRunner can only be run once")
        self._started = True
        logger.debug("FSM %s enter %s", self._name, type(self._state).__name__)
        try:
            while not isinstance(self._state, ExitFSM):
                state = self._state
                state_name = type(state).__name__
                snapshot, changed = self._inputs.snapshot_and_watch()
                started = perf_counter()
                logger.debug("FSM %s run %s started", self._name, state_name)
                try:
                    successor = await state.run(snapshot)
                finally:
                    logger.debug("FSM %s run %s ended in %.6fs", self._name, state_name, perf_counter() - started)
                if successor is None:
                    logger.debug("FSM %s wait %s for inputs", self._name, state_name)
                    await changed.wait()
                else:
                    logger.debug("FSM %s exit %s", self._name, state_name)
                    self._state = successor
                    logger.debug("FSM %s enter %s", self._name, type(successor).__name__)
        except BaseException as error:
            logger.debug("FSM %s stopped in %s (%s)", self._name, type(self._state).__name__, type(error).__name__)
            raise
        logger.debug("FSM %s exited", self._name)
