"""Production exporter lifecycle states for controller and standalone modes.

Each state class docstring is the canonical description of that phase. Keep those
docstrings aligned with JEP-0016 and any exported lifecycle diagrams when
transitions or policies change (see the exporter-fsm-docs-sync Cursor rule).
"""

from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager, AsyncExitStack
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
)
from jumpstarter.fsm import ExitFSM
from jumpstarter.logging import clear_log_context, set_log_context

if TYPE_CHECKING:
    from jumpstarter.exporter.exporter import Exporter
    from jumpstarter.exporter.session import Session

logger = logging.getLogger("jumpstarter.exporter.fsm")


def _bind(lease: LeaseContext, session: Session, main_path: str, hook_path: str) -> None:
    lease.session, lease.socket_path, lease.hook_socket_path = session, main_path, hook_path
    session.lease_context = lease


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


async def _setup_abandoned(exporter: Exporter, key: LeaseKey, lease: LeaseContext) -> AfterLeaseHook:
    """Close a setup that will not run; cleanup runs only if its start was recorded."""
    await exporter.setup_did_not_start(lease)
    return AfterLeaseHook(exporter, key, lease)


@dataclass
class Starting:
    """Registered; wait for the first status before admitting or taking over a lease."""

    exporter: Exporter

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Available | RecoveringLease | Stopping | None:
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
    """Take over the lease a previous exporter process left, per the controller's hook record."""

    exporter: Exporter
    takeover: Takeover

    async def run(
        self, inputs: Inputs, resources: AsyncExitStack
    ) -> Available | LeaseReady | BeforeLeaseHook | BeforeLeaseHookFailed | AfterLeaseHook | Stopping:
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
        exporter.lease_context = lease
        set_log_context(lease_id=lease.lease_name, exporter=exporter.exporter_name)
        set_log_context(client=lease.client_name)
        if restart is Restart.SETTLE:
            return await self._settle(key, lease, before, after)
        if restart is Restart.FINISH_SETUP:
            assert before is not None
            logger.info("Resuming lease %s after exporter restart (beforeLease hook did not finish)", lease.lease_name)
            lease.hooks.finish_cut_off("before_lease", before, exporter.on_interrupt("before_lease"), lease.lease_name)
            _bind(lease, *await resources.enter_async_context(exporter.session_for_lease()))
            return BeforeLeaseHook(exporter, key, lease)
        if restart is Restart.RESUME:
            assert before is not None
            logger.info(
                "Resuming lease %s after exporter restart (beforeLease hook %s before it)",
                lease.lease_name,
                LeaseHookPhase(before.phase).name.lower(),
            )
            lease.hooks.resume(before)
            # A resumed lease is not set up again, so its device is not reset either.
            _bind(lease, *await resources.enter_async_context(exporter.session_for_lease(reset_device=False)))
            if not exporter.standalone:
                await resources.enter_async_context(exporter.lease_connections(lease))
            resumed = lease.hooks.resumed
            assert resumed is not None
            await exporter.report_status(*resumed, lease=lease)
            if resumed[0] == ExporterStatus.LEASE_READY:
                return LeaseReady(exporter, key, lease)
            return BeforeLeaseHookFailed(
                exporter, key, lease, RuntimeError(before.message or "beforeLease hook failed")
            )
        # Restart.CLEAN_UP: the lease owes its afterLease hook, first thing.
        if takeover.assigned:
            logger.info("Lease %s owes its afterLease hook; running it", lease.lease_name)
            lease.end_session_requested = True
        else:
            logger.info("Lease %s ended while the exporter was down; running its afterLease hook", lease.lease_name)
        return await self._clean_up(resources, key, lease, before, after)

    async def _clean_up(
        self, resources: AsyncExitStack, key: LeaseKey, lease: LeaseContext,
        before: jumpstarter_pb2.LeaseHookState | None, after: jumpstarter_pb2.LeaseHookState | None,
    ) -> AfterLeaseHook:
        """Serve a takeover whose lease owes cleanup, without setting it up again."""
        exporter = self.exporter
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
        lease.hooks.resumed = (ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook")
        # Setup does not run again: the lease is in cleanup, so no client dials to serve.
        _bind(lease, *await resources.enter_async_context(exporter.session_for_lease(reset_device=False)))
        return AfterLeaseHook(exporter, key, lease)

    async def _settle(
        self, key: LeaseKey, lease: LeaseContext, before: jumpstarter_pb2.LeaseHookState | None,
        after: jumpstarter_pb2.LeaseHookState | None,
    ) -> Available:
        """Settle a recorded lease that needs no more hooks."""
        exporter = self.exporter
        if after is None:
            await exporter.record_lease_hook(lease, "after_lease", LeaseHookPhase.SKIPPED, "beforeLease hook failed")
        exporter.lease_context = None
        if not self.takeover.assigned:
            return Available(exporter)
        # Never set it up again; ignore its leased ticks until the lease ends.
        if hook_failure_ended_lease(before) or hook_failure_ended_lease(after):
            # Hold the slot so the release targets this lease, as the previous
            # process would have done for a hook that failed with onFailure: endLease.
            exporter.lease_context = lease
            logger.info("Lease %s had a hook fail with onFailure: endLease; releasing it", lease.lease_name)
            try:
                await exporter.request_lease_release(lease, key)
            finally:
                exporter.lease_context = None
        else:
            logger.info("Lease %s is done with its hooks; leaving it to its client", lease.lease_name)
        return Available(exporter, last_completed=key)


@dataclass
class Available:
    """Admit a new lease or honor exporter shutdown.

    Waiting state: honor an outstanding shutdown before admission. Reject the
    last completed assignment key so a stale observation cannot reopen the same
    lease. Otherwise prepare the session for the current assignment, register it
    on the runner resource stack, and transition to ``BeforeLeaseHook``. Return
    ``None`` when there is nothing admissible.
    """

    exporter: Exporter
    last_completed: LeaseKey | None = None
    # Standalone TCP injects its session here; controller mode leaves this None
    # and falls back to exporter.session_for_lease() at admission.
    session_context: AbstractAsyncContextManager[tuple[Session, str, str]] | None = None
    # Set only while entering the session context so test doubles can observe admission.
    admitting: LeaseContext | None = None

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> BeforeLeaseHook | Stopping | None:
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
        # Keep admitting visible while the session context enters for test doubles.
        self.admitting = lease
        try:
            context = self.session_context or self.exporter.session_for_lease()
            # Register session/sockets on the runner stack so ClosingLease can tear them down.
            session, main_path, hook_path = await resources.enter_async_context(context)
        finally:
            self.admitting = None
        _bind(lease, session, main_path, hook_path)
        session.update_status(ExporterStatus.BEFORE_LEASE_HOOK, "Preparing lease")
        return BeforeLeaseHook(self.exporter, lease_assignment.key, lease)


@dataclass
class BeforeLeaseHook:
    """Run the before-lease hook for an admitted assignment.

    Activity state: after session setup, re-check the latest assignment. If the
    assignment was revoked or stop-now applies, skip both hooks and go to
    ``ClosingLease``. Otherwise start Listen (controller mode), await the
    execution-only before-lease hook under a shielded cancel scope, and select
    ``LeaseReady`` or ``BeforeLeaseHookFailed`` from the outcome.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(
        self, inputs: Inputs, resources: AsyncExitStack
    ) -> LeaseReady | BeforeLeaseHookFailed | AfterLeaseHook | ClosingLease:
        # Setup may have awaited while this assignment was revoked or replaced.
        if _released(inputs, self.key) or _stop_now(inputs):
            return await _setup_abandoned(self.exporter, self.key, self.lease)
        _update_client(inputs, self.key, self.lease)
        # Controller mode: start Listen/dial tasks for this lease on the resource stack.
        if not self.exporter.standalone:
            await resources.enter_async_context(self.exporter.lease_connections(self.lease))
        exporter, lease = self.exporter, self.lease
        executor = exporter.hook_executor
        warning = None
        try:
            if executor is None or executor.config.before_lease is None:
                # No setup hook: the record must still show the lease as set up.
                if not await exporter.record_lease_hook(lease, "before_lease", LeaseHookPhase.SKIPPED):
                    lease.skip_after_lease_hook = True
                    return ClosingLease(exporter, self.key, lease)
            else:
                if not await exporter.record_start(lease, "before_lease"):
                    await exporter.setup_did_not_start(lease)
                    return ClosingLease(exporter, self.key, lease)
                # Recording can wait on the controller while this lease ends: a late
                # acknowledgement must not run setup for a lease that no longer holds it.
                if _released(inputs, self.key) or _stop_now(inputs):
                    return await _setup_abandoned(exporter, self.key, lease)
                await exporter.report_status(
                    ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook", lease=lease
                )
                # Ordinary stop/release updates are inputs. External cancellation also
                # leaves this bounded hook time to finish before its session is closed.
                with CancelScope(shield=True):
                    warning = await executor.execute_before_lease_hook(lease)
                await exporter.record_outcome(lease, "before_lease", warning)
        except HookExecutionError as error:
            await exporter.record_lease_hook(
                lease, "before_lease", LeaseHookPhase.FAILED, str(error), error.on_failure
            )
            return BeforeLeaseHookFailed(exporter, self.key, lease, error)
        except Exception as error:  # noqa: BLE001 - failure policy is the successor state's
            await exporter.record_lease_hook(lease, "before_lease", LeaseHookPhase.FAILED, str(error))
            return BeforeLeaseHookFailed(exporter, self.key, lease, error)
        return LeaseReady(exporter, self.key, lease, warning)


@dataclass
class LeaseReady:
    """Serve an active lease until the session needs finishing.

    Waiting state: inspect shutdown mode, revocation/reassignment, and this
    lease's EndSession request. Report ``LEASE_READY`` (including any before-hook
    warning) while normal service continues and return ``None``. Transition to
    ``AfterLeaseHook`` when the session must finish.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    warning: str | None = None

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> AfterLeaseHook | ClosingLease | None:
        _update_client(inputs, self.key, self.lease)
        if _released(inputs, self.key) or self.lease.end_session_requested:
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        if _stop_now(inputs):
            if _hand_off(self.exporter, inputs, self.key, self.lease):
                return ClosingLease(self.exporter, self.key, self.lease)
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        # Still serving: publish LEASE_READY once (including any before-hook warning).
        if self.exporter.exporter_status != ExporterStatus.LEASE_READY:
            message = (
                f"{HOOK_WARNING_PREFIX}beforeLease hook warning: {self.warning}"
                if self.warning
                else "Ready for commands"
            )
            await self.exporter.report_status(ExporterStatus.LEASE_READY, message, lease=self.lease)
        return None


@dataclass
class BeforeLeaseHookFailed:
    """Hold a failed before-lease hook outcome until cleanup policy applies.

    Waiting/policy state: publish ``BEFORE_LEASE_HOOK_FAILED``. Explicit
    ``HookExecutionError`` outcomes never run the after-hook: ``endLease`` goes
    to ``ReleasingLease``; ``exit`` requests drain shutdown and waits in
    ``AwaitingLeaseRelease``. Unexpected failures retain the failed status until
    release, stop-now, or EndSession requires ``AfterLeaseHook`` cleanup.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    error: Exception

    async def run(
        self, inputs: Inputs, resources: AsyncExitStack
    ) -> ReleasingLease | AwaitingLeaseRelease | AfterLeaseHook | ClosingLease | None:
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
                return ClosingLease(self.exporter, self.key, self.lease)
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        return None


@dataclass
class AfterLeaseHook:
    """Run the after-lease cleanup hook once for this assignment.

    Activity state: await the execution-only after-lease hook under a shielded
    cancel scope, then select ``AwaitingLeaseRelease`` or
    ``AfterLeaseHookFailed``. Reports ``AVAILABLE`` after a successful (or
    warning-only) cleanup; reservation retention still belongs to the successor.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> AwaitingLeaseRelease | AfterLeaseHookFailed:
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
                await exporter.report_status(
                    ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook", lease=lease
                )
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
class AfterLeaseHookFailed:
    """Apply after-lease hook failure policy, then wait for release or close.

    Activity/policy state: publish ``AFTER_LEASE_HOOK_FAILED``. Explicit
    ``endLease`` failures go through ``ReleasingLease``; ``exit`` requests drain
    shutdown. All paths then reach ``AwaitingLeaseRelease`` (or release then
    close) so reservation and transport teardown stay ordered.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    error: Exception

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ReleasingLease | AwaitingLeaseRelease:
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
class ReleasingLease:
    """Request controller release after an explicit ``endLease`` hook failure.

    Activity state: reached only for ``endLease`` hook failures. In standalone
    mode the single failed session is retained until stop
    (``AwaitingLeaseRelease``). Otherwise preserve the one-second
    failure-observation window for polling clients, attempt a named release when
    still assigned, then proceed to ``ClosingLease``. Ordinary cleanup never
    enters this state.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ClosingLease | AwaitingLeaseRelease:
        if self.exporter.standalone:
            # Direct mode retains its single failed session until the exporter stops.
            return AwaitingLeaseRelease(self.exporter, self.key, self.lease)
        if not _released(inputs, self.key) and not _stop_now(inputs):
            # Preserve the existing observation window for clients polling failed status.
            await sleep(1)
            # Named ReleaseLease so a delayed retry cannot target a replacement.
            await self.exporter.request_lease_release(self.lease, self.key)
        return ClosingLease(self.exporter, self.key, self.lease)


@dataclass
class AwaitingLeaseRelease:
    """Retain reservation and final-log access after hooks complete.

    Waiting state: may report ``AVAILABLE`` while still holding the controller
    reservation—only ``Available`` admits another lease. Return ``None`` until
    controller release/reassignment or stop-now; proceed to ``ClosingLease``
    immediately if one already applies. EndSession (and drain alone) does not
    release the reservation.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ClosingLease | None:
        _update_client(inputs, self.key, self.lease)
        # Only controller release/reassignment or stop-now frees the reservation.
        if _released(inputs, self.key) or _stop_now(inputs):
            return ClosingLease(self.exporter, self.key, self.lease)
        # EndSession (and drain alone) does not release the controller reservation.
        return None


@dataclass
class ClosingLease:
    """Tear down lease-scoped resources before admitting another lease or stopping.

    Activity state: stop and join lease I/O, then await ``resources.aclose()`` for
    the session, sockets, and drivers. A failed close does not free the admission
    slot. Clear lease-local log context, then return ``Available(last_completed=key)``
    to continue serving or ``Stopping`` when shutdown, exit-on-lease-end, or
    standalone mode requires exit.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Available | Stopping:
        # LIFO: stop and join lease I/O before closing its session, sockets, and drivers.
        await resources.aclose()
        clear_log_context()
        set_log_context(exporter=self.exporter.exporter_name)
        if self.exporter.exit_on_lease_end and not self.exporter.standalone:
            self.exporter.stop(should_unregister=True)
        # One-shot / standalone sessions exit after this lease; otherwise reopen Available.
        if inputs.shutdown or self.exporter.exit_on_lease_end or self.exporter.standalone:
            return Stopping(self.exporter)
        # Available gets a fresh snapshot before it can admit any replacement.
        return Available(self.exporter, last_completed=self.key)


@dataclass
class Stopping:
    """Finish exporter shutdown and terminate the lifecycle runner.

    Activity state: publish offline status and complete applicable controller
    unregistration, telemetry, and runtime-sidecar shutdown via
    ``Exporter.finish_shutdown``, then return ``ExitFSM()``.
    """

    exporter: Exporter

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ExitFSM:
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
