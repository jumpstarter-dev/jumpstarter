import anyio
import pytest
from anyio import create_task_group

from jumpstarter.exporter.lease_lifecycle import (
    InvalidTransitionError,
    LeaseLifecycle,
    LeasePhase,
)

pytestmark = pytest.mark.anyio


def test_initial_state_created() -> None:
    lc = LeaseLifecycle()
    assert lc.phase == LeasePhase.CREATED
    assert not lc.end_requested
    assert not lc.skip_after_lease


def test_all_valid_transitions_succeed() -> None:
    paths = [
        (
            LeasePhase.CREATED,
            LeasePhase.STARTING,
            LeasePhase.BEFORE_LEASE,
            LeasePhase.READY,
            LeasePhase.ENDING,
            LeasePhase.AFTER_LEASE,
            LeasePhase.RELEASING,
            LeasePhase.DONE,
        ),
        (
            LeasePhase.CREATED,
            LeasePhase.STARTING,
            LeasePhase.READY,
            LeasePhase.ENDING,
            LeasePhase.RELEASING,
            LeasePhase.DONE,
        ),
        (
            LeasePhase.CREATED,
            LeasePhase.FAILED,
        ),
        (
            LeasePhase.CREATED,
            LeasePhase.STARTING,
            LeasePhase.ENDING,
            LeasePhase.DONE,
        ),
        (
            LeasePhase.CREATED,
            LeasePhase.STARTING,
            LeasePhase.BEFORE_LEASE,
            LeasePhase.ENDING,
            LeasePhase.AFTER_LEASE,
            LeasePhase.FAILED,
        ),
    ]
    for sequence in paths:
        lc = LeaseLifecycle()
        for i, target in enumerate(sequence[1:], start=1):
            lc.transition(target)
            assert lc.phase == target, f"step {i} to {target}"


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (LeasePhase.CREATED, LeasePhase.READY),
        (LeasePhase.READY, LeasePhase.STARTING),
        (LeasePhase.DONE, LeasePhase.CREATED),
        (LeasePhase.DONE, LeasePhase.READY),
        (LeasePhase.FAILED, LeasePhase.CREATED),
        (LeasePhase.FAILED, LeasePhase.DONE),
    ],
)
def test_invalid_transitions_raise(current: LeasePhase, target: LeasePhase) -> None:
    lc = LeaseLifecycle()
    if current == LeasePhase.READY:
        lc.transition(LeasePhase.STARTING)
        lc.transition(LeasePhase.READY)
    elif current == LeasePhase.DONE:
        lc.transition(LeasePhase.STARTING)
        lc.transition(LeasePhase.READY)
        lc.transition(LeasePhase.ENDING)
        lc.transition(LeasePhase.RELEASING)
        lc.transition(LeasePhase.DONE)
    elif current == LeasePhase.FAILED:
        lc.transition(LeasePhase.FAILED)

    with pytest.raises(InvalidTransitionError) as exc_info:
        lc.transition(target)
    assert exc_info.value.current == lc.phase


def test_request_end_in_ready_transitions_to_ending() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    lc.request_end()
    assert lc.phase == LeasePhase.ENDING
    assert not lc.end_requested


def test_request_end_in_before_lease_records_intent_only() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.BEFORE_LEASE)
    lc.request_end()
    assert lc.phase == LeasePhase.BEFORE_LEASE
    assert lc.end_requested


def test_request_end_in_starting_records_intent_only() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.request_end()
    assert lc.phase == LeasePhase.STARTING
    assert lc.end_requested


async def test_wait_ready_unblocks_on_ready() -> None:
    lc = LeaseLifecycle()
    seen = []

    async def waiter() -> None:
        await lc.wait_ready()
        seen.append("ready")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.STARTING)
        lc.transition(LeasePhase.READY)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["ready"]


async def test_wait_ready_unblocks_on_done() -> None:
    lc = LeaseLifecycle()
    seen = []

    async def waiter() -> None:
        await lc.wait_ready()
        seen.append("unblocked")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.STARTING)
        lc.transition(LeasePhase.ENDING)
        lc.transition(LeasePhase.RELEASING)
        lc.transition(LeasePhase.DONE)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["unblocked"]


async def test_wait_ready_unblocks_on_failed() -> None:
    lc = LeaseLifecycle()
    seen = []

    async def waiter() -> None:
        await lc.wait_ready()
        seen.append("unblocked")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.FAILED)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["unblocked"]


async def test_wait_complete_unblocks_on_done() -> None:
    lc = LeaseLifecycle()
    seen = []

    async def waiter() -> None:
        await lc.wait_complete()
        seen.append("done")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.STARTING)
        lc.transition(LeasePhase.READY)
        lc.transition(LeasePhase.ENDING)
        lc.transition(LeasePhase.RELEASING)
        lc.transition(LeasePhase.DONE)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["done"]


async def test_wait_complete_unblocks_on_failed() -> None:
    lc = LeaseLifecycle()
    seen = []

    async def waiter() -> None:
        await lc.wait_complete()
        seen.append("failed")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.FAILED)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["failed"]


def test_is_ready_after_ready_transition() -> None:
    lc = LeaseLifecycle()
    assert not lc.is_ready()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    assert lc.is_ready()


def test_is_complete_after_done_transition() -> None:
    lc = LeaseLifecycle()
    assert not lc.is_complete()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    lc.transition(LeasePhase.ENDING)
    lc.transition(LeasePhase.RELEASING)
    lc.transition(LeasePhase.DONE)
    assert lc.is_complete()


def test_drivers_ready_false_early_phases() -> None:
    lc = LeaseLifecycle()
    assert not lc.drivers_ready()
    lc.transition(LeasePhase.STARTING)
    assert not lc.drivers_ready()
    lc.transition(LeasePhase.BEFORE_LEASE)
    assert not lc.drivers_ready()


def test_drivers_ready_true_when_gating_phases() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    assert lc.drivers_ready()
    lc.transition(LeasePhase.ENDING)
    assert lc.drivers_ready()
    lc.transition(LeasePhase.AFTER_LEASE)
    assert lc.drivers_ready()
    lc.transition(LeasePhase.RELEASING)
    assert lc.drivers_ready()


def test_skip_after_lease_default_and_setter() -> None:
    lc = LeaseLifecycle()
    assert lc.skip_after_lease is False
    lc.skip_after_lease = True
    assert lc.skip_after_lease is True


def test_happy_path_full_sequence() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.BEFORE_LEASE)
    lc.transition(LeasePhase.READY)
    lc.transition(LeasePhase.ENDING)
    lc.transition(LeasePhase.AFTER_LEASE)
    lc.transition(LeasePhase.RELEASING)
    lc.transition(LeasePhase.DONE)
    assert lc.phase == LeasePhase.DONE


def test_no_hook_path() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    lc.transition(LeasePhase.ENDING)
    lc.transition(LeasePhase.RELEASING)
    lc.transition(LeasePhase.DONE)
    assert lc.phase == LeasePhase.DONE


def test_early_end_during_before_lease() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.BEFORE_LEASE)
    lc.request_end()
    assert lc.end_requested
    assert lc.is_end_requested()
    assert lc.phase == LeasePhase.BEFORE_LEASE
    lc.transition(LeasePhase.ENDING)
    assert lc.phase == LeasePhase.ENDING


def test_is_end_requested_false_initially() -> None:
    lc = LeaseLifecycle()
    assert not lc.is_end_requested()


def test_is_end_requested_after_request_end_in_ready() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.READY)
    lc.request_end()
    assert lc.is_end_requested()
    assert lc.phase == LeasePhase.ENDING


def test_is_end_requested_after_transition_to_ending() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.ENDING)
    assert lc.is_end_requested()


async def test_wait_end_requested_unblocks_on_request_end() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    lc.transition(LeasePhase.BEFORE_LEASE)
    seen = []

    async def waiter() -> None:
        await lc.wait_end_requested()
        seen.append("end_requested")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.request_end()

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["end_requested"]


async def test_wait_end_requested_unblocks_on_ending_transition() -> None:
    lc = LeaseLifecycle()
    lc.transition(LeasePhase.STARTING)
    seen = []

    async def waiter() -> None:
        await lc.wait_end_requested()
        seen.append("ending")

    async def actor() -> None:
        await anyio.sleep(0)
        lc.transition(LeasePhase.ENDING)

    async with create_task_group() as tg:
        tg.start_soon(waiter)
        tg.start_soon(actor)

    assert seen == ["ending"]
