from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError
from types import MappingProxyType
from typing import TYPE_CHECKING, assert_type

import pytest
from anyio import Event, create_task_group, fail_after
from jumpstarter_protocol import jumpstarter_pb2
from jumpstarter_protocol.jumpstarter.v1.jumpstarter_pb2 import LEASE_HOOK_PHASE_RUNNING as RUNNING_STATE

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
from jumpstarter.exporter.lease_hooks import Restart
from jumpstarter.fsm import ExitFSM, FSMRunner, InputSource


def request_end_session(lease: LeaseContext, inputs: InputBuffer) -> None:
    """Demo RPC adapter: retain the request on this session's lease, then wake the runner."""
    if not lease.end_session_requested:
        lease.request_end_session()
        inputs.wake("EndSession")


if TYPE_CHECKING:

    async def _check_types(inputs: InputBuffer, initial: State, lease: LeaseContext) -> None:
        snapshot, _ = inputs.snapshot_and_watch()
        assert_type(await initial.run(snapshot), State | ExitFSM | None)
        assert_type(inputs.snapshot_and_watch(), tuple[Inputs, Event])
        assert_type(snapshot.lease_assignment, LeaseAssignment | None)
        assert_type(snapshot.shutdown, ShutdownRequest | None)
        assert_type(lease.end_session_requested, bool)
        source: InputSource[Inputs] = inputs
        assert_type(FSMRunner(initial, source, name="exporter").current_state, State | ExitFSM)


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
        async def run(self, inputs: Inputs) -> ExitFSM | None:
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
async def test_lease_local_request_wakes_once_without_changing_observations() -> None:
    inputs = InputBuffer()
    inputs.publish_lease_assignment("A")
    lease, replacement = LeaseContext(lease_name="A"), LeaseContext(lease_name="A")
    before, changed = inputs.snapshot_and_watch()
    request_end_session(lease, inputs)
    after, next_change = inputs.snapshot_and_watch()
    assert changed.is_set() and after is before  # Wakes the runner without changing observations.
    assert lease.end_session_requested and not replacement.end_session_requested
    request_end_session(lease, inputs)
    assert not next_change.is_set()


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
async def test_status_stream_latches_takeover_and_support_from_first_status_only() -> None:
    inputs = InputBuffer()
    record = jumpstarter_pb2.LeaseHooks(
        lease_name="A",
        lease_uid="u1",
        client_name="client",
        before_lease=jumpstarter_pb2.LeaseHookState(phase=RUNNING_STATE, attempts=1),
    )
    await inputs.send(jumpstarter_pb2.StatusResponse(lease_hooks=record))
    first, _ = inputs.snapshot_and_watch()
    assert first.status_seen and first.hooks_supported
    assert first.takeover is not None
    assert first.takeover.restart is Restart.CLEAN_UP  # not leased: cleanup is owed either way
    assert (first.takeover.lease_name, first.takeover.lease_uid, first.takeover.client_name) == ("A", "u1", "client")

    # A later record describes this process's own work, not work left owed by a previous one.
    stale = jumpstarter_pb2.LeaseHooks(
        lease_name="B", lease_uid="u2", before_lease=jumpstarter_pb2.LeaseHookState(phase=RUNNING_STATE)
    )
    await inputs.send(jumpstarter_pb2.StatusResponse(leased=True, lease_name="B", lease_uid="u2", lease_hooks=stale))
    second, _ = inputs.snapshot_and_watch()
    assert second.takeover is first.takeover

    # A controller that keeps no records never latches one.
    plain = InputBuffer()
    await plain.send(jumpstarter_pb2.StatusResponse())
    snapshot, _ = plain.snapshot_and_watch()
    assert snapshot.status_seen and not snapshot.hooks_supported and snapshot.takeover is None


@pytest.mark.anyio
async def test_assignments_track_lease_uid_and_generation() -> None:
    inputs = InputBuffer()
    await inputs.send(jumpstarter_pb2.StatusResponse(leased=True, lease_name="A", lease_uid="u1"))
    first, _ = inputs.snapshot_and_watch()
    assert first.lease_assignment is not None and first.lease_assignment.key == LeaseKey("A", 1, "u1")
    # Same lease, new observation: no replacement.
    await inputs.send(
        jumpstarter_pb2.StatusResponse(leased=True, lease_name="A", lease_uid="u1", client_name="client")
    )
    second, _ = inputs.snapshot_and_watch()
    assert second.lease_assignment is not None and second.lease_assignment.key == LeaseKey("A", 1, "u1")
    # A new lease reusing the name, told apart by its UID, is a replacement.
    await inputs.send(jumpstarter_pb2.StatusResponse(leased=True, lease_name="A", lease_uid="u2"))
    third, _ = inputs.snapshot_and_watch()
    assert third.lease_assignment is not None and third.lease_assignment.key == LeaseKey("A", 2, "u2")


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


@pytest.mark.anyio
async def test_input_logs_name_changes_and_wakeups_without_payloads(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="jumpstarter")
    inputs = InputBuffer()
    inputs.wake("manual recheck")
    inputs.publish_lease_assignment("A", "secret-client", {"token": "secret-token"})
    inputs.request_shutdown(ShutdownRequest(ShutdownMode.DRAIN, reason="secret-reason"))
    request_end_session(LeaseContext(lease_name="A"), inputs)
    messages = [record.getMessage() for record in caplog.records if record.name == "jumpstarter.exporter.fsm"]
    assert any(
        message.startswith("FSM inputs changed: lease_assignment=LeaseKey(name='A', generation=1")
        for message in messages
    )
    assert any("shutdown_mode=DRAIN" in message for message in messages)
    assert "FSM wakeup: EndSession" in messages and "FSM wakeup: manual recheck" in messages
    assert "secret-" not in caplog.text
