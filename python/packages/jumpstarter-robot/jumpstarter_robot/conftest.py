"""Shared fixtures: fresh session state per test and in-process Robot Framework runs."""

from __future__ import annotations

import gc
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from robot.running.namespace import IMPORTER

from .testing import reset
from jumpstarter.config.env import JMP_CLIENT_CONFIG, JUMPSTARTER_HOST


@pytest.fixture(autouse=True)
def fresh_sessions(monkeypatch):
    """Every test starts with no sessions, factories, scoped resources or shell."""
    monkeypatch.delenv(JUMPSTARTER_HOST, raising=False)
    monkeypatch.delenv(JMP_CLIENT_CONFIG, raising=False)
    monkeypatch.delenv("JMP_LEASE", raising=False)
    _forget_drivers()
    yield
    _forget_drivers()


def _forget_drivers() -> None:
    """Drop earlier tests' Driver instances, which ``instances()`` (a WeakSet) would still list.

    Robot Framework keeps the libraries of the last in-process run in its
    import cache until the next run starts, so clear that cache too.
    """
    reset()
    IMPORTER.reset()
    gc.collect()


@dataclass
class RobotRun:
    rc: int
    tests: dict[str, Any]
    result: Any

    def status(self, name: str) -> str:
        return self.tests[name].status

    def message(self, name: str) -> str:
        return self.tests[name].message

    def assert_passed(self) -> None:
        failed = {name: t.message for name, t in self.tests.items() if t.status != "PASS"}
        assert not failed, failed
        assert self.rc == 0


def write(directory: Path, name: str, content: str) -> Path:
    """Write a dedented test file and return its path."""
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).lstrip())
    return path


@pytest.fixture
def run_robot(tmp_path):
    """Run Robot Framework in this process and return the rc and per-test results."""
    import robot
    from robot.api import ExecutionResult

    runs = iter(range(1000))

    def run(*sources: Path, **options: Any) -> RobotRun:
        out = tmp_path / f"results-{next(runs)}"
        out.mkdir()
        output = out / "output.xml"
        rc = robot.run(
            *map(str, sources),
            outputdir=str(out),
            output=str(output),
            report="NONE",
            log="NONE",
            console="none",
            **options,
        )
        result = ExecutionResult(str(output))
        return RobotRun(rc, {test.name: test for test in result.suite.all_tests}, result)

    return run
