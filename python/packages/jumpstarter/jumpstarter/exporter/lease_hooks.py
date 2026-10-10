"""What a lease's hooks still need, from the controller's hook record.

The controller keeps a record of the hooks of each exporter's latest lease
(Exporter.status.leaseHooks), so the lease can outlive an exporter process.
After a restart, decide_restart reads the record against the lease assigned now
and says what that lease still needs. HookPlan keeps, per lease, how this
process runs its hooks and what the record shows of them.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Literal

from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus, LeaseHookPhase
from jumpstarter.config.exporter import MAX_HOOK_ATTEMPTS

logger = logging.getLogger(__name__)

HookType = Literal["before_lease", "after_lease"]
HOOK_NAMES: dict[HookType, str] = {"before_lease": "beforeLease", "after_lease": "afterLease"}
FINISHED_HOOK_PHASES = frozenset({LeaseHookPhase.SUCCEEDED, LeaseHookPhase.FAILED, LeaseHookPhase.SKIPPED})
# A setup that failed with one of these does not get cleanup, as without a restart.
_CLEANUP_SKIPPING_FAILURE_ACTIONS = frozenset({
    jumpstarter_pb2.LEASE_HOOK_FAILURE_ACTION_END_LEASE,
    jumpstarter_pb2.LEASE_HOOK_FAILURE_ACTION_EXIT,
})


class Restart(Enum):
    """What a lease's hooks still need after an exporter restart."""

    NEW = auto()  # no record of this lease: set it up as a new lease
    RESUME = auto()  # its setup ran: resume it without running setup again
    FINISH_SETUP = auto()  # its setup was cut off: run it again or fail it, per onInterrupt
    CLEAN_UP = auto()  # it owes cleanup (it ended, or cleanup was cut off): run it
    SETTLE = auto()  # no hook left to run: close the record, release on an endLease failure
    NOTHING = auto()  # nothing owed: no record, or a lease done with its hooks that has ended


def decide_restart(record: jumpstarter_pb2.LeaseHooks, assigned: tuple[str, str] | None) -> Restart:
    """Decide what the lease in the record needs, given the lease assigned now (name, UID).

    A record of a lease other than the assigned one is stale: hooks of that lease
    must not touch a device handed to its replacement, so the assigned lease is
    set up as new. A phase this exporter does not know reads as not finished.
    """
    if not record.HasField("before_lease") or (
        assigned is not None and assigned != (record.lease_name, record.lease_uid)
    ):
        return Restart.NEW if assigned is not None else Restart.NOTHING
    before = record.before_lease
    after = record.after_lease if record.HasField("after_lease") else None
    if after is not None and after.phase in FINISHED_HOOK_PHASES:
        return Restart.SETTLE if assigned is not None else Restart.NOTHING
    if before.phase == LeaseHookPhase.FAILED and before.on_failure in _CLEANUP_SKIPPING_FAILURE_ACTIONS:
        return Restart.SETTLE
    if after is not None:
        return Restart.CLEAN_UP  # cleanup was cut off
    if assigned is not None:
        return Restart.RESUME if before.phase in FINISHED_HOOK_PHASES else Restart.FINISH_SETUP
    return Restart.CLEAN_UP  # the lease ended while no process served it


@dataclass
class HookPlan:
    """How this exporter process runs a lease's hooks, and what the controller's record shows.

    Attributes:
        tracked: hook transitions are recorded with the controller, so the lease can
            outlive this process (see Exporter._record_lease_hook)
        recorded: the controller holds a hook record for this lease
        resumed: the status to report for a lease whose setup ran in an earlier
            process; None if setup runs in this one
        attempts: the attempt each hook runs (or fails) as; 1 unless a restart cut it off
        started: the attempt the record shows as started, per hook
        cut_off: failure message per hook a restart cut off that fails instead of running again
    """

    tracked: bool = False
    recorded: bool = False
    resumed: tuple[ExporterStatus, str] | None = None
    attempts: dict[HookType, int] = field(default_factory=dict)
    started: dict[HookType, int] = field(default_factory=dict)
    cut_off: dict[HookType, str] = field(default_factory=dict)

    def attempt(self, hook: HookType) -> int:
        """The attempt the hook runs (or fails) as."""
        return self.attempts.get(hook, 1)

    def resume(self, before: jumpstarter_pb2.LeaseHookState) -> None:
        """Resume a lease whose setup ran: report what it reported before the restart."""
        if before.phase != LeaseHookPhase.FAILED:
            self.resumed = (ExporterStatus.LEASE_READY, "Ready for commands (lease resumed after exporter restart)")
        elif before.on_failure == jumpstarter_pb2.LEASE_HOOK_FAILURE_ACTION_WARN:
            warning = f"{HOOK_WARNING_PREFIX}beforeLease hook warning: {before.message}"
            self.resumed = (ExporterStatus.LEASE_READY, warning)
        else:
            self.resumed = (ExporterStatus.BEFORE_LEASE_HOOK_FAILED, f"beforeLease hook failed: {before.message}")

    def finish_cut_off(
        self, hook: HookType, state: jumpstarter_pb2.LeaseHookState, on_interrupt: str | None, lease_name: str
    ) -> None:
        """Plan how to finish a hook an exporter restart cut off, per its onInterrupt.

        'rerun' runs it again as the next attempt, up to MAX_HOOK_ATTEMPTS; after
        that, with 'fail', or with no such hook configured, the hook fails without
        running and its onFailure applies.
        """
        attempts = max(state.attempts, 1)
        self.started[hook] = attempts
        name = HOOK_NAMES[hook]
        if on_interrupt == "rerun" and attempts < MAX_HOOK_ATTEMPTS:
            self.attempts[hook] = attempts + 1
            logger.warning(
                "%s hook of lease %s was cut off by an exporter restart; running it again (attempt %d of %d)",
                name, lease_name, attempts + 1, MAX_HOOK_ATTEMPTS,
            )
            return
        self.attempts[hook] = attempts
        cause = "an exporter restart cut it off" if attempts == 1 else f"exporter restarts cut it off {attempts} times"
        self.cut_off[hook] = f"{name} hook did not finish: {cause}"
        logger.warning("%s hook of lease %s did not finish: %s", name, lease_name, cause)
