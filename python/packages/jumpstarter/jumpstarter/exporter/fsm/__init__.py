"""Shared exporter lifecycle for controller and standalone modes."""

from __future__ import annotations

from dataclasses import dataclass, field

from jumpstarter.exporter.fsm.input import (
    InputBuffer,
    Inputs,
    LeaseAssignment,
    LeaseKey,
    ShutdownMode,
    ShutdownRequest,
    State,
    Takeover,
)
from jumpstarter.exporter.fsm.lease_context import LeaseContext
from jumpstarter.exporter.fsm.states import (
    AfterLeaseHook,
    AfterLeaseHookFailed,
    Available,
    AwaitingLeaseRelease,
    BeforeLeaseHook,
    BeforeLeaseHookFailed,
    ClosingLease,
    LeaseReady,
    RecoveringLease,
    ReleasingLease,
    Starting,
    Stopping,
)
from jumpstarter.fsm import FSMRunner

__all__ = [
    "AfterLeaseHook",
    "AfterLeaseHookFailed",
    "Available",
    "AwaitingLeaseRelease",
    "BeforeLeaseHook",
    "BeforeLeaseHookFailed",
    "ClosingLease",
    "ExporterFSM",
    "InputBuffer",
    "Inputs",
    "LeaseAssignment",
    "LeaseContext",
    "LeaseKey",
    "LeaseReady",
    "RecoveringLease",
    "ReleasingLease",
    "ShutdownMode",
    "ShutdownRequest",
    "Starting",
    "State",
    "Stopping",
    "Takeover",
]


@dataclass
class ExporterFSM:
    """Owns the exporter lifecycle's input buffer and single-use runner."""

    inputs: InputBuffer = field(default_factory=InputBuffer)
    runner: FSMRunner[Inputs] | None = field(init=False, default=None)
    """The lifecycle in either serving mode; current_state remains queryable after exit."""

    async def run(self, initial: State) -> None:
        # The exporter name is already in the log context; name the FSM itself.
        self.runner = FSMRunner(initial, self.inputs, name="exporter")
        await self.runner.run()
