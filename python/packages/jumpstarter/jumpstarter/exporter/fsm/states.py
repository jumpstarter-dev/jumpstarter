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

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus
from jumpstarter.exporter.fsm.input import Inputs, LeaseKey, ShutdownMode
from jumpstarter.exporter.fsm.lease_context import LeaseContext
from jumpstarter.exporter.hooks import HookExecutionError
from jumpstarter.fsm import ExitFSM
from jumpstarter.logging import clear_log_context, set_log_context

if TYPE_CHECKING:
    from jumpstarter.exporter.exporter import Exporter
    from jumpstarter.exporter.session import Session

logger = logging.getLogger("jumpstarter.exporter.fsm")


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
        )
        set_log_context(
            **{"lease_id": lease.lease_name, "exporter": self.exporter.exporter_name, **lease_assignment.context}
        )
        set_log_context(client=lease.client_name)
        logger.info("Starting new lease: %s", lease.lease_name)
        return Leased(self.exporter, lease_assignment.key, lease, self.session_context)


@dataclass
class Leased:
    """Own one lease's session, sockets, drivers, and I/O for as long as it runs.

    Composite state: open the session, then run the lease's own states, from
    ``BeforeLeaseHook`` until one of them returns ``ExitFSM``, as a nested
    machine inside that ``async with`` block. Controller mode also keeps the
    lease's Listen/dial tasks open around them. Leaving the block stops and
    joins lease I/O before closing the session, sockets, and drivers, whether
    the lease's states exited, failed, or were cancelled.

    Session setup awaits, so the assignment is read again once it is open: if it
    was revoked or replaced, or stop-now applies, skip Listen and both hooks and
    close. A failed close propagates and does not free the admission slot. Clear
    lease-local log context, then return ``Available(last_completed=key)`` to
    continue serving, or ``Stopping`` for exit-on-lease-end and standalone mode.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    session_context: AbstractAsyncContextManager[tuple[Session, str, str]] | None = None

    async def run(self, inputs: Inputs) -> Available | Stopping:
        exporter, lease = self.exporter, self.lease
        async with self.session_context or exporter.session_for_lease() as (session, main_path, hook_path):
            lease.session, lease.socket_path, lease.hook_socket_path = session, main_path, hook_path
            session.lease_context = lease
            session.update_status(ExporterStatus.BEFORE_LEASE_HOOK, "Preparing lease")
            # The snapshot this state was entered with predates session setup.
            latest = exporter.fsm.inputs.snapshot()
            if not (_released(latest, self.key) or _stop_now(latest)):
                # Controller mode: Listen/dial tasks for this lease, closed before its session.
                connections = nullcontext() if exporter.standalone else exporter.lease_connections(lease)
                async with connections:
                    await exporter.fsm.run_nested(BeforeLeaseHook(exporter, self.key, lease))
        clear_log_context()
        set_log_context(exporter=exporter.exporter_name)
        if exporter.exit_on_lease_end and not exporter.standalone:
            exporter.stop(should_unregister=True)
        # One-shot / standalone sessions exit after this lease.
        if exporter.exit_on_lease_end or exporter.standalone:
            return Stopping(exporter)
        # Available gets a fresh snapshot: it honors shutdown before admitting any replacement.
        return Available(exporter, last_completed=self.key)


@dataclass
class BeforeLeaseHook:
    """Run the before-lease hook for an admitted assignment.

    Activity state: first state of the machine nested in ``Leased``, entered
    with the session open and the assignment still held. Await the
    execution-only before-lease hook under a shielded cancel scope, and select
    ``LeaseReady`` or ``BeforeLeaseHookFailed`` from the outcome.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs) -> LeaseReady | BeforeLeaseHookFailed:
        _update_client(inputs, self.key, self.lease)
        executor = self.exporter.hook_executor
        warning = None
        try:
            if executor:
                if executor.config.before_lease:
                    await self.exporter.report_status(
                        ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook", lease=self.lease
                    )
                # Ordinary stop/release updates are inputs. External cancellation also
                # leaves this bounded hook time to finish before its session is closed.
                with CancelScope(shield=True):
                    warning = await executor.execute_before_lease_hook(self.lease)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return BeforeLeaseHookFailed(self.exporter, self.key, self.lease, error)
        return LeaseReady(self.exporter, self.key, self.lease, warning)


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

    async def run(self, inputs: Inputs) -> AfterLeaseHook | None:
        _update_client(inputs, self.key, self.lease)
        # Release, stop-now, or client EndSession: finish the session via after-hook.
        if _released(inputs, self.key) or _stop_now(inputs) or self.lease.end_session_requested:
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

    async def run(self, inputs: Inputs) -> ReleasingLease | AwaitingLeaseRelease | AfterLeaseHook | None:
        if self.exporter.exporter_status != ExporterStatus.BEFORE_LEASE_HOOK_FAILED:
            await self.exporter.report_status(
                ExporterStatus.BEFORE_LEASE_HOOK_FAILED,
                f"beforeLease hook failed: {self.error}",
                lease=self.lease,
            )
        if isinstance(self.error, HookExecutionError):
            # Explicit before-hook failures never run the after-hook.
            if self.error.should_end_lease():
                return ReleasingLease(self.exporter, self.key, self.lease)
            # on_failure=exit: drain shutdown, keep reservation until controller releases.
            self.exporter.stop(wait_for_lease_exit=True, should_unregister=True, exit_code=1)
            return AwaitingLeaseRelease(self.exporter, self.key, self.lease)
        # Unexpected failures retain their failed status until the lease needs cleanup.
        if _released(inputs, self.key) or _stop_now(inputs) or self.lease.end_session_requested:
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

    async def run(self, inputs: Inputs) -> AwaitingLeaseRelease | AfterLeaseHookFailed:
        _update_client(inputs, self.key, self.lease)
        executor = self.exporter.hook_executor
        warning = None
        try:
            if executor:
                if executor.config.after_lease:
                    await self.exporter.report_status(
                        ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook", lease=self.lease
                    )
                # Shield so external cancel still lets the after-hook finish before teardown.
                with CancelScope(shield=True):
                    warning = await executor.execute_after_lease_hook(self.lease)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return AfterLeaseHookFailed(self.exporter, self.key, self.lease, error)
        # Session is done; AVAILABLE here does not free the controller reservation yet.
        message = f"{HOOK_WARNING_PREFIX}afterLease hook warning: {warning}" if warning else "Available for new lease"
        await self.exporter.report_status(ExporterStatus.AVAILABLE, message, lease=self.lease)
        return AwaitingLeaseRelease(self.exporter, self.key, self.lease)


@dataclass
class AfterLeaseHookFailed:
    """Apply after-lease hook failure policy, then wait for release or close.

    Activity/policy state: publish ``AFTER_LEASE_HOOK_FAILED``. Explicit
    ``endLease`` failures go through ``ReleasingLease``; ``exit`` requests drain
    shutdown. All paths then reach ``AwaitingLeaseRelease`` (or release then
    exit) so reservation and transport teardown stay ordered.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
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
class ReleasingLease:
    """Request controller release after an explicit ``endLease`` hook failure.

    Activity state: reached only for ``endLease`` hook failures. In standalone
    mode the single failed session is retained until stop
    (``AwaitingLeaseRelease``). Otherwise preserve the one-second
    failure-observation window for polling clients, attempt a named release when
    still assigned, then return ``ExitFSM`` so ``Leased`` closes the lease.
    Ordinary cleanup never enters this state.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

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
class AwaitingLeaseRelease:
    """Retain reservation and final-log access after hooks complete.

    Waiting state: may report ``AVAILABLE`` while still holding the controller
    reservation—only ``Available`` admits another lease. Return ``None`` until
    controller release/reassignment or stop-now; return ``ExitFSM`` immediately
    if one already applies, so ``Leased`` closes the lease. EndSession (and
    drain alone) does not release the reservation.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

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
