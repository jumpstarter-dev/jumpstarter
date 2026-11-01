"""Decide what a lease's hooks still need after an exporter restart, from its record."""

import pytest
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus, LeaseHookPhase
from jumpstarter.config.exporter import MAX_HOOK_ATTEMPTS
from jumpstarter.exporter.lease_hooks import (
    HOOK_FAILURE_ACTIONS,
    HookPlan,
    Restart,
    decide_restart,
    hook_failure_ended_lease,
)

WARN = HOOK_FAILURE_ACTIONS["warn"]
END_LEASE = HOOK_FAILURE_ACTIONS["endLease"]
EXIT = HOOK_FAILURE_ACTIONS["exit"]


def hook_state(phase, on_failure=0, message="", attempts=1):
    return jumpstarter_pb2.LeaseHookState(
        phase=jumpstarter_pb2.LeaseHookPhase.ValueType(phase),
        on_failure=jumpstarter_pb2.LeaseHookFailureAction.ValueType(on_failure),
        message=message,
        attempts=attempts,
    )


def record(before=None, after=None, lease_name="A", lease_uid="u1", client_name="client"):
    return jumpstarter_pb2.LeaseHooks(
        lease_name=lease_name,
        lease_uid=lease_uid,
        client_name=client_name,
        **({"before_lease": before} if before is not None else {}),
        **({"after_lease": after} if after is not None else {}),
    )


SUCCEEDED = hook_state(LeaseHookPhase.SUCCEEDED)
RUNNING = hook_state(LeaseHookPhase.RUNNING)
FAILED_WARN = hook_state(LeaseHookPhase.FAILED, WARN, "boom")
FAILED_END_LEASE = hook_state(LeaseHookPhase.FAILED, END_LEASE, "boom")
FAILED_EXIT = hook_state(LeaseHookPhase.FAILED, EXIT, "boom")

ASSIGNED = ("A", "u1")
OTHER_LEASE = ("B", "u2")


@pytest.mark.parametrize(
    ("before", "after", "assigned", "expected"),
    [
        (None, None, ASSIGNED, Restart.NEW),
        (None, None, None, Restart.NOTHING),
        (None, SUCCEEDED, ASSIGNED, Restart.NEW),
        # A record of a lease other than the assigned one is stale.
        (SUCCEEDED, None, OTHER_LEASE, Restart.NEW),
        (RUNNING, None, OTHER_LEASE, Restart.NEW),
        (SUCCEEDED, None, ASSIGNED, Restart.RESUME),
        (RUNNING, None, ASSIGNED, Restart.FINISH_SETUP),
        (FAILED_WARN, None, ASSIGNED, Restart.RESUME),
        # A failed setup that skips cleanup, or one done with its hooks, settles.
        (FAILED_END_LEASE, None, ASSIGNED, Restart.SETTLE),
        (FAILED_EXIT, None, ASSIGNED, Restart.SETTLE),
        (SUCCEEDED, SUCCEEDED, ASSIGNED, Restart.SETTLE),
        (SUCCEEDED, SUCCEEDED, None, Restart.NOTHING),
        (FAILED_END_LEASE, SUCCEEDED, ASSIGNED, Restart.SETTLE),
        # Cleanup was cut off, or the lease ended while no process served it.
        (SUCCEEDED, RUNNING, ASSIGNED, Restart.CLEAN_UP),
        (RUNNING, RUNNING, ASSIGNED, Restart.CLEAN_UP),
        (SUCCEEDED, None, None, Restart.CLEAN_UP),
        (RUNNING, None, None, Restart.CLEAN_UP),
        (SUCCEEDED, RUNNING, None, Restart.CLEAN_UP),
    ],
)
def test_decide_restart(before, after, assigned, expected):
    assert decide_restart(record(before, after), assigned) is expected


@pytest.mark.parametrize("on_failure", [WARN, END_LEASE, EXIT, 0])
@pytest.mark.parametrize("phase", list(LeaseHookPhase))
def test_hook_failure_ended_lease_only_for_end_lease(phase, on_failure):
    state = hook_state(phase, on_failure)
    assert hook_failure_ended_lease(state) is (phase == LeaseHookPhase.FAILED and on_failure == END_LEASE)
    assert hook_failure_ended_lease(None) is False


def test_resume_reports_what_the_setup_reported():
    plan = HookPlan()
    plan.resume(SUCCEEDED)
    assert plan.resumed == (ExporterStatus.LEASE_READY, "Ready for commands (lease resumed after exporter restart)")
    plan.resume(FAILED_WARN)
    assert plan.resumed == (ExporterStatus.LEASE_READY, f"{HOOK_WARNING_PREFIX}beforeLease hook warning: boom")
    plan.resume(FAILED_END_LEASE)
    assert plan.resumed == (ExporterStatus.BEFORE_LEASE_HOOK_FAILED, "beforeLease hook failed: boom")


@pytest.mark.parametrize("on_interrupt", ["rerun", "fail", None])
def test_finish_cut_off_per_on_interrupt(on_interrupt):
    plan = HookPlan()
    plan.finish_cut_off("before_lease", hook_state(LeaseHookPhase.RUNNING, attempts=1), on_interrupt, "A")
    if on_interrupt == "rerun":
        assert plan.attempts["before_lease"] == 2 and plan.cut_off == {}
    else:
        assert plan.attempts["before_lease"] == 1
        assert plan.cut_off["before_lease"] == "beforeLease hook did not finish: an exporter restart cut it off"


def test_finish_cut_off_rerun_gives_up_at_max_attempts():
    plan = HookPlan()
    plan.finish_cut_off(
        "after_lease", hook_state(LeaseHookPhase.RUNNING, attempts=MAX_HOOK_ATTEMPTS), "rerun", "A"
    )
    assert plan.attempts["after_lease"] == MAX_HOOK_ATTEMPTS
    assert plan.cut_off["after_lease"] == (
        f"afterLease hook did not finish: exporter restarts cut it off {MAX_HOOK_ATTEMPTS} times"
    )
