from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jumpstarter.exporter.fsm import Starting
from jumpstarter.fsm import ExitFSM
from jumpstarter.fsm_mermaid import mermaid

if TYPE_CHECKING:
    from typing import Any


@dataclass
class Start:
    flag: bool = False

    async def run(self, inputs: Any, resources: AsyncExitStack) -> Middle | ExitFSM | None:
        if self.flag:
            return ExitFSM()
        return Middle() if inputs else None


@dataclass
class Middle:
    async def run(self, inputs: Any, resources: AsyncExitStack) -> End:
        return End()


@dataclass
class End:
    async def run(self, inputs: Any, resources: AsyncExitStack) -> ExitFSM:
        return ExitFSM()


def test_mermaid_compiles_static_transitions() -> None:
    lines = set(mermaid(Start()).splitlines())
    assert lines == {
        "stateDiagram-v2",
        "    [*] --> Start",
        "    Start --> [*]",
        "    Start --> Middle",
        "    Start --> Start",
        "    Middle --> End",
        "    End --> [*]",
    }


def test_mermaid_compiles_exporter_fsm() -> None:
    diagram = mermaid(Starting)
    lines = set(diagram.splitlines())
    for state in [
        "Starting",
        "RecoveringLease",
        "Available",
        "BeforeLeaseHook",
        "LeaseReady",
        "BeforeLeaseHookFailed",
        "AfterLeaseHook",
        "AfterLeaseHookFailed",
        "ReleasingLease",
        "AwaitingLeaseRelease",
        "ClosingLease",
        "Stopping",
    ]:
        assert any(line.startswith(f"    {state} ") or line.endswith(f" --> {state}") for line in lines)
    assert "    [*] --> Starting" in lines
    assert "    Starting --> Available" in lines
    assert "    Starting --> RecoveringLease" in lines
    assert "    RecoveringLease --> Available" in lines
    assert "    RecoveringLease --> AfterLeaseHook" in lines
    assert "    Available --> BeforeLeaseHook" in lines
    assert "    Available --> Stopping" in lines
    assert "    Available --> Available" in lines
    assert "    ClosingLease --> Available" in lines
    assert "    ClosingLease --> Stopping" in lines
    assert "    Stopping --> [*]" in lines
    assert "    BeforeLeaseHookFailed --> AwaitingLeaseRelease" in lines
    # A lease handed to the next exporter process skips cleanup.
    assert "    LeaseReady --> ClosingLease" in lines
    assert "    BeforeLeaseHookFailed --> ClosingLease" in lines
