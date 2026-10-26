from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_type

import pytest
from anyio import CancelScope, Event, create_task_group, fail_after, get_cancelled_exc_class, sleep_forever

from jumpstarter.fsm import ExitFSM, FSMRunner, InputSource, State


@dataclass
class Switch:
    """An independent input source: no exporter, lease, or shutdown dependencies."""

    value: bool = False
    changed: Event = field(default_factory=Event)

    def snapshot_and_watch(self) -> tuple[bool, Event]:
        return self.value, self.changed

    def publish(self, value: bool) -> None:
        changed = self.changed
        self.value = value
        self.changed = Event()
        changed.set()


if TYPE_CHECKING:

    async def _check_types(state: State[bool], inputs: InputSource[bool]) -> None:
        assert_type(await state.run(False), State[bool] | ExitFSM | None)
        assert_type(FSMRunner(state, inputs).current_state, State[bool] | ExitFSM)


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["worker", "watchdog"])
async def test_named_runner_queries_waiting_transitions_and_timings(
    name: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG, logger="jumpstarter.fsm")
    clock = iter(range(100))
    monkeypatch.setattr("jumpstarter.fsm.perf_counter", lambda: float(next(clock)))
    inputs = Switch()
    evaluated = Event()
    entered = Event()
    release = Event()
    finished = Event()
    snapshots: list[bool] = []

    class Activity:
        async def run(self, snapshot: bool) -> ExitFSM:
            assert snapshot
            entered.set()
            await release.wait()
            return ExitFSM()

    activity = Activity()

    class Waiting:
        async def run(self, snapshot: bool) -> Activity | None:
            snapshots.append(snapshot)
            evaluated.set()
            return activity if snapshot else None

    initial = Waiting()
    runner = FSMRunner(initial, inputs, name=name)
    assert runner.current_state is initial
    with pytest.raises(AttributeError):
        runner.__setattr__("current_state", ExitFSM())

    async def run() -> None:
        await runner.run()
        finished.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(run)
            await evaluated.wait()
            assert runner.current_state is initial and not finished.is_set()
            with pytest.raises(RuntimeError, match="only be run once"):
                await runner.run()
            evaluated = Event()
            inputs.publish(False)
            await evaluated.wait()
            assert runner.current_state is initial
            inputs.publish(True)
            await entered.wait()
            assert runner.current_state is activity
            release.set()
            await finished.wait()

    assert snapshots == [False, False, True]
    assert isinstance(runner.current_state, ExitFSM)
    messages = [record.getMessage() for record in caplog.records if record.name == "jumpstarter.fsm"]
    assert [message for message in messages if " enter " in message] == [
        f"FSM {name} enter Waiting",
        f"FSM {name} enter Activity",
        f"FSM {name} enter ExitFSM",
    ]
    assert [message for message in messages if " exit " in message] == [
        f"FSM {name} exit Waiting",
        f"FSM {name} exit Activity",
    ]
    assert sum("ended in 1.000000s" in message for message in messages) == 4
    assert all(message.startswith(f"FSM {name} ") for message in messages)
    with pytest.raises(RuntimeError, match="only be run once"):
        await runner.run()


@pytest.mark.anyio
async def test_exit_fsm_can_be_the_initial_state() -> None:
    initial = ExitFSM()
    runner = FSMRunner(initial, Switch())
    with fail_after(2):
        await runner.run()
    assert runner.current_state is initial


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [False, True])
async def test_runner_preserves_state_and_propagates_failure_or_cancellation(
    cancel: bool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="jumpstarter.fsm")
    entered = Event()
    observed = Event()
    cleaned = Event()
    scope = CancelScope()
    error = RuntimeError("state failed")

    class Activity:
        async def run(self, snapshot: bool) -> ExitFSM:
            entered.set()
            if cancel:
                await sleep_forever()
            raise error

    activity = Activity()
    runner = FSMRunner(activity, Switch(), name="broken")

    async def owner() -> None:
        with scope:
            try:
                await runner.run()
                raise AssertionError("Runner swallowed the failure")
            except get_cancelled_exc_class():
                assert cancel
                observed.set()
                raise
            except RuntimeError as caught:
                assert not cancel and caught is error
                observed.set()
            finally:
                cleaned.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(owner)
            await entered.wait()
            if cancel:
                scope.cancel()
            await observed.wait()
            await cleaned.wait()
    assert runner.current_state is activity
    assert "FSM broken run Activity ended in" in caplog.text
    assert "FSM broken stopped in Activity" in caplog.text
    assert "FSM broken enter ExitFSM" not in caplog.text
    with pytest.raises(RuntimeError, match="only be run once"):
        await runner.run()
