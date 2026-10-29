"""Typed, sequential state machines with coalescing input notifications."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from anyio import Event
from anyio.lowlevel import checkpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitFSM:
    """Stop evaluating states; the runner closes remaining resources before exit."""


class InputSource[InputsT](Protocol):
    def snapshot_and_watch(self) -> tuple[InputsT, Event]:
        """Capture current input and its change event without an intervening await."""
        ...


class State[InputsT](Protocol):
    async def run(self, inputs: InputsT, resources: AsyncExitStack) -> State[InputsT] | ExitFSM | None:
        """Return a successor, ExitFSM to exit, or None to wait for changed inputs."""
        ...


class FSMRunner[InputsT]:
    """Single-use runner with externally readable state and named debug tracing."""

    def __init__(
        self,
        initial: State[InputsT] | ExitFSM,
        inputs: InputSource[InputsT],
        *,
        name: str = "fsm",
    ) -> None:
        self._state = initial
        self._inputs = inputs
        self._name = name
        self._started = False

    @property
    def current_state(self) -> State[InputsT] | ExitFSM:
        """The initial/current/terminal object; observers must not mutate it."""
        return self._state

    async def run(self) -> None:
        """Run once; preserve the last state and propagate failures/cancellation."""
        if self._started:
            raise RuntimeError("FSMRunner can only be run once")
        self._started = True
        logger.debug("FSM %s enter %s", self._name, type(self._state).__name__)
        resources = AsyncExitStack()
        try:
            try:
                while not isinstance(self._state, ExitFSM):
                    # State coroutines may return without yielding, starving cancellation and input producers.
                    # Checkpoint before capturing inputs so the next evaluation sees their latest updates.
                    await checkpoint()
                    state = self._state
                    state_name = type(state).__name__
                    inputs, changed = self._inputs.snapshot_and_watch()
                    started = perf_counter()
                    logger.debug("FSM %s run %s started", self._name, state_name)
                    try:
                        successor = await state.run(inputs, resources)
                    finally:
                        logger.debug("FSM %s run %s ended in %.6fs", self._name, state_name, perf_counter() - started)
                    if successor is None:
                        logger.debug("FSM %s wait %s for inputs", self._name, state_name)
                        await changed.wait()
                    else:
                        if isinstance(successor, ExitFSM):
                            # Keep the last active state queryable until cleanup has actually succeeded.
                            await resources.aclose()
                        logger.debug("FSM %s exit %s", self._name, state_name)
                        self._state = successor
                        logger.debug("FSM %s enter %s", self._name, type(successor).__name__)
            except BaseException as error:
                # Exception-aware resources need the failure (e.g. to roll back).
                # Ignore suppression: a resource cannot turn a failed lifecycle into success.
                await resources.__aexit__(type(error), error, error.__traceback__)
                raise
            else:
                await resources.aclose()
        except BaseException as error:
            logger.debug("FSM %s stopped in %s (%s)", self._name, type(self._state).__name__, type(error).__name__)
            raise
        logger.debug("FSM %s exited", self._name)
