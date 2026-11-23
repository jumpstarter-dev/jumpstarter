from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from jumpstarter.exporter.fsm import Available, Starting
from jumpstarter.fsm import ExitFSM
from jumpstarter.fsm_mermaid import mermaid

if TYPE_CHECKING:
    from typing import Any


@dataclass
class Start:
    flag: bool = False

    async def run(self, inputs: Any) -> Middle | ExitFSM | None:
        if self.flag:
            return ExitFSM()
        return Middle() if inputs else None


@dataclass
class Middle:
    async def run(self, inputs: Any) -> End:
        # A state constructed without being returned is not a successor.
        Start()
        return End()


@dataclass
class End:
    async def run(self, inputs: Any) -> ExitFSM:
        return ExitFSM()


@dataclass
class Outer:
    async def run(self, inputs: Any) -> Scope | ExitFSM:
        # Only the state the call constructs is a successor, not its argument.
        return Scope(InnerA()) if inputs else _leave()


@dataclass
class Scope:
    """A composite state: it runs the machine starting at ``first`` inside itself."""

    first: InnerA | InnerB
    runner: Any = None

    async def run(self, inputs: Any) -> Outer:
        await self.runner.run_nested(self.first)
        return Outer()


@dataclass
class InnerA:
    async def run(self, inputs: Any) -> InnerB | None:
        return InnerB() if inputs else None


@dataclass
class InnerB:
    async def run(self, inputs: Any) -> ExitFSM:
        return ExitFSM()


def _leave() -> ExitFSM:
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


def test_mermaid_draws_a_nested_machine_inside_the_state_that_runs_it() -> None:
    assert mermaid(Outer).splitlines() == [
        "stateDiagram-v2",
        "    [*] --> Outer",
        "    Outer --> Scope",
        "    Outer --> [*]",  # through the module-level helper it returns
        "    state Scope {",
        "        [*] --> InnerA",
        "        [*] --> InnerB",
        "        InnerA --> InnerB",
        "        InnerA --> InnerA",
        "        InnerB --> [*]",
        "    }",
        "    Scope --> Outer",
    ]


def test_mermaid_compiles_exporter_fsm() -> None:
    diagram = mermaid(Starting, Available)
    lines = diagram.splitlines()
    # Controller mode starts at Starting, standalone mode at Available.
    assert lines[:3] == ["stateDiagram-v2", "    [*] --> Starting", "    [*] --> Available"]

    opened, closed = lines.index("    state Leased {"), lines.index("    }")
    outer = set(lines[:opened] + lines[closed + 1 :])
    nested = set(lines[opened + 1 : closed])
    assert outer >= {
        "    Starting --> Starting",
        "    Starting --> Available",
        "    Starting --> RecoveringLease",
        "    Starting --> Stopping",
        "    RecoveringLease --> Available",
        "    RecoveringLease --> Leased",
        "    RecoveringLease --> Stopping",
        "    Available --> Available",
        "    Available --> Leased",
        "    Available --> Stopping",
        "    Leased --> Available",
        "    Leased --> Stopping",
        "    Stopping --> [*]",
    }
    # The lease's own states are drawn inside Leased only.
    inner_states = [
        "BeforeLeaseHook",
        "LeaseReady",
        "BeforeLeaseHookFailed",
        "AfterLeaseHook",
        "AfterLeaseHookFailed",
        "ReleasingLease",
        "AwaitingLeaseRelease",
    ]
    for state in inner_states:
        assert any(line.startswith(f"        {state} --> ") for line in nested)
        assert not any(f" {state}" in line for line in outer)
    assert nested >= {
        # A new lease, and the states a lease taken over after a restart can start at.
        "        [*] --> BeforeLeaseHook",
        "        [*] --> LeaseReady",
        "        [*] --> BeforeLeaseHookFailed",
        "        [*] --> AfterLeaseHook",
        "        BeforeLeaseHook --> LeaseReady",
        "        BeforeLeaseHook --> BeforeLeaseHookFailed",
        # A setup that will not start closes its record through cleanup.
        "        BeforeLeaseHook --> AfterLeaseHook",
        "        BeforeLeaseHookFailed --> AwaitingLeaseRelease",
        "        AfterLeaseHook --> AfterLeaseHookFailed",
        "        ReleasingLease --> [*]",
        "        AwaitingLeaseRelease --> [*]",
        # A lease handed to the next exporter process ends without cleanup.
        "        LeaseReady --> [*]",
        "        BeforeLeaseHookFailed --> [*]",
    }
