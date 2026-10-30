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


def _stop_now(inputs: Inputs) -> bool:
    return bool(inputs.shutdown) and inputs.shutdown.mode == ShutdownMode.STOP_NOW


def _released(inputs: Inputs, key: LeaseKey) -> bool:
    return not inputs.lease_assignment or inputs.lease_assignment.key != key


def _update_client(inputs: Inputs, key: LeaseKey, lease: LeaseContext) -> None:
    if inputs.lease_assignment and inputs.lease_assignment.key == key:
        lease.client_name = inputs.lease_assignment.client_name
        set_log_context(client=lease.client_name)


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

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> BeforeLeaseHook | Stopping | None:
        if inputs.shutdown:
            return Stopping(self.exporter)
        if self.exporter._exporter_status != ExporterStatus.AVAILABLE:
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
        self.exporter._lease_context = lease
        set_log_context(
            **{"lease_id": lease.lease_name, "exporter": self.exporter.exporter_name, **lease_assignment.context}
        )
        set_log_context(client=lease.client_name)
        logger.info("Starting new lease: %s", lease.lease_name)
        context = self.session_context or self.exporter.session_for_lease()
        session, main_path, hook_path = await resources.enter_async_context(context)
        lease.session, lease.socket_path, lease.hook_socket_path = session, main_path, hook_path
        session.lease_context = lease
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

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> LeaseReady | BeforeLeaseHookFailed | ClosingLease:
        # Setup may have awaited while this assignment was revoked or replaced.
        if _released(inputs, self.key) or _stop_now(inputs):
            return ClosingLease(self.exporter, self.key, self.lease)
        _update_client(inputs, self.key, self.lease)
        if not self.exporter._standalone:
            await resources.enter_async_context(self.exporter.lease_connections(self.lease))
        executor = self.exporter.hook_executor
        warning = None
        try:
            if executor:
                if executor.config.before_lease:
                    await self.exporter.report_status(ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook")
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

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> AfterLeaseHook | None:
        _update_client(inputs, self.key, self.lease)
        if _released(inputs, self.key) or _stop_now(inputs) or self.lease.end_session_requested:
            return AfterLeaseHook(self.exporter, self.key, self.lease)
        if self.exporter._exporter_status != ExporterStatus.LEASE_READY:
            message = (
                f"{HOOK_WARNING_PREFIX}beforeLease hook warning: {self.warning}"
                if self.warning
                else "Ready for commands"
            )
            await self.exporter.report_status(ExporterStatus.LEASE_READY, message)
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
    ) -> ReleasingLease | AwaitingLeaseRelease | AfterLeaseHook | None:
        if self.exporter._exporter_status != ExporterStatus.BEFORE_LEASE_HOOK_FAILED:
            await self.exporter.report_status(
                ExporterStatus.BEFORE_LEASE_HOOK_FAILED, f"beforeLease hook failed: {self.error}"
            )
        if isinstance(self.error, HookExecutionError):
            # Explicit before-hook failures never run the after-hook.
            if self.error.should_end_lease():
                return ReleasingLease(self.exporter, self.key, self.lease)
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

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> AwaitingLeaseRelease | AfterLeaseHookFailed:
        _update_client(inputs, self.key, self.lease)
        executor = self.exporter.hook_executor
        warning = None
        try:
            if executor:
                if executor.config.after_lease:
                    await self.exporter.report_status(ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook")
                with CancelScope(shield=True):
                    warning = await executor.execute_after_lease_hook(self.lease)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return AfterLeaseHookFailed(self.exporter, self.key, self.lease, error)
        message = f"{HOOK_WARNING_PREFIX}afterLease hook warning: {warning}" if warning else "Available for new lease"
        await self.exporter.report_status(ExporterStatus.AVAILABLE, message)
        return AwaitingLeaseRelease(self.exporter, self.key, self.lease)


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
            ExporterStatus.AFTER_LEASE_HOOK_FAILED, f"afterLease hook failed: {self.error}"
        )
        if isinstance(self.error, HookExecutionError):
            if self.error.should_end_lease():
                return ReleasingLease(self.exporter, self.key, self.lease)
            self.exporter.stop(wait_for_lease_exit=True, should_unregister=True, exit_code=1)
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
        if self.exporter._standalone:
            # Direct mode retains its single failed session until the exporter stops.
            return AwaitingLeaseRelease(self.exporter, self.key, self.lease)
        if not _released(inputs, self.key) and not _stop_now(inputs):
            # Preserve the existing observation window for clients polling failed status.
            await sleep(1)
            await self.exporter._request_lease_release(self.lease, self.key)
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
        if _released(inputs, self.key) or _stop_now(inputs):
            return ClosingLease(self.exporter, self.key, self.lease)
        # EndSession (and drain alone) does not release the controller reservation.
        return None


@dataclass
class ClosingLease:
    """Tear down lease-scoped resources before admitting another lease or stopping.

    Activity state: stop and join lease I/O, then await ``resources.aclose()`` for
    the session, sockets, and drivers. A failed close does not free the admission
    slot. Clear lease-local context, then return ``Available(last_completed=key)``
    to continue serving or ``Stopping`` when shutdown, exit-on-lease-end, or
    standalone mode requires exit.
    """

    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> Available | Stopping:
        # LIFO: stop and join lease I/O before closing its session, sockets, and drivers.
        await resources.aclose()
        self.exporter._lease_context = None
        clear_log_context()
        set_log_context(exporter=self.exporter.exporter_name)
        if self.exporter.exit_on_lease_end and not self.exporter._standalone:
            self.exporter.stop(should_unregister=True)
        if inputs.shutdown or self.exporter.exit_on_lease_end or self.exporter._standalone:
            return Stopping(self.exporter)
        # Available gets a fresh snapshot before it can admit any replacement.
        return Available(self.exporter, last_completed=self.key)


@dataclass
class Stopping:
    """Finish exporter shutdown and terminate the lifecycle runner.

    Activity state: publish offline status and complete applicable controller
    unregistration, telemetry, and runtime-sidecar shutdown via
    ``Exporter._finish_shutdown``, then return ``ExitFSM()``.
    """

    exporter: Exporter

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ExitFSM:
        await self.exporter._finish_shutdown()
        return ExitFSM()
