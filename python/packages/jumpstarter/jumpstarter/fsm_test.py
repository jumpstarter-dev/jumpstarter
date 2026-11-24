from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, assert_type

import pytest
from anyio import (
    CancelScope,
    Event,
    create_task_group,
    fail_after,
    get_cancelled_exc_class,
    get_current_task,
    sleep_forever,
)
from anyio.lowlevel import checkpoint

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
        async def run(self, inputs: bool) -> ExitFSM:
            assert inputs
            entered.set()
            await release.wait()
            return ExitFSM()

    activity = Activity()

    class Waiting:
        async def run(self, inputs: bool) -> Activity | None:
            snapshots.append(inputs)
            evaluated.set()
            return activity if inputs else None

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
async def test_already_cancelled_runner_does_not_evaluate_states() -> None:
    class Immediate:
        async def run(self, inputs: bool) -> ExitFSM:
            raise AssertionError("State ran despite pending cancellation")

    initial = Immediate()
    runner = FSMRunner(initial, Switch())
    with CancelScope() as scope:
        scope.cancel()
        with pytest.raises(get_cancelled_exc_class()):
            await runner.run()
    assert runner.current_state is initial


@pytest.mark.anyio
async def test_immediate_transitions_allow_input_publication_and_cancellation() -> None:
    source = Switch()
    entered = Event()
    observed = Event()
    scope = CancelScope()
    snapshots: list[bool] = []

    class Immediate:
        async def run(self, inputs: bool) -> Immediate:
            snapshots.append(inputs)
            # A finite limit makes starvation fail instead of hanging the event loop.
            assert len(snapshots) < 100, "Immediate transitions starved the producer"
            assert inputs == source.value  # Capture inputs after yielding to producers.
            entered.set()
            if inputs:
                observed.set()
            return Immediate()

    async def publish_and_cancel() -> None:
        await entered.wait()
        source.publish(True)
        await observed.wait()
        scope.cancel()

    runner = FSMRunner(Immediate(), source)
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(publish_and_cancel)
            with scope, pytest.raises(get_cancelled_exc_class()):
                await runner.run()
    assert snapshots[0] is False and snapshots[-1] is True
    assert isinstance(runner.current_state, Immediate)


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
        async def run(self, inputs: bool) -> ExitFSM:
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


@pytest.mark.anyio
async def test_nested_machine_scopes_resources_to_the_state_that_runs_it(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="jumpstarter.fsm")
    inputs = Switch()
    waiting = {"a": Event(), "b": Event()}
    trace: list[str] = []
    tasks: list[int] = []

    @asynccontextmanager
    async def resource(name: str):
        tasks.append(get_current_task().id)
        trace.append(f"open {name}")
        try:
            yield
        finally:
            await checkpoint()
            # The nested machine has exited: the owning state is current again.
            assert isinstance(runner.current_state, Scope)
            tasks.append(get_current_task().id)
            trace.append(f"close {name}")

    @dataclass
    class Scope:
        name: str

        async def run(self, inputs: bool) -> Scope | ExitFSM:
            async with resource(f"{self.name} first"), resource(f"{self.name} second"):
                await runner.run_nested(Waiting(self.name))
            # Both resources are closed before the successor can open its own.
            trace.append(f"closed {self.name}")
            return Scope("b") if self.name == "a" else ExitFSM()

    @dataclass
    class Waiting:
        name: str

        async def run(self, inputs: bool) -> ExitFSM | None:
            assert trace[-2:] == [f"open {self.name} first", f"open {self.name} second"]
            waiting[self.name].set()
            return ExitFSM() if inputs == (self.name == "a") else None

    initial = Scope("a")
    runner = FSMRunner(initial, inputs, name="outer")
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(runner.run)
            await waiting["a"].wait()
            # The innermost active state is the one observers see.
            assert isinstance(runner.current_state, Waiting) and runner.current_state.name == "a"
            inputs.publish(True)
            await waiting["b"].wait()
            assert isinstance(runner.current_state, Waiting) and runner.current_state.name == "b"
            inputs.publish(False)
    assert trace == [
        "open a first", "open a second", "close a second", "close a first", "closed a",
        "open b first", "open b second", "close b second", "close b first", "closed b",
    ]  # fmt: skip
    assert len(set(tasks)) == 1
    assert isinstance(runner.current_state, ExitFSM)
    messages = [record.getMessage() for record in caplog.records if record.name == "jumpstarter.fsm"]
    assert messages.count("FSM outer/Scope enter Waiting") == 2
    assert messages.count("FSM outer/Scope exited") == 2
    assert messages[-1] == "FSM outer exited"


@pytest.mark.anyio
async def test_nested_machines_nest_further() -> None:
    seen: list[str] = []

    class Leaf:
        async def run(self, inputs: bool) -> ExitFSM:
            assert runner.current_state is self
            seen.append("leaf")
            return ExitFSM()

    class Middle:
        async def run(self, inputs: bool) -> ExitFSM:
            # The outer runner hands the request to its innermost machine.
            await runner.run_nested(Leaf())
            assert runner.current_state is self
            seen.append("middle")
            return ExitFSM()

    class Outer:
        async def run(self, inputs: bool) -> ExitFSM:
            await runner.run_nested(Middle())
            assert runner.current_state is self
            seen.append("outer")
            return ExitFSM()

    runner = FSMRunner(Outer(), Switch())
    with fail_after(2):
        await runner.run()
    assert seen == ["leaf", "middle", "outer"]


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["error", "setup_failure", "cancel_activity", "cancel_wait"])
async def test_scoped_resources_finish_awaited_cleanup_on_failure_or_cancellation(outcome: str) -> None:
    entered = Event()
    closed: list[str] = []
    scope = CancelScope()

    @asynccontextmanager
    async def resource(name: str):
        try:
            if name == "partial":
                raise RuntimeError("setup failed")
            yield
        finally:
            with fail_after(1, shield=True):
                await checkpoint()
                closed.append(name)

    class Activity:
        async def run(self, inputs: bool) -> None:
            entered.set()
            if outcome == "error":
                raise RuntimeError("activity failed")
            if outcome == "cancel_activity":
                await sleep_forever()

    activity = Activity()

    class Scope:
        async def run(self, inputs: bool) -> ExitFSM:
            async with resource("entered"):
                if outcome == "setup_failure":
                    async with resource("partial"):
                        raise AssertionError("Entered a context whose setup failed")
                await runner.run_nested(activity)
            return ExitFSM()

    owner = Scope()
    runner = FSMRunner(owner, Switch())

    async def cancel() -> None:
        await entered.wait()
        scope.cancel()

    with fail_after(2):
        async with create_task_group() as tg:
            if outcome.startswith("cancel"):
                tg.start_soon(cancel)
            error = get_cancelled_exc_class() if outcome.startswith("cancel") else RuntimeError
            with scope, pytest.raises(error):
                await runner.run()
    assert closed == (["partial", "entered"] if outcome == "setup_failure" else ["entered"])
    # The last active state stays queryable: the nested one unless setup never reached it.
    assert runner.current_state is (owner if outcome == "setup_failure" else activity)


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [False, True])
async def test_close_failure_stops_in_the_owning_state_and_closes_remaining_resources(timeout: bool) -> None:
    closed: list[str] = []

    @asynccontextmanager
    async def resource(name: str):
        try:
            yield
        finally:
            with fail_after(0 if timeout and name == "broken" else 1, shield=True):
                await checkpoint()
                if name == "broken":
                    raise RuntimeError("close failed")
                closed.append(name)

    class Done:
        async def run(self, inputs: bool) -> ExitFSM:
            return ExitFSM()

    class Scope:
        async def run(self, inputs: bool) -> ExitFSM:
            async with resource("first"), resource("broken"):
                await runner.run_nested(Done())
            raise AssertionError("A failed close must not reach the successor")

    owner = Scope()
    runner = FSMRunner(owner, Switch())
    with fail_after(2), pytest.raises(TimeoutError if timeout else RuntimeError):
        await runner.run()
    assert closed == ["first"]
    assert runner.current_state is owner
