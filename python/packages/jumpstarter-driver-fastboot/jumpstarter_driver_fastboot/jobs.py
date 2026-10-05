"""Flash jobs: on-disk state shared by the flasher and its runner.

A job lives in ``state_dir/jobs/<job_id>/``::

    job.json      immutable: resolved plan, device, identity, fastboot config, settings
    journal.jsonl append-only, fsync'd per record
    output.log    raw fastboot output
    runner.lock   flock held by the live runner (liveness without PIDs)
    gate          "commit" or "cancel": whichever is created first wins
    result.json   terminal state, written once
    exit.json     the exit script's outcome, if the flasher has one

The driver instance is rebuilt every lease, and a job can outlive its lease
and the exporter itself, so everything a job needs to resume is on disk.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import time
from enum import StrEnum
from pathlib import Path
from typing import Any, NotRequired, TypedDict

JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SPAWN_GRACE = 30.0  # seconds a freshly created job counts as active before its runner holds the lock


class JobState(StrEnum):
    """Where a flash job is."""

    STARTING = "starting"
    """Created; the job hasn't taken the device yet."""
    COMMITTING = "committing"
    """Running: past the commit point, it will finish (or fail) on its own."""
    STALLED = "stalled"
    """Its runner is gone without a result (exporter crash): resumed on the flasher's next use."""
    SUCCEEDED = "succeeded"
    FAILED_CLEAN = "failed_clean"
    """Failed before anything was written."""
    FAILED_PARTIAL = "failed_partial"
    """Failed after writing: the device was left in fastboot, not booted."""
    INTERRUPTED = "interrupted"
    """The device went away; ``resume`` once it is back."""
    CANCELLED = "cancelled"
    """Cancelled before the first write."""

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_STATES


TERMINAL_STATES = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED_CLEAN, JobState.FAILED_PARTIAL, JobState.INTERRUPTED, JobState.CANCELLED}
)
TERMINAL = tuple(sorted(state.value for state in TERMINAL_STATES))


class ExitInfo(TypedDict):
    """The job's exit script, if the flasher has one."""

    state: str
    """``waiting``, ``running``, ``succeeded``, ``failed``, or ``skipped``."""
    error: NotRequired[str | None]
    reason: NotRequired[str | None]
    output: NotRequired[list[str]]


class JobInfo(TypedDict):
    """A flash job, as ``start``, ``status``, ``jobs``, ``cancel``, and ``resume`` report it."""

    job_id: str
    state: str
    """A :class:`JobState` value."""
    device: str
    stage_id: str
    source: str | None
    created: float
    steps_done: int
    total_steps: int
    message: str | None
    failed_step: str | None
    critical_incomplete: bool
    """A critical step (bootloader chain, say) failed part-way: the device may need recovery."""
    exit: NotRequired[ExitInfo]
    step_index: NotRequired[int]
    step_name: NotRequired[str]
    critical: NotRequired[bool]


class JobError(RuntimeError):
    pass


def describe(op: dict[str, Any]) -> str:
    return op.get("describe") or op["op"]


def append_record(path: Path, event: str, /, **fields: Any) -> dict[str, Any]:
    record = {"ts": time.time(), "event": event, **fields}
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(record) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return record


def read_records(path: Path, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Read complete JSON lines from ``offset``; return (records, new_offset)."""
    if not path.exists():
        return [], offset
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n") + 1
    records = []
    for line in data[:end].splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records, offset + end


def atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def lock_held(path: Path) -> bool:
    """True if another open file description holds an exclusive flock on ``path``."""
    try:
        fd = os.open(path, os.O_RDWR)
    except FileNotFoundError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return True
        raise
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def claim_gate(job_dir: Path, value: str) -> str:
    """Atomically claim the commit/cancel gate; return the winning value."""
    gate = job_dir / "gate"
    try:
        fd = os.open(gate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        for _ in range(50):
            text = gate.read_text().strip()
            if text:
                return text
            time.sleep(0.01)
        return gate.read_text().strip()
    try:
        os.write(fd, value.encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return value


def device_lock_path(state_dir: Path, device: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", device)
    path = state_dir / "locks" / f"{safe}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return path


class JobStore:
    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, job_id: str) -> Path:
        if not JOB_ID.match(job_id):
            raise JobError(f"invalid job id {job_id!r}")
        return self.root / job_id

    def exists(self, job_id: str) -> bool:
        return (self.path(job_id) / "job.json").exists()

    def create(self, job_id: str, payload: dict[str, Any]) -> Path:
        job_dir = self.path(job_id)
        job_dir.mkdir(parents=False, exist_ok=False)
        (job_dir / "runner.lock").touch()
        atomic_write_json(job_dir / "job.json", payload)
        append_record(job_dir / "journal.jsonl", "created", stage_id=payload.get("stage_id"))
        return job_dir

    def load(self, job_id: str) -> dict[str, Any]:
        path = self.path(job_id) / "job.json"
        if not path.exists():
            raise JobError(f"unknown job {job_id!r}")
        return json.loads(path.read_text())

    def result(self, job_id: str) -> dict[str, Any] | None:
        path = self.path(job_id) / "result.json"
        return json.loads(path.read_text()) if path.exists() else None

    def alive(self, job_id: str) -> bool:
        return lock_held(self.path(job_id) / "runner.lock")

    def gate(self, job_id: str) -> str | None:
        path = self.path(job_id) / "gate"
        return path.read_text().strip() or None if path.exists() else None

    def ids(self) -> list[str]:
        jobs = [p.parent for p in self.root.glob("*/job.json")]
        jobs.sort(key=lambda p: (p / "job.json").stat().st_mtime)
        return [p.name for p in jobs]

    def info(self, job_id: str) -> dict[str, Any]:
        job = self.load(job_id)
        records, _ = read_records(self.path(job_id) / "journal.jsonl")
        result = self.result(job_id)
        done = {r["index"] for r in records if r["event"] == "step-done"}
        current = next((r for r in reversed(records) if r["event"] == "step-start"), None)
        plan = job["plan"]
        if result is not None:
            state = result["state"]
        elif self.alive(job_id):
            state = "committing" if self.gate(job_id) == "commit" else "starting"
        elif time.time() - job["created"] < SPAWN_GRACE and not any(r["event"] == "runner-start" for r in records):
            state = "starting"
        else:
            state = "stalled"  # runner gone without a result: resumable
        info = {
            "job_id": job_id,
            "state": state,
            "device": job["device"],
            "stage_id": job["stage_id"],
            "source": job.get("source"),
            "created": job["created"],
            "steps_done": len(done),
            "total_steps": len(plan),
            "message": (result or {}).get("message"),
            "failed_step": (result or {}).get("failed_step"),
            "critical_incomplete": (result or {}).get("critical_incomplete", False),
        }
        exit_info = self._exit_info(job_id, job, records, finished=result is not None)
        if exit_info is not None:
            info["exit"] = exit_info
        if current is not None and state not in TERMINAL:
            op = plan[current["index"]]
            info |= {
                "step_index": current["index"] + 1,
                "step_name": describe(op),
                "critical": op.get("critical", False),
            }
        return info

    def _exit_info(
        self, job_id: str, job: dict[str, Any], records: list[dict[str, Any]], *, finished: bool
    ) -> dict[str, Any] | None:
        """The exit script's state: waiting (job running), running, or its outcome."""
        if not job.get("exit"):
            return None
        path = self.path(job_id) / "exit.json"
        if path.exists():
            data = json.loads(path.read_text())
            return {"state": data["state"], "error": data.get("error"), "output": data.get("output", [])}
        skipped = next((r for r in records if r["event"] == "exit-skipped"), None)
        if skipped is not None:
            return {"state": "skipped", "reason": skipped.get("reason")}
        if finished and not self.alive(job_id):
            return {"state": "skipped", "reason": "the runner stopped before running it"}
        if any(r["event"] == "exit-running" for r in records):
            return {"state": "running"}
        return {"state": "waiting"}

    def active(self, device: str) -> str | None:
        """The job currently owning ``device``: not terminal, and running, starting, or resumable."""
        for job_id in reversed(self.ids()):
            job = self.load(job_id)
            if job["device"] != device:
                continue
            if self.result(job_id) is None:
                return job_id
        return None

    def wait_started(self, job_id: str, timeout: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.alive(job_id) or self.result(job_id) is not None:
                return True
            time.sleep(0.1)
        return False


def runner_timeout(settings: dict[str, Any]) -> float:
    """The exporter task's timeout: past the runner's own bound, so only a hung runner ever hits it.

    The runner checks ``max_job_duration`` between steps; a step can then still
    take its retries' stall timeouts and a re-enumeration wait.
    """
    step = (settings["step_retries"] + 1) * (settings["stall_timeout"] + settings["reenumerate_timeout"])
    return settings["max_job_duration"] + step + 600

