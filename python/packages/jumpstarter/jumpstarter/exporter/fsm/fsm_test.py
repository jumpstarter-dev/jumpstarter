from __future__ import annotations

import logging
import re
from contextlib import AsyncExitStack
from dataclasses import FrozenInstanceError, dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, assert_type

import pytest
from anyio import Event, create_task_group, fail_after
from anyio.lowlevel import checkpoint

from jumpstarter.exporter.fsm import (
    InputBuffer,
    Inputs,
    LeaseAssignment,
    LeaseKey,
    ShutdownMode,
    ShutdownRequest,
    State,
)
from jumpstarter.exporter.fsm.lease_context import LeaseContext
from jumpstarter.fsm import ExitFSM, FSMRunner, InputSource


def request_end_session(lease: LeaseContext, inputs: InputBuffer) -> None:
    """Demo RPC adapter: retain the request on this session's lease, then wake the runner."""
    if not lease.end_session_requested:
        lease.request_end_session()
        inputs.wake("EndSession")


@dataclass
class Story:
    fail_hook: bool
    trace: list[str] = field(default_factory=list)
    waiting: Event = field(default_factory=Event)
    entered: Event = field(default_factory=Event)
    release: Event = field(default_factory=Event)
    ready: Event = field(default_factory=Event)
    closing: Event = field(default_factory=Event)
    teardown: Event | None = None
    started: list[tuple[LeaseKey, LeaseContext]] = field(default_factory=list)
    closed: list[LeaseKey] = field(default_factory=list)
    observed: Inputs | None = None


@dataclass
class Available:
    story: Story
    last_completed: LeaseKey | None = None

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> BeforeHook | ExitFSM | None:
        if inputs.shutdown is not None:
            return ExitFSM()
        if inputs.lease_assignment is not None and inputs.lease_assignment.key != self.last_completed:
            key = inputs.lease_assignment.key
            lease = LeaseContext(lease_name=key.name)
            self.story.started.append((key, lease))
            return BeforeHook(self.story, key, lease)
        self.story.trace.append("waiting")
        self.story.waiting.set()
        return None


@dataclass
class BeforeHook:
    story: Story
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Ready | BeforeFailed:
        self.story.trace.append("before")
        self.story.entered.set()
        await self.story.release.wait()
        if self.story.fail_hook:
            return BeforeFailed(self.story, self.key, self.lease)
        return Ready(self.story, self.key, self.lease)


@dataclass
class BeforeFailed:
    story: Story
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Cleanup:
        self.story.trace.append("failed")
        self.story.observed = inputs
        return Cleanup(self.story, self.key, self.lease)


@dataclass
class Ready:
    story: Story
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Cleanup | None:
        self.story.trace.append("ready")
        self.story.ready.set()
        self.story.observed = inputs
        if (
            inputs.lease_assignment is None
            or inputs.lease_assignment.key != self.key
            or (inputs.shutdown is not None and inputs.shutdown.mode == ShutdownMode.STOP_NOW)
            or self.lease.end_session_requested
        ):
            return Cleanup(self.story, self.key, self.lease)
        return None


@dataclass
class Cleanup:
    story: Story
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Available | ExitFSM:
        self.story.trace.append("cleanup")
        self.story.closing.set()
        if self.story.teardown is not None:
            await self.story.teardown.wait()
        self.story.closed.append(self.key)
        return ExitFSM() if inputs.shutdown is not None else Available(self.story, last_completed=self.key)


if TYPE_CHECKING:

    async def _check_types(inputs: InputBuffer, before: BeforeHook, initial: State, resources: AsyncExitStack) -> None:
        snapshot, _ = inputs.snapshot_and_watch()
        assert_type(await initial.run(snapshot, resources), State | ExitFSM | None)
        assert_type(await before.run(snapshot, resources), Ready | BeforeFailed)
        assert_type(await Cleanup(before.story, before.key, before.lease).run(snapshot, resources), Available | ExitFSM)
        assert_type(inputs.snapshot_and_watch(), tuple[Inputs, Event])
        assert_type(snapshot.lease_assignment, LeaseAssignment | None)
        assert_type(snapshot.shutdown, ShutdownRequest | None)
        assert_type(before.lease.end_session_requested, bool)
        source: InputSource[Inputs] = inputs
        assert_type(FSMRunner(initial, source, name="exporter").current_state, State | ExitFSM)


@pytest.mark.anyio
@pytest.mark.parametrize("fail_hook", [False, True])
async def test_sequential_activity_defers_inputs_and_uses_latest_snapshot(fail_hook: bool) -> None:
    inputs = InputBuffer()
    story = Story(fail_hook)
    finished = Event()
    initial = Available(story)
    runner = FSMRunner(initial, inputs, name="exporter")
    assert runner.current_state is initial

    async def run() -> None:
        await runner.run()
        story.trace.append("finished")
        finished.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(run)
            await story.waiting.wait()
            assert runner.current_state is initial
            inputs.publish_lease_assignment("A")
            await story.entered.wait()
            current = runner.current_state
            assert isinstance(current, BeforeHook)
            with pytest.raises(RuntimeError, match="only be run once"):
                await runner.run()
            original, _ = inputs.snapshot_and_watch()
            key, lease = story.started[0]
            for index in range(100):
                inputs.publish_lease_assignment(f"B{index}")
                await checkpoint()
            inputs.publish_lease_assignment("C")
            inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN, should_unregister=True))
            request_end_session(lease, inputs)
            assert story.trace == ["waiting", "before"]
            assert original.lease_assignment is not None and original.lease_assignment.key.name == "A"
            assert original.shutdown is None
            assert runner.current_state is current
            story.release.set()
            await finished.wait()

    assert story.trace == ["waiting", "before", "failed" if fail_hook else "ready", "cleanup", "finished"]
    assert story.observed is not None
    assert story.observed.lease_assignment is not None and story.observed.lease_assignment.key.name == "C"
    assert story.observed.shutdown == ShutdownRequest(ShutdownMode.DRAIN, should_unregister=True)
    assert lease.end_session_requested
    assert story.closed == [key]
    final, _ = inputs.snapshot_and_watch()
    assert final.shutdown == story.observed.shutdown
    assert isinstance(runner.current_state, ExitFSM)
    with pytest.raises(RuntimeError, match="only be run once"):
        await runner.run()


@pytest.mark.anyio
@pytest.mark.parametrize("lease_local", [False, True])
async def test_runner_captures_watch_before_state_can_await_and_return_none(lease_local: bool) -> None:
    inputs = InputBuffer()
    lease = LeaseContext(lease_name="A")
    entered = Event()
    release = Event()
    finished = Event()
    calls = 0

    class Evaluating:
        async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ExitFSM | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
                assert inputs.lease_assignment is None  # This call still has its original inputs.
                return None
            if lease_local:
                assert inputs == Inputs() and lease.end_session_requested
            else:
                assert inputs.lease_assignment is not None and inputs.lease_assignment.key.name == "A"
            return ExitFSM()

    async def run() -> None:
        await FSMRunner(Evaluating(), inputs, name="exporter").run()
        finished.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(run)
            await entered.wait()
            if lease_local:
                request_end_session(lease, inputs)
            else:
                inputs.publish_lease_assignment("A")
            release.set()
            await finished.wait()
    assert calls == 2


@pytest.mark.anyio
async def test_snapshot_watch_cannot_miss_update_before_wait() -> None:
    inputs = InputBuffer()
    original, changed = inputs.snapshot_and_watch()
    inputs.publish_lease_assignment("A")
    inputs.publish_lease_assignment("B")
    inputs.publish_lease_assignment("C")
    with fail_after(2):
        await changed.wait()
    latest, next_change = inputs.snapshot_and_watch()
    assert original == Inputs()
    assert latest.lease_assignment is not None and latest.lease_assignment.key == LeaseKey("C", 3)
    assert changed is not next_change and not next_change.is_set()
    inputs.publish_lease_assignment("C")
    assert inputs.snapshot_and_watch() == (latest, next_change)
    assert not next_change.is_set()


@pytest.mark.anyio
async def test_assignment_generations_preserve_release_boundaries() -> None:
    inputs = InputBuffer()
    inputs.publish_lease_assignment("A")
    a = LeaseKey("A", 1)
    inputs.publish_lease_assignment("A", "client", {"job": "1"})
    snapshot, _ = inputs.snapshot_and_watch()
    assert snapshot.lease_assignment is not None and snapshot.lease_assignment.key == a
    inputs.publish_lease_assignment(None)
    inputs.publish_lease_assignment("A")
    replacement = LeaseKey("A", 3)
    snapshot, _ = inputs.snapshot_and_watch()
    assert snapshot.lease_assignment is not None and snapshot.lease_assignment.key == replacement
    inputs.publish_lease_assignment("A", "another-client", {"job": "2"})
    snapshot, _ = inputs.snapshot_and_watch()
    assert snapshot.lease_assignment is not None and snapshot.lease_assignment.key == replacement
    inputs.publish_lease_assignment("B")
    snapshot, _ = inputs.snapshot_and_watch()
    assert snapshot.lease_assignment is not None and snapshot.lease_assignment.key == LeaseKey("B", 4)


@pytest.mark.anyio
@pytest.mark.parametrize("replacement_name", ["A", "B"])
async def test_states_own_teardown_completion_and_lease_scoped_requests(replacement_name: str) -> None:
    inputs = InputBuffer()
    inputs.publish_lease_assignment("A")
    story = Story(False, teardown=Event())
    story.release.set()
    finished = Event()

    async def run() -> None:
        await FSMRunner(Available(story), inputs, name="exporter").run()
        finished.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(run)
            await story.ready.wait()
            old_key, old_lease = story.started[0]
            inputs.publish_lease_assignment(None)
            inputs.publish_lease_assignment(replacement_name)
            await story.closing.wait()
            assert len(story.started) == 1 and story.closed == []
            story.ready = Event()
            assert story.teardown is not None
            story.teardown.set()
            await story.ready.wait()
            new_key, new_lease = story.started[1]
            assert old_key == LeaseKey("A", 1) and new_key == LeaseKey(replacement_name, 3)
            assert story.closed == [old_key]

            before, changed = inputs.snapshot_and_watch()
            request_end_session(old_lease, inputs)
            await checkpoint()
            assert changed.is_set() and not new_lease.end_session_requested
            assert len(story.started) == 2 and story.closed == [old_key]
            after, _ = inputs.snapshot_and_watch()
            assert after is before  # A lease-local request wakes the runner without changing observations.

            story.waiting = Event()
            request_end_session(new_lease, inputs)
            _, changed = inputs.snapshot_and_watch()
            request_end_session(new_lease, inputs)
            request_end_session(old_lease, inputs)
            assert not changed.is_set() and new_lease.end_session_requested
            await story.waiting.wait()
            assert story.closed == [old_key, new_key]

            # Available remembers completion even if client/context updates wake it again.
            inputs.publish_lease_assignment(replacement_name, "another-client", {"job": "2"})
            await checkpoint()
            assert len(story.started) == 2
            inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN))
            await finished.wait()


@pytest.mark.anyio
async def test_assignment_snapshots_copy_metadata_and_validate_before_publishing() -> None:
    inputs = InputBuffer()
    metadata = {"job": "old"}
    inputs.publish_lease_assignment("A", "client", metadata)
    original, changed = inputs.snapshot_and_watch()
    assert original.lease_assignment is not None
    metadata["job"] = "new"
    assert original.lease_assignment.context == {"job": "old"}
    assert isinstance(original.lease_assignment.context, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        original.lease_assignment.__setattr__("client_name", "mutated")
    with pytest.raises(ValueError, match="non-empty name"):
        inputs.publish_lease_assignment("")
    assert inputs.snapshot_and_watch() == (original, changed)
    assert not changed.is_set()
    inputs.publish_lease_assignment("A", "client", metadata)
    latest, _ = inputs.snapshot_and_watch()
    assert changed.is_set() and latest.lease_assignment is not None
    assert latest.lease_assignment.key == original.lease_assignment.key
    assert latest.lease_assignment.context == {"job": "new"}
    assert original.lease_assignment.context == {"job": "old"}
    with pytest.raises(ValueError, match="positive generation"):
        LeaseKey("A", 0)


@pytest.mark.anyio
@pytest.mark.parametrize("first_code", [None, 0, 1, 75])
async def test_shutdown_escalates_without_losing_exit_or_cleanup_policy(first_code: int | None) -> None:
    inputs = InputBuffer()
    inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN, first_code, "first", True))
    original, _ = inputs.snapshot_and_watch()
    inputs.request_shutdown(ShutdownRequest(ShutdownMode.STOP_NOW, 75, "stream failure", False))
    inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN, 1, "later drain", True))
    snapshot, changed = inputs.snapshot_and_watch()
    assert snapshot.shutdown == ShutdownRequest(ShutdownMode.STOP_NOW, 75, "stream failure", False)
    assert original.shutdown == ShutdownRequest(ShutdownMode.DRAIN, first_code, "first", True)
    inputs.request_shutdown(ShutdownRequest(ShutdownMode.STOP_NOW, should_unregister=True))
    assert inputs.snapshot_and_watch() == (snapshot, changed)
    assert not changed.is_set()
    inputs.publish_lease_assignment("A")
    snapshot, _ = inputs.snapshot_and_watch()
    story = Story(False)
    async with AsyncExitStack() as resources:
        assert isinstance(await Available(story).run(snapshot, resources), ExitFSM)
    assert story.started == []


@pytest.mark.anyio
async def test_runner_logs_transitions_evaluations_duration_and_safe_inputs(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.DEBUG, logger="jumpstarter")
    clock = iter(range(100))
    monkeypatch.setattr("jumpstarter.fsm.perf_counter", lambda: float(next(clock)))
    inputs = InputBuffer()
    story = Story(False)
    runner = FSMRunner(Available(story), inputs, name="exporter")
    finished = Event()
    with pytest.raises(AttributeError):
        runner.__setattr__("current_state", ExitFSM())

    async def run() -> None:
        await runner.run()
        finished.set()

    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(run)
            await story.waiting.wait()
            story.waiting = Event()
            inputs.wake("manual recheck")
            await story.waiting.wait()
            inputs.publish_lease_assignment("A", "secret-client", {"token": "secret-token"})
            await story.entered.wait()
            assert "FSM exporter run BeforeHook started" in caplog.text
            assert "FSM exporter run BeforeHook ended" not in caplog.text
            inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN, reason="secret-reason"))
            request_end_session(story.started[0][1], inputs)
            story.release.set()
            await finished.wait()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name in ("jumpstarter.fsm", "jumpstarter.exporter.fsm")
    ]
    assert [message for message in messages if message.startswith("FSM exporter enter ")] == [
        "FSM exporter enter Available",
        "FSM exporter enter BeforeHook",
        "FSM exporter enter Ready",
        "FSM exporter enter Cleanup",
        "FSM exporter enter ExitFSM",
    ]
    assert [message for message in messages if message.startswith("FSM exporter exit ")] == [
        "FSM exporter exit Available",
        "FSM exporter exit BeforeHook",
        "FSM exporter exit Ready",
        "FSM exporter exit Cleanup",
    ]
    assert messages.count("FSM exporter run Available started") == 3
    durations = [
        match.group(1)
        for message in messages
        if (match := re.fullmatch(r"FSM exporter run \w+ ended in ([\d.]+)s", message))
    ]
    assert durations == ["1.000000"] * 6
    assert any(
        message.startswith("FSM inputs changed: lease_assignment=LeaseKey(name='A', generation=1)")
        for message in messages
    )
    assert any("shutdown_mode=DRAIN" in message for message in messages)
    assert "FSM wakeup: EndSession" in messages and "FSM wakeup: manual recheck" in messages
    assert "secret-" not in caplog.text
    assert isinstance(runner.current_state, ExitFSM)
