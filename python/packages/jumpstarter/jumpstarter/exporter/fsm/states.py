"""Production exporter lifecycle states for controller and standalone modes.

Each state class docstring is the canonical description of that phase. Keep those
docstrings aligned with JEP-0016 and any exported lifecycle diagrams when
transitions or policies change (see the exporter-fsm-docs-sync Cursor rule).
"""

from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING

from anyio import CancelScope, sleep
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus, LeaseHookPhase
from jumpstarter.exporter.fsm.input import Inputs, LeaseKey, ShutdownMode, Takeover
from jumpstarter.exporter.fsm.lease_context import LeaseContext
from jumpstarter.exporter.hooks import HookExecutionError
from jumpstarter.exporter.lease_hooks import (
    FINISHED_HOOK_PHASES,
    HookPlan,
    Restart,
    hook_failure_ended_lease,
    resumed_ready_message,
)
from jumpstarter.fsm import ExitFSM
from jumpstarter.logging import clear_log_context, set_log_context

if TYPE_CHECKING:
    from jumpstarter.exporter.exporter import Exporter
    from jumpstarter.exporter.session import Session

logger = logging.getLogger("jumpstarter.exporter.fsm")


@dataclass
class Starting:
    """Registered; wait for the first status before admitting or taking over a lease.

    Waiting state: honor shutdown, then return ``None`` until the Status stream
    has delivered its first observation. That observation carries the
    controller's hook record, so the exporter knows whether a previous process
    left hook work owed: go to ``RecoveringLease`` if it did and this exporter
    records its hooks, otherwise to ``Available``.
    """

    exporter: Exporter

    async def run(self, inputs: Inputs) -> Available | RecoveringLease | Stopping | None:
        if inputs.shutdown is not None:
            return Stopping(self.exporter)
        if not inputs.status_seen:
            # AVAILABLE is reported only once the exporter knows it owes no hook
            # work from a previous process: the record arrives with the first status.
            return None
        if inputs.takeover is not None and self.exporter.records_lease_hooks:
            return RecoveringLease(self.exporter, inputs.takeover)
        return Available(self.exporter)


@dataclass
class RecoveringLease:
    """Take over the lease a previous exporter process left, per the controller's hook record.

    Activity state: the record says what the lease still needs. A lease that
    needs no more hooks is settled here, releasing it if a hook had failed with
    ``endLease``, and goes to ``Available``, as does a record with nothing owed.
    Any other lease goes to ``Leased``, which opens a session for it and starts
    its states where the record left them: ``BeforeLeaseHook`` to finish a setup
    the restart cut off, ``LeaseReady`` or ``BeforeLeaseHookFailed`` to resume a
    lease whose setup had ended, ``AfterLeaseHook`` for a lease that owes cleanup.
    """

    exporter: Exporter
    takeover: Takeover

    async def run(self, inputs: Inputs) -> Available | Leased | Stopping:
        exporter, takeover = self.exporter, self.takeover
        if inputs.shutdown is not None:
            return Stopping(exporter)
        restart, before, after = takeover.restart, takeover.before, takeover.after
        if restart in (Restart.NEW, Restart.NOTHING):
            if restart is Restart.NEW and before is not None:
                logger.info(
                    "Not finishing hooks of lease %s (uid %s): lease %s is assigned now",
                    takeover.lease_name,
                    takeover.lease_uid,
                    inputs.lease_assignment.key.name if inputs.lease_assignment is not None else "",
                )
            return Available(exporter)
        key = (
            inputs.lease_assignment.key
            if takeover.assigned and inputs.lease_assignment is not None
            else LeaseKey(takeover.lease_name, 1, takeover.lease_uid)
        )
        lease = LeaseContext(
            lease_name=takeover.lease_name,
            client_name=takeover.client_name,  # kept by the record, as the lease may have ended
            wake=exporter.fsm.inputs.wake,
            lease_uid=takeover.lease_uid,
            hooks=HookPlan(tracked=True, recorded=True),
        )
        set_log_context(lease_id=lease.lease_name, exporter=exporter.exporter_name)
        set_log_context(client=lease.client_name)
        if restart is Restart.SETTLE:
            return await self._settle(key, lease, before, after)
        if restart is Restart.FINISH_SETUP:
            assert before is not None
            logger.info("Resuming lease %s after exporter restart (beforeLease hook did not finish)", lease.lease_name)
            lease.hooks.finish_cut_off("before_lease", before, exporter.on_interrupt("before_lease"), lease.lease_name)
            return Leased(BeforeLeaseHook(exporter, key, lease))
        if restart is Restart.RESUME:
            assert before is not None
            return self._resume(key, lease, before)
        return await self._clean_up(key, lease, before, after)

    def _resume(self, key: LeaseKey, lease: LeaseContext, before: jumpstarter_pb2.LeaseHookState) -> Leased:
        """Resume a lease whose setup ran: it reports what it reported before the restart."""
        logger.info(
            "Resuming lease %s after exporter restart (beforeLease hook %s before it)",
            lease.lease_name,
            LeaseHookPhase(before.phase).name.lower(),
        )
        message = resumed_ready_message(before)
        if message is None:
            error = RuntimeError(before.message or "beforeLease hook failed")
            return Leased(BeforeLeaseHookFailed(self.exporter, key, lease, error), resumed=True)
        return Leased(LeaseReady(self.exporter, key, lease, message), resumed=True)

    async def _clean_up(
        self,
        key: LeaseKey,
        lease: LeaseContext,
        before: jumpstarter_pb2.LeaseHookState | None,
        after: jumpstarter_pb2.LeaseHookState | None,
    ) -> Leased:
        """Run the afterLease hook a lease owes, first thing, without setting it up again."""
        exporter = self.exporter
        if self.takeover.assigned:
            logger.info("Lease %s owes its afterLease hook; running it", lease.lease_name)
            lease.end_session_requested = True
        else:
            logger.info("Lease %s ended while the exporter was down; running its afterLease hook", lease.lease_name)
        if before is not None and before.phase not in FINISHED_HOOK_PHASES:
            # The lease ended before the cut-off setup could run again.
            await exporter.record_lease_hook(
                lease,
                "before_lease",
                LeaseHookPhase.FAILED,
                "beforeLease hook did not finish: the lease ended while the exporter was restarting",
                attempts=max(before.attempts, 1),
            )
        if after is not None:
            lease.hooks.finish_cut_off("after_lease", after, exporter.on_interrupt("after_lease"), lease.lease_name)
        # The lease is in cleanup, so no client dials to serve.
        return Leased(AfterLeaseHook(exporter, key, lease), resumed=True, listen=False)

    async def _settle(
        self,
        key: LeaseKey,
        lease: LeaseContext,
        before: jumpstarter_pb2.LeaseHookState | None,
        after: jumpstarter_pb2.LeaseHookState | None,
    ) -> Available:
        """Settle a recorded lease that needs no more hooks."""
        exporter = self.exporter
        if after is None:
            await exporter.record_lease_hook(lease, "after_lease", LeaseHookPhase.SKIPPED, "beforeLease hook failed")
        if not self.takeover.assigned:
            return Available(exporter)
        # Never set it up again; ignore its leased ticks until the lease ends.
        if hook_failure_ended_lease(before) or hook_failure_ended_lease(after):
            # As the previous process would have done for a hook that failed with onFailure: endLease.
            logger.info("Lease %s had a hook fail with onFailure: endLease; releasing it", lease.lease_name)
            await exporter.request_lease_release(lease, key)
        else:
            logger.info("Lease %s is done with its hooks; leaving it to its client", lease.lease_name)
        return Available(exporter, last_completed=key)


@dataclass
class Available:
    """Admit a new lease or honor exporter shutdown.

    Waiting state: honor an outstanding shutdown before admission. Reject the
    last completed assignment key so a stale observation cannot reopen the same
    lease. Otherwise admit the current assignment and transition to ``Leased``,
    which opens its session. Return ``None`` when there is nothing admissible.
    """

    exporter: Exporter
    last_completed: LeaseKey | None = None
    # Standalone TCP injects its session here; controller mode leaves this None
    # and Leased falls back to exporter.session_for_lease().
    session_context: AbstractAsyncContextManager[tuple[Session, str, str]] | None = None

    async def run(self, inputs: Inputs) -> Leased | Stopping | None:
        # Drain/stop-now already requested: never admit another lease.
        if inputs.shutdown:
            return Stopping(self.exporter)
        if self.exporter.exporter_status != ExporterStatus.AVAILABLE:
            await self.exporter.report_status(ExporterStatus.AVAILABLE, "Available for new lease")
        lease_assignment = inputs.lease_assignment
        # Skip a stale snapshot of the lease we just tore down.
        if not lease_assignment or lease_assignment.key == self.last_completed:
            return None

        lease = LeaseContext(
            lease_name=lease_assignment.key.name,
            client_name=lease_assignment.client_name,
            wake=self.exporter.fsm.inputs.wake,
            lease_uid=lease_assignment.key.uid,
            # Hook records identify a lease by its UID, which every controller that keeps them sends.
            hooks=HookPlan(
                tracked=inputs.hooks_supported and self.exporter.records_lease_hooks and bool(lease_assignment.key.uid)
            ),
        )
        set_log_context(
            **{"lease_id": lease.lease_name, "exporter": self.exporter.exporter_name, **lease_assignment.context}
        )
        set_log_context(client=lease.client_name)
        logger.info("Starting new lease: %s", lease.lease_name)
        return Leased(BeforeLeaseHook(self.exporter, lease_assignment.key, lease), session_context=self.session_context)


@dataclass
class _LeaseState:
    """Fields shared by the states that run inside ``Leased``."""

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext


@dataclass
class Leased:
    """Own one lease's session, sockets, drivers, and I/O for as long as it runs.

    Composite state: open the session, and in controller mode the lease's
    Listen/dial tasks, then run the lease's own states as a nested machine
    inside that ``async with`` block, from ``first`` until one of them returns
    ``ExitFSM``. Leaving the block stops and joins lease I/O before closing the
    session, sockets, and drivers, whether the lease's states exited, failed, or
    were cancelled.

    A new lease starts at ``BeforeLeaseHook``. A lease taken over after an
    exporter restart starts where its hook record left it; one whose setup does
    not run again is ``resumed``, so its device is not reset, and one that only
    owes cleanup has no client to serve, so it does not ``listen``; neither does
    a lease that ended while its session was being set up.

    A failed close propagates and does not free the admission slot. Clear
    lease-local log context, then return ``Available(last_completed=key)`` to
    continue serving, or ``Stopping`` for exit-on-lease-end and standalone mode.
    """

    first: BeforeLeaseHook | LeaseReady | BeforeLeaseHookFailed | AfterLeaseHook
    resumed: bool = False
    listen: bool = True
    session_context: AbstractAsyncContextManager[tuple[Session, str, str]] | None = None

    @property
    def lease(self) -> LeaseContext:
        return self.first.lease

    async def run(self, inputs: Inputs) -> Available | Stopping:
        exporter, key, lease = self.first.exporter, self.first.key, self.first.lease
        session_context = self.session_context or exporter.session_for_lease(reset_device=not self.resumed)
        async with session_context as (session, main_path, hook_path):
            lease.session, lease.socket_path, lease.hook_socket_path = session, main_path, hook_path
            session.lease_context = lease
            if not self.resumed:
                # Remote dials stay buffered behind this status until setup has an outcome.
                session.update_status(ExporterStatus.BEFORE_LEASE_HOOK, "Preparing lease")
            # Controller mode: Listen/dial tasks for this lease, closed before its session.
            # Not for a lease that ended during session setup: this state's snapshot predates it.
            latest = exporter.fsm.inputs.snapshot()
            ended = _released(latest, key) or _stop_now(latest)
            serve_clients = self.listen and not exporter.standalone and not ended
            async with exporter.lease_connections(lease) if serve_clients else nullcontext():
                await exporter.fsm.run_nested(self.first)
        clear_log_context()
        set_log_context(exporter=exporter.exporter_name)
        if exporter.exit_on_lease_end and not exporter.standalone:
            exporter.stop(should_unregister=True)
        # One-shot / standalone sessions exit after this lease.
        if exporter.exit_on_lease_end or exporter.standalone:
            return Stopping(exporter)
        # Available gets a fresh snapshot: it honors shutdown before admitting any replacement.
        return Available(exporter, last_completed=key)


@dataclass
class BeforeLeaseHook(_LeaseState):
    """Run the before-lease hook for an admitted assignment.

    Activity state: entered with the session open. Session setup awaits, so
    check the latest assignment first: if it was revoked or stop-now applies,
    the setup does not start and its record is closed through ``AfterLeaseHook``.
    Otherwise record the hook's start with the controller, which can refuse it
    (``ExitFSM``), await the execution-only before-lease hook under a shielded
    cancel scope, record its outcome, and select ``LeaseReady`` or
    ``BeforeLeaseHookFailed`` from it.
    """

    async def run(self, inputs: Inputs) -> LeaseReady | BeforeLeaseHookFailed | AfterLeaseHook | ExitFSM:
        exporter, lease = self.exporter, self.lease
        if _released(inputs, self.key) or _stop_now(inputs):
            # Close a setup that will not run; cleanup runs only if its start was recorded.
            await exporter.setup_did_not_start(lease)
            return AfterLeaseHook(exporter, self.key, lease)
        _update_client(inputs, self.key, lease)
        executor = exporter.hook_executor
        warning = None
        try:
            if executor is None or executor.config.before_lease is None:
                # No setup hook: the record must still show the lease as set up.
                if not await exporter.record_lease_hook(lease, "before_lease", LeaseHookPhase.SKIPPED):
                    return ExitFSM()
            else:
                # The record's retries stop by themselves once this lease ends.
                if not await exporter.record_start(lease, "before_lease"):
                    await exporter.setup_did_not_start(lease)
                    return ExitFSM()
                await exporter.report_status(ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook", lease=lease)
                # Ordinary stop/release updates are inputs. External cancellation also
                # leaves this bounded hook time to finish before its session is closed.
                with CancelScope(shield=True):
                    warning = await executor.execute_before_lease_hook(lease)
                await exporter.record_outcome(lease, "before_lease", warning)
        except HookExecutionError as error:
            await exporter.record_lease_hook(lease, "before_lease", LeaseHookPhase.FAILED, str(error), error.on_failure)
            return BeforeLeaseHookFailed(exporter, self.key, lease, error)
        except Exception as error:  # noqa: BLE001 - failure policy is the successor state's
            await exporter.record_lease_hook(lease, "before_lease", LeaseHookPhase.FAILED, str(error))
            return BeforeLeaseHookFailed(exporter, self.key, lease, error)
        if warning:
            return LeaseReady(exporter, self.key, lease, f"{HOOK_WARNING_PREFIX}beforeLease hook warning: {warning}")
        return LeaseReady(exporter, self.key, lease)


@dataclass
class LeaseReady(_LeaseState):
    """Serve an active lease until the session needs finishing.

    Waiting state: inspect shutdown mode, revocation/reassignment, and this
    lease's EndSession request. Report ``LEASE_READY`` with ``message`` (a
    before-hook warning, or that the lease was resumed) while normal service
    continues and return ``None``. Transition to ``AfterLeaseHook`` when the
    session must finish. On stop-now, a lease whose hooks the controller tracks
    is left in place for the next exporter process instead (``ExitFSM``).
    """

    message: str = "Ready for commands"

    async def run(self, inputs: Inputs) -> AfterLeaseHook | ExitFSM | None:
        _update_client(inputs, self.key, self.lease)
        if _released(inputs, self.key) or self.lease.end_session_requested:
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        if _stop_now(inputs):
            if _hand_off(self.exporter, inputs, self.key, self.lease):
                return ExitFSM()
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        # Still serving: publish LEASE_READY once.
        if self.exporter.exporter_status != ExporterStatus.LEASE_READY:
            await self.exporter.report_status(ExporterStatus.LEASE_READY, self.message, lease=self.lease)
        return None


@dataclass
class BeforeLeaseHookFailed(_LeaseState):
    """Hold a failed before-lease hook outcome until cleanup policy applies.

    Waiting/policy state: publish ``BEFORE_LEASE_HOOK_FAILED``. Explicit
    ``HookExecutionError`` outcomes never run the after-hook: ``endLease`` goes
    to ``ReleasingLease``; ``exit`` requests drain shutdown and waits in
    ``AwaitingLeaseRelease``. Unexpected failures retain the failed status until
    release, stop-now, or EndSession requires ``AfterLeaseHook`` cleanup. On
    stop-now, a lease whose hooks the controller tracks is left in place for the
    next exporter process instead (``ExitFSM``).
    """

    error: Exception

    async def run(self, inputs: Inputs) -> ReleasingLease | AwaitingLeaseRelease | AfterLeaseHook | ExitFSM | None:
        if self.exporter.exporter_status != ExporterStatus.BEFORE_LEASE_HOOK_FAILED:
            await self.exporter.report_status(
                ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
                f"beforeLease hook failed: {self.error}",
                lease=self.lease,
            )
        if isinstance(self.error, HookExecutionError):
            # Explicit before-hook failures never run the after-hook.
            if self.error.should_end_lease():
                # A lease whose setup failed with endLease owes no cleanup: close
                # its record, or the controller holds this exporter for it.
                await self.exporter.record_lease_hook(
                    self.lease, "after_lease", LeaseHookPhase.SKIPPED, "beforeLease hook failed"
                )
                return ReleasingLease(self.exporter, self.key, self.lease)
            # on_failure=exit: drain shutdown, keep reservation until controller releases.
            self.exporter.stop(wait_for_lease_exit=True, should_unregister=True, exit_code=1)
            return AwaitingLeaseRelease(self.exporter, self.key, self.lease)
        # Unexpected failures retain their failed status until the lease needs cleanup.
        if _released(inputs, self.key) or self.lease.end_session_requested:
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        if _stop_now(inputs):
            if _hand_off(self.exporter, inputs, self.key, self.lease):
                return ExitFSM()
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        return None


@dataclass
class AfterLeaseHook(_LeaseState):
    """Run the after-lease cleanup hook once for this assignment.

    Activity state: record the hook's start with the controller, await the
    execution-only after-lease hook under a shielded cancel scope, record its
    outcome, then select ``AwaitingLeaseRelease`` or ``AfterLeaseHookFailed``.
    The hook is recorded as skipped, and does not run, when none is configured
    or the lease's setup never ran; it does not run either if the controller
    refuses its start. Reports ``AVAILABLE`` after a successful (or
    warning-only) cleanup; reservation retention still belongs to the successor.
    """

    async def run(self, inputs: Inputs) -> AwaitingLeaseRelease | AfterLeaseHookFailed:
        _update_client(inputs, self.key, self.lease)
        exporter, lease = self.exporter, self.lease
        executor = exporter.hook_executor
        warning = None
        reason = "beforeLease hook failed" if lease.skip_after_lease_hook else None
        try:
            if reason is None and (executor is None or executor.config.after_lease is None):
                reason = "no afterLease hook configured"
            if reason is not None:
                # Close the lease's record, so it owes no cleanup.
                await exporter.record_lease_hook(lease, "after_lease", LeaseHookPhase.SKIPPED, reason)
            else:
                await exporter.report_status(ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook", lease=lease)
                if not await exporter.record_start(lease, "after_lease"):
                    # Another lease has the board now, or the next process runs the
                    # hook; this one must not touch the board.
                    logger.warning(
                        "Not running afterLease hook for lease %s: its start was not recorded", lease.lease_name
                    )
                else:
                    assert executor is not None
                    # Ordinary stop/release updates are inputs. External cancellation also
                    # leaves this bounded hook time to finish before its session is closed.
                    with CancelScope(shield=True):
                        warning = await executor.execute_after_lease_hook(lease)
                    await exporter.record_outcome(lease, "after_lease", warning)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return AfterLeaseHookFailed(exporter, self.key, lease, error)
        # Session is done; AVAILABLE here does not free the controller reservation yet.
        message = f"{HOOK_WARNING_PREFIX}afterLease hook warning: {warning}" if warning else "Available for new lease"
        if inputs.shutdown is None:
            await exporter.report_status(ExporterStatus.AVAILABLE, message, lease=lease)
        return AwaitingLeaseRelease(exporter, self.key, lease)


@dataclass
class AfterLeaseHookFailed(_LeaseState):
    """Apply after-lease hook failure policy, then wait for release or close.

    Activity/policy state: publish ``AFTER_LEASE_HOOK_FAILED``. Explicit
    ``endLease`` failures go through ``ReleasingLease``; ``exit`` requests drain
    shutdown. All paths then reach ``AwaitingLeaseRelease`` (or release then
    exit) so reservation and transport teardown stay ordered.
    """

    error: Exception

    async def run(self, inputs: Inputs) -> ReleasingLease | AwaitingLeaseRelease:
        await self.exporter.report_status(
            ExporterStatus.AFTER_LEASE_HOOK_FAILED,
            f"afterLease hook failed: {self.error}",
            lease=self.lease,
        )
        if isinstance(self.error, HookExecutionError):
            if self.error.should_end_lease():
                return ReleasingLease(self.exporter, self.key, self.lease)
            # on_failure=exit: request drain; still wait for release before closing.
            self.exporter.stop(wait_for_lease_exit=True, should_unregister=True, exit_code=1)
        # Unexpected failures still retain reservation until release/stop-now.
        return AwaitingLeaseRelease(self.exporter, self.key, self.lease)


@dataclass
class ReleasingLease(_LeaseState):
    """Request controller release after an explicit ``endLease`` hook failure.

    Activity state: reached only for ``endLease`` hook failures. In standalone
    mode the single failed session is retained until stop
    (``AwaitingLeaseRelease``). Otherwise preserve the one-second
    failure-observation window for polling clients, attempt a named release when
    still assigned, then return ``ExitFSM`` so ``Leased`` closes the lease.
    Ordinary cleanup never enters this state.
    """

    async def run(self, inputs: Inputs) -> ExitFSM | AwaitingLeaseRelease:
        if self.exporter.standalone:
            # Direct mode retains its single failed session until the exporter stops.
            return AwaitingLeaseRelease(self.exporter, self.key, self.lease)
        if not _released(inputs, self.key) and not _stop_now(inputs):
            # Preserve the existing observation window for clients polling failed status.
            await sleep(1)
            # Named ReleaseLease so a delayed retry cannot target a replacement.
            await self.exporter.request_lease_release(self.lease, self.key)
        return ExitFSM()


@dataclass
class AwaitingLeaseRelease(_LeaseState):
    """Retain reservation and final-log access after hooks complete.

    Waiting state: may report ``AVAILABLE`` while still holding the controller
    reservation—only ``Available`` admits another lease. Return ``None`` until
    controller release/reassignment or stop-now; return ``ExitFSM`` immediately
    if one already applies, so ``Leased`` closes the lease. EndSession (and
    drain alone) does not release the reservation.
    """

    async def run(self, inputs: Inputs) -> ExitFSM | None:
        _update_client(inputs, self.key, self.lease)
        # Only controller release/reassignment or stop-now frees the reservation.
        if _released(inputs, self.key) or _stop_now(inputs):
            return ExitFSM()
        # EndSession (and drain alone) does not release the controller reservation.
        return None


@dataclass
class Stopping:
    """Finish exporter shutdown and terminate the lifecycle runner.

    Activity state: publish offline status and complete applicable controller
    unregistration, telemetry, and runtime-sidecar shutdown via
    ``Exporter.finish_shutdown``, then return ``ExitFSM()``.
    """

    exporter: Exporter

    async def run(self, inputs: Inputs) -> ExitFSM:
        # Offline status, unregister, telemetry, and sidecar cleanup live on Exporter.
        await self.exporter.finish_shutdown()
        return ExitFSM()


def _stop_now(inputs: Inputs) -> bool:
    return bool(inputs.shutdown) and inputs.shutdown.mode == ShutdownMode.STOP_NOW


def _released(inputs: Inputs, key: LeaseKey) -> bool:
    return not inputs.lease_assignment or inputs.lease_assignment.key != key


def _update_client(inputs: Inputs, key: LeaseKey, lease: LeaseContext) -> None:
    if inputs.lease_assignment and inputs.lease_assignment.key == key:
        lease.client_name = inputs.lease_assignment.client_name
        set_log_context(client=lease.client_name)


def _hand_off(exporter: Exporter, inputs: Inputs, key: LeaseKey, lease: LeaseContext) -> bool:
    """A dying process leaves a live lease to the next one instead of ending it.

    With the lease's hooks tracked by the controller, the next process resumes
    it, or runs its afterLease hook if it ended meanwhile. A dying process
    never cleans up a live lease, since it may not be able to report the
    outcome.
    """
    if not (lease.hooks.tracked and not _released(inputs, key) and not lease.end_session_requested):
        return False
    exporter.hand_off_lease(lease.lease_name)
    return True
