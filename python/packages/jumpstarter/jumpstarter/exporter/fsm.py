"""Shared exporter lifecycle for controller and standalone modes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from dataclasses import dataclass, field, replace
from enum import IntEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from anyio import CancelScope, Event, sleep
from anyio.lowlevel import checkpoint
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common import HOOK_WARNING_PREFIX, ExporterStatus
from jumpstarter.exporter.hooks import HookExecutionError
from jumpstarter.exporter.lease_context import LeaseContext
from jumpstarter.fsm import ExitFSM, FSMRunner
from jumpstarter.fsm import State as FSMState
from jumpstarter.logging import clear_log_context, set_log_context

if TYPE_CHECKING:
    from jumpstarter.exporter.exporter import Exporter
    from jumpstarter.exporter.session import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LeaseKey:
    name: str
    generation: int

    def __post_init__(self) -> None:
        if not self.name or self.generation < 1:
            raise ValueError("An assigned lease needs a non-empty name and a positive generation")


@dataclass(frozen=True)
class Assignment:
    key: LeaseKey
    client_name: str = ""
    context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


class ShutdownMode(IntEnum):
    DRAIN = 1
    STOP_NOW = 2


@dataclass(frozen=True)
class ShutdownRequest:
    mode: ShutdownMode
    exit_code: int | None = None
    reason: str = ""
    should_unregister: bool = False


@dataclass(frozen=True)
class Inputs:
    """Immutable observations and requests for one state evaluation."""

    assignment: Assignment | None = None
    shutdown: ShutdownRequest | None = None


type State = FSMState[Inputs]


class InputBuffer:
    """Latest assignment, persistent shutdown intent, and a coalescing runner wakeup."""

    def __init__(self) -> None:
        self._snapshot = Inputs()
        self._changed = Event()
        self._generation = 0

    def snapshot_and_watch(self) -> tuple[Inputs, Event]:
        return self._snapshot, self._changed

    def wake(self, reason: str = "lease-local input") -> None:
        """Notify after updating lease-local inputs, even if the snapshot is unchanged."""
        # ponytail: event-loop-only updates; marshal thread callbacks onto the loop if needed.
        changed = self._changed
        self._changed = Event()
        changed.set()
        logger.debug("FSM wakeup: %s", reason)

    def _publish(self, inputs: Inputs) -> None:
        if inputs != self._snapshot:
            self._snapshot = inputs
            logger.debug(
                "FSM inputs changed: assignment=%r shutdown_mode=%s",
                inputs.assignment.key if inputs.assignment is not None else None,
                inputs.shutdown.mode.name if inputs.shutdown is not None else None,
            )
            self.wake("snapshot changed")

    def publish_assignment(
        self,
        lease_name: str | None,
        client_name: str = "",
        context: Mapping[str, str] | None = None,
    ) -> None:
        """Publish an observation; None means unleased, not a stream interruption."""
        if lease_name == "":
            raise ValueError("An assigned lease needs a non-empty name; use None for unleased")
        previous = self._snapshot.assignment
        previous_name = previous.key.name if previous is not None else None
        generation = self._generation + (previous_name != lease_name)
        assignment = (
            Assignment(LeaseKey(lease_name, generation), client_name, context if context is not None else {})
            if lease_name is not None
            else None
        )
        self._generation = generation
        self._publish(replace(self._snapshot, assignment=assignment))

    def request_shutdown(self, request: ShutdownRequest) -> None:
        """Stronger requests take precedence; skipping controller cleanup is sticky."""
        previous = self._snapshot.shutdown
        if previous is not None:
            stronger = request.mode > previous.mode
            request = ShutdownRequest(
                mode=max(previous.mode, request.mode),
                exit_code=(
                    request.exit_code
                    if request.exit_code is not None and (stronger or previous.exit_code is None)
                    else previous.exit_code
                ),
                reason=(request.reason or previous.reason) if stronger else (previous.reason or request.reason),
                should_unregister=previous.should_unregister and request.should_unregister,
            )
        self._publish(replace(self._snapshot, shutdown=request))

    async def send(self, status: jumpstarter_pb2.StatusResponse) -> None:
        """Status-stream adapter; an outage never calls this with a synthetic release."""
        self.publish_assignment(status.lease_name if status.leased else None, status.client_name, status.context)
        await checkpoint()


@dataclass
class ExporterFSM:
    """Owns the exporter lifecycle's input buffer and single-use runner."""

    inputs: InputBuffer = field(default_factory=InputBuffer)
    runner: FSMRunner[Inputs] | None = field(init=False, default=None)
    """The lifecycle in either serving mode; current_state remains queryable after exit."""

    async def run(self, initial: State, *, name: str) -> None:
        self.runner = FSMRunner(initial, self.inputs, name=name)
        await self.runner.run()


def _stop_now(inputs: Inputs) -> bool:
    return inputs.shutdown is not None and inputs.shutdown.mode == ShutdownMode.STOP_NOW


def _released(inputs: Inputs, key: LeaseKey) -> bool:
    return inputs.assignment is None or inputs.assignment.key != key


def _update_client(inputs: Inputs, key: LeaseKey, lease: LeaseContext) -> None:
    if inputs.assignment is not None and inputs.assignment.key == key:
        lease.client_name = inputs.assignment.client_name
        set_log_context(client=lease.client_name)


@dataclass
class Available:
    exporter: Exporter
    last_completed: LeaseKey | None = None
    session_context: AbstractAsyncContextManager[tuple[Session, str, str]] | None = None

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> BeforeLeaseHook | Stopping | None:
        if inputs.shutdown is not None:
            return Stopping(self.exporter)
        if self.exporter._exporter_status != ExporterStatus.AVAILABLE:
            await self.exporter._report_status(ExporterStatus.AVAILABLE, "Available for new lease")
        assignment = inputs.assignment
        if assignment is None or assignment.key == self.last_completed:
            return None

        lease = LeaseContext(
            lease_name=assignment.key.name,
            client_name=assignment.client_name,
            wake=self.exporter.fsm.inputs.wake,
        )
        self.exporter._lease_context = lease
        set_log_context(**{"lease_id": lease.lease_name, "exporter": self.exporter.exporter_name, **assignment.context})
        set_log_context(client=lease.client_name)
        logger.info("Starting new lease: %s", lease.lease_name)
        context = self.session_context if self.session_context is not None else self.exporter.session_for_lease()
        session, main_path, hook_path = await resources.enter_async_context(context)
        lease.session, lease.socket_path, lease.hook_socket_path = session, main_path, hook_path
        session.lease_context = lease
        session.update_status(ExporterStatus.BEFORE_LEASE_HOOK, "Preparing lease")
        return BeforeLeaseHook(self.exporter, assignment.key, lease)


@dataclass
class BeforeLeaseHook:
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
            if executor is not None:
                if executor.config.before_lease is not None:
                    await self.exporter._report_status(ExporterStatus.BEFORE_LEASE_HOOK, "Running beforeLease hook")
                # Ordinary stop/release updates are inputs. External cancellation also
                # leaves this bounded hook time to finish before its session is closed.
                with CancelScope(shield=True):
                    warning = await executor.execute_before_lease_hook(self.lease)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return BeforeLeaseHookFailed(self.exporter, self.key, self.lease, error)
        return LeaseReady(self.exporter, self.key, self.lease, warning)


@dataclass
class LeaseReady:
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
            await self.exporter._report_status(ExporterStatus.LEASE_READY, message)
        return None


@dataclass
class BeforeLeaseHookFailed:
    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    error: Exception

    async def run(
        self, inputs: Inputs, resources: AsyncExitStack
    ) -> ReleasingLease | AwaitingLeaseRelease | AfterLeaseHook | None:
        if self.exporter._exporter_status != ExporterStatus.BEFORE_LEASE_HOOK_FAILED:
            await self.exporter._report_status(
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
    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> AwaitingLeaseRelease | AfterLeaseHookFailed:
        _update_client(inputs, self.key, self.lease)
        executor = self.exporter.hook_executor
        warning = None
        try:
            if executor is not None:
                if executor.config.after_lease is not None:
                    await self.exporter._report_status(ExporterStatus.AFTER_LEASE_HOOK, "Running afterLease hook")
                with CancelScope(shield=True):
                    warning = await executor.execute_after_lease_hook(self.lease)
        except Exception as error:  # noqa: BLE001 - failure policy belongs to the successor state
            return AfterLeaseHookFailed(self.exporter, self.key, self.lease, error)
        message = f"{HOOK_WARNING_PREFIX}afterLease hook warning: {warning}" if warning else "Available for new lease"
        await self.exporter._report_status(ExporterStatus.AVAILABLE, message)
        return AwaitingLeaseRelease(self.exporter, self.key, self.lease)


@dataclass
class AfterLeaseHookFailed:
    exporter: Exporter
    key: LeaseKey
    lease: LeaseContext
    error: Exception

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ReleasingLease | AwaitingLeaseRelease:
        await self.exporter._report_status(
            ExporterStatus.AFTER_LEASE_HOOK_FAILED, f"afterLease hook failed: {self.error}"
        )
        if isinstance(self.error, HookExecutionError):
            if self.error.should_end_lease():
                return ReleasingLease(self.exporter, self.key, self.lease)
            self.exporter.stop(wait_for_lease_exit=True, should_unregister=True, exit_code=1)
        return AwaitingLeaseRelease(self.exporter, self.key, self.lease)


@dataclass
class ReleasingLease:
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
        if inputs.shutdown is not None or self.exporter.exit_on_lease_end or self.exporter._standalone:
            return Stopping(self.exporter)
        # Available gets a fresh snapshot before it can admit any replacement.
        return Available(self.exporter, last_completed=self.key)


@dataclass
class Stopping:
    exporter: Exporter

    async def run(self, inputs: Inputs, resources: AsyncExitStack) -> ExitFSM:
        await self.exporter._finish_shutdown()
        return ExitFSM()
