"""Typed, sequential state machines with coalescing input notifications."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol

from anyio import Event
from anyio.lowlevel import checkpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExitFSM:
    """Stop evaluating states; a nested machine returns to the state that runs it."""


class InputSource[InputsT](Protocol):
    def snapshot_and_watch(self) -> tuple[InputsT, Event]:
        """Capture current input and its change event without an intervening await."""
        ...


class State[InputsT](Protocol):
    async def run(self, inputs: InputsT) -> State[InputsT] | ExitFSM | None:
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
        self._nested: FSMRunner[InputsT] | None = None

    @property
    def current_state(self) -> State[InputsT] | ExitFSM:
        """The initial/current/terminal object; observers must not mutate it.

        While a state runs a nested machine, this is the innermost active state.
        """
        return self._nested.current_state if self._nested is not None else self._state

    async def run_nested(self, initial: State[InputsT] | ExitFSM) -> None:
        """Run a nested machine to its exit, from inside the current state's run().

        A state that owns resources opens them with ``async with`` and runs the
        states that use them here, so they live exactly as long as that block.
        """
        if self._nested is not None:
            await self._nested.run_nested(initial)
            return
        nested = FSMRunner(initial, self._inputs, name=f"{self._name}/{type(self._state).__name__}")
        self._nested = nested
        await nested.run()
        # Reached only on a successful exit: a failed nested state stays queryable.
        self._nested = None

    async def run(self) -> None:
        """Run once; preserve the last state and propagate failures/cancellation."""
        if self._started:
            raise RuntimeError("FSMRunner can only be run once")
        self._started = True
        logger.info("FSM %s enter %s", self._name, type(self._state).__name__)
        try:
            while not isinstance(self._state, ExitFSM):
                # State coroutines may return without yielding, starving cancellation and input producers.
                # Checkpoint before capturing inputs so the next evaluation sees their latest updates.
                await checkpoint()
                state = self._state
                state_name = type(state).__name__
                inputs, changed = self._inputs.snapshot_and_watch()
                started = perf_counter()
                logger.info("FSM %s run %s started", self._name, state_name)
                try:
                    successor = await state.run(inputs)
                finally:
                    logger.info("FSM %s run %s ended in %.6fs", self._name, state_name, perf_counter() - started)
                if successor is None:
                    logger.info("FSM %s wait %s for inputs", self._name, state_name)
                    await changed.wait()
                else:
                    logger.debug("FSM %s exit %s", self._name, state_name)
                    self._state = successor
                    logger.debug("FSM %s enter %s", self._name, type(successor).__name__)
        except BaseException as error:
            logger.error("FSM %s stopped in %s (%s)", self._name, type(self._state).__name__, type(error).__name__)
            raise
        logger.info("FSM %s exited", self._name)
