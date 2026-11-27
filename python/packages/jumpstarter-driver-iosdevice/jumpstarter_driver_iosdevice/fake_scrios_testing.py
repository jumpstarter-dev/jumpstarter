"""Test helpers for the HMI driver: a fake ``scrios`` that speaks the real one's framed protocol (see
``fake_scrios``).
"""

import base64
import json
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

from .hid_usages import USAGES

FAKE_SCRIOS = Path(__file__).with_name("fake_scrios.py")


def write_fake_scrios(directory: Path, name: str = "scrios") -> Path:
    """Write an executable stand-in for ``scrios`` into ``directory``.

    It records how it was started and what it was told on stdin to ``$FAKE_SCRIOS_RECORD``, streams synthetic access
    units, and can be steered through ``$FAKE_SCRIOS_CONTROL`` (see ``fake_scrios`` for both and for the other
    ``FAKE_SCRIOS_*`` settings).
    """
    path = directory / name
    path.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE_SCRIOS))} "$@"\n')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def read_record(path: Path) -> list[dict]:
    """What the fake scrios appended to ``$FAKE_SCRIOS_RECORD``, in order."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def send_operation(path: Path, **operation) -> None:
    """Append an operation to ``$FAKE_SCRIOS_CONTROL`` (``{"op": "exit", "code": 3}``, ...)."""
    with path.open("a") as control:
        control.write(json.dumps(operation) + "\n")


def commands(record: list[dict]) -> list[str]:
    """The lines the fake was given on stdin, in order."""
    return [r["line"] for r in record if r["event"] == "command"]


def touches(record: list[dict]) -> list[tuple]:
    """The touches the fake was given, as ``("touch", kind, x, y, finger)`` (what a conformance suite looks for)."""
    found = []
    for line in commands(record):
        parts = line.split()
        if parts[:1] == ["touch"] and len(parts) == 5:
            found.append(("touch", parts[1], float(parts[2]), float(parts[3]), int(parts[4])))
    return found


def input_events(record: list[dict]) -> list[tuple]:
    """What the fake was given as input, in order: ``("touch", kind, x, y, finger)`` and ``("keyboard", code, down)``
    (the key's ``KeyboardEvent.code``, as the HMI package's fake input sink records it).
    """
    codes = {usage: code for code, usage in USAGES.items()}
    found: list[tuple] = []
    for line in commands(record):
        parts = line.split()
        if parts[:1] == ["touch"] and len(parts) == 5:
            found.append(("touch", parts[1], float(parts[2]), float(parts[3]), int(parts[4])))
        elif parts[:1] == ["kb"] and len(parts) == 3 and parts[1] in ("down", "up"):
            usage = int(parts[2], 16)
            found.append(("keyboard", codes.get(usage, f"usage {usage:02x}"), parts[1] == "down"))
    return found


def keyboard_events(record: list[dict]) -> list[tuple]:
    """The keys the fake was given, as ``("kb", "down" | "up", usage)`` with the usage as a number."""
    found = []
    for line in commands(record):
        parts = line.split()
        if parts[:1] == ["kb"] and len(parts) == 3 and parts[1] in ("down", "up"):
            found.append(("kb", parts[1], int(parts[2], 16)))
    return found


def typed(record: list[dict]) -> list[str]:
    """The texts the fake was given with ``kb type``, decoded, one per command."""
    return [
        base64.b64decode(line.split()[2]).decode("utf-8")
        for line in commands(record)
        if line.startswith("kb type ") and len(line.split()) == 3
    ]


@dataclass
class FakeScrios:
    """A fake scrios on disk with its record and control files, for a test to look at and steer."""

    path: Path
    record: Path
    control: Path

    @classmethod
    def create(cls, directory: Path, monkeypatch) -> "FakeScrios":
        """Write the fake into ``directory`` and point its environment at its record and control files."""
        fake = cls(write_fake_scrios(directory), directory / "record.jsonl", directory / "control.jsonl")
        fake.control.write_text("")
        monkeypatch.setenv("FAKE_SCRIOS_RECORD", str(fake.record))
        monkeypatch.setenv("FAKE_SCRIOS_CONTROL", str(fake.control))
        return fake

    def events(self, name: str | None = None) -> list[dict]:
        """The record, or the events of one kind."""
        return [r for r in read_record(self.record) if name is None or r["event"] == name]

    def starts(self) -> list[dict]:
        """One entry per run, with its arguments and its pid."""
        return self.events("start")

    def commands(self, line: str | None = None) -> list[str]:
        """The lines it was given on stdin, in order (only ``line`` if given)."""
        return [c for c in commands(read_record(self.record)) if line is None or c == line]

    def touches(self) -> list[tuple]:
        """The touches it was given (see :func:`touches`)."""
        return touches(read_record(self.record))

    def input_events(self) -> list[tuple]:
        """The touches and keys it was given, in order (see :func:`input_events`)."""
        return input_events(read_record(self.record))

    def keyboard(self) -> list[tuple]:
        """The keys it was given (see :func:`keyboard_events`)."""
        return keyboard_events(read_record(self.record))

    def typed(self) -> list[str]:
        """The texts it was given (see :func:`typed`)."""
        return typed(read_record(self.record))

    def argv(self, run: int = -1) -> list[str]:
        """The arguments of a run (the last by default)."""
        return self.starts()[run]["argv"]

    def op(self, **operation) -> None:
        """Ask the running fake to do something (see ``fake_scrios.OPERATIONS``)."""
        send_operation(self.control, **operation)

    def pid(self, run: int = -1) -> int:
        """The process id of a run (the last by default)."""
        return self.starts()[run]["pid"]
