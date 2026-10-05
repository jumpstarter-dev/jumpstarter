"""Job runner: owns the commit phase of a flash job.

The flasher runs it as a long-running exporter task (``jumpstarter.driver.tasks``):
client disconnects don't stop it, a lease that ends waits for it, and no new
lease is assigned until it is done. Progress is journaled step by step, so a
runner that dies anyway (exporter crash, container stop, host power loss) is
resumed from the first unfinished step the next time the flasher is used.

Safety rules the runner enforces by construction:

* It only speaks fastboot. It never touches power, buttons, or consoles, so it
  cannot power-cycle a device mid-write.
* Recovery is retry-without-reboot: a failed step is re-issued (``oem``
  commands are never retried: they may not be idempotent), and a device that
  drops off the bus is waited for. Bootloader and fastbootd are switched with
  a fastboot command, only when a step needs the other one.
* Before every step the device's serial is re-checked; a different device on
  the bench port aborts the job.
* On unrecoverable failure it leaves the device in fastboot and skips the
  final ``reboot``/``continue``, so a partially written device never boots.
* Lease end doesn't stop it. A local ``abort --force-unsafe`` stops it
  between steps. The only other way is the task's timeout, well past
  ``max_job_duration``, for a hung runner.

Host-side abort (exporter host only)::

    python -m jumpstarter_driver_fastboot.runner abort <job_dir> --force-unsafe
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import shlex
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from anyio import to_thread

from .fastboot import Fastboot, FastbootError
from .jobs import append_record, atomic_write_json, claim_gate, describe, read_records


class StepFailed(Exception):
    """A step failed; it is retried (without rebooting) up to ``step_retries``."""


class IdentityMismatch(StepFailed):
    """A different device is on the bench port: never retried, nothing more is written."""


class Interrupted(Exception):
    """The device went away and didn't come back in time; the job can be resumed."""


class Aborted(Exception):
    """Stopped on the exporter host (``runner abort --force-unsafe``)."""


class Runner:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir
        self.job = json.loads((job_dir / "job.json").read_text())
        self.settings = self.job["settings"]
        self.config = self.job["fastboot"]
        self.journal = job_dir / "journal.jsonl"
        self.output = open(job_dir / "output.log", "a", buffering=1)  # noqa: SIM115 - open for the runner's life
        self.fb = Fastboot(
            usb_port=self.config.get("usb_port"),
            serial=self.config.get("serial"),
            address=self.config.get("address"),
            binary=self.config["binary"],
            command_timeout=self.config["command_timeout"],
            probe_timeout=self.config.get("probe_timeout", 5.0),
        )

    def record(self, event: str, /, **fields: Any) -> None:
        append_record(self.journal, event, **fields)

    def finish(self, state: str, message: str, **fields: Any) -> None:
        result = {"state": state, "message": message, "finished": time.time(), **fields}
        atomic_write_json(self.job_dir / "result.json", result)
        self.record("result", **result)

    def abort_requested(self) -> bool:
        return (self.job_dir / "abort").exists()

    def _on_output(self, text: str) -> None:
        self.output.write(text)

    # -- fastboot ----------------------------------------------------------------

    async def wait_device(self) -> None:
        """Wait (up to ``reenumerate_timeout``) for the device, and check it is the job's device.

        Raises :class:`Interrupted` if it doesn't appear, :class:`IdentityMismatch`
        if another device is there.
        """
        timeout = self.settings["reenumerate_timeout"]
        if not await self.fb.present():
            self.record("wait-device", timeout=timeout)
            if not await self.fb.wait_present(timeout):
                raise Interrupted(f"device on {self.fb.label} did not appear in fastboot within {timeout}s")
        expected = self.config.get("expected_serial")
        serial = await self.fb.getvar("serialno")
        if expected is not None and serial is not None and serial != expected:
            raise IdentityMismatch(
                f"identity mismatch: expected serialno {expected!r}, found {serial!r}; refusing to write"
            )

    async def prepare(self, op: dict[str, Any]) -> None:
        """Bring the device into the fastboot mode ``op`` needs (bootloader or fastbootd)."""
        mode = op["mode"]
        userspace = await self.fb.is_userspace()
        if userspace == (mode == "userspace"):
            return
        target = "fastboot" if mode == "userspace" else "bootloader"
        self.record("mode-switch", target=target)
        await self.fb.run(["reboot", target], timeout=self.config["command_timeout"], on_output=self._on_output)
        await asyncio.sleep(self.config.get("mode_switch_settle", 2.0))
        await self.wait_device()
        if await self.fb.is_userspace() != (mode == "userspace"):
            raise StepFailed(f"device did not come back in {target} mode")

    async def execute(self, op: dict[str, Any]) -> None:
        stall = self.settings["stall_timeout"]
        out = self._on_output
        match op["op"]:
            case "flash":
                await self.fb.run(["flash", op["target"], op["path"]], stall_timeout=stall, on_output=out)
            case "erase":
                await self.fb.run(["erase", op["target"]], stall_timeout=stall, on_output=out)
            case "set_active":
                await self.fb.run(["set_active", op["target"]], stall_timeout=stall, on_output=out)
            case "oem":
                await self.fb.run(["oem", *shlex.split(op["command"])], stall_timeout=stall, on_output=out)
            case "mode":
                pass  # prepare() switched modes
            case other:
                raise StepFailed(f"unknown fastboot operation {other!r}")

    def retries(self, op: dict[str, Any]) -> int:
        """How often a failed ``op`` is retried: never for ``oem`` (it may not be idempotent)."""
        return 0 if op["op"] == "oem" else self.settings["step_retries"]

    async def finalize(self) -> None:
        """After every step succeeded: boot the device (``finally``), unless told to stay."""
        action = self.config.get("finally", "reboot")
        self.record("finalize", action=action)
        if action == "stay":
            return
        timeout = self.config["command_timeout"]
        if action == "continue" and not await self.fb.is_userspace():
            await self.fb.run(["continue"], timeout=timeout, on_output=self._on_output)
        else:
            await self.fb.run(["reboot"], timeout=timeout, on_output=self._on_output)

    # -- the job -----------------------------------------------------------------

    async def run_step(self, index: int, op: dict[str, Any]) -> None:
        if op["op"] == "sleep":
            await asyncio.sleep(op["seconds"])
            return
        retries = self.retries(op)
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.wait_device()
                await self.prepare(op)
                await self.execute(op)
                return
            except IdentityMismatch:
                raise
            except (FastbootError, StepFailed) as exc:
                if attempt > retries:
                    raise StepFailed(f"{describe(op)}: {exc}") from exc
                self.record("retry", index=index, attempt=attempt, error=str(exc)[-500:])
                await asyncio.sleep(min(2 * attempt, 10))

    async def run(self, resume: bool) -> None:
        plan = self.job["plan"]
        records, _ = read_records(self.journal)
        done = {r["index"] for r in records if r["event"] == "step-done"}
        self.record("runner-start", pid=os.getpid(), resume=resume, steps_done=len(done))

        gate = claim_gate(self.job_dir, "commit")
        if gate == "cancel":
            self.finish("cancelled", "cancelled before the first write")
            return

        deadline = time.monotonic() + self.settings["max_job_duration"]
        current: dict[str, Any] | None = None
        try:
            for index, op in enumerate(plan):
                if index in done:
                    continue
                if self.abort_requested():
                    raise Aborted("aborted on the exporter host (--force-unsafe)")
                if time.monotonic() > deadline:
                    raise StepFailed(f"job exceeded max_job_duration ({self.settings['max_job_duration']}s)")
                current = op | {"index": index}
                self.record("step-start", index=index, step=describe(op), critical=op.get("critical", False))
                await self.run_step(index, op)
                self.record("step-done", index=index)
                done.add(index)
                current = None  # between steps: nothing is mid-write
            try:
                await self.finalize()
            except (FastbootError, StepFailed) as exc:
                self.finish("succeeded", f"all steps completed; finalizing failed: {exc}")
                return
            self.finish("succeeded", "all steps completed")
        except Interrupted as exc:
            self.finish("interrupted", str(exc), **self._failure_fields(current))
        except (StepFailed, Aborted, FastbootError) as exc:
            # A step that failed may have written part of itself; any completed step wrote.
            wrote = current is not None or bool(done)
            state = "failed_partial" if wrote else "failed_clean"
            self.finish(state, f"{exc}; device left in fastboot", **self._failure_fields(current))

    def _failure_fields(self, current: dict[str, Any] | None) -> dict[str, Any]:
        if current is None:
            return {"critical_incomplete": False}
        return {
            "failed_step": describe(current),
            "failed_index": current["index"],
            "critical_incomplete": bool(current.get("critical")),
        }


def _acquire(path: Path, *, blocking_timeout: float | None) -> int | None:
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = None if blocking_timeout is None else time.monotonic() + blocking_timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            if deadline is None or time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.5)


async def run_job(
    job_dir: Path,
    resume: bool,
    *,
    after: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run (or resume) the job in ``job_dir`` to a result, then ``after()``.

    The job's ``runner.lock`` is held throughout, ``after()`` included, so the
    job counts as live until both are done. Returns at once if another runner
    owns the job or it already has a result.
    """
    job_fd = _acquire(job_dir / "runner.lock", blocking_timeout=0)
    if job_fd is None:
        return  # another runner owns this job
    try:
        if (job_dir / "result.json").exists():
            return
        runner = Runner(job_dir)
        try:
            device_fd = await to_thread.run_sync(lambda: _acquire(Path(runner.job["device_lock"]), blocking_timeout=60))
            if device_fd is None:
                records, _ = read_records(runner.journal)
                if not any(r["event"] == "step-done" for r in records):
                    runner.finish("failed_clean", f"device {runner.job['device']} is busy with another job")
                else:
                    runner.record("device-busy")  # leave unfinished so it is resumed later
                return
            try:
                await runner.run(resume)
            except Exception as exc:
                runner.record("runner-crash", error=repr(exc))
                raise
            finally:
                os.close(device_fd)
        finally:
            runner.output.close()
        if after is not None:
            await after()
    finally:
        os.close(job_fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m jumpstarter_driver_fastboot.runner")
    sub = parser.add_subparsers(dest="command", required=True)
    abort = sub.add_parser("abort", help="stop a job between steps (exporter host only)")
    abort.add_argument("job_dir", type=Path)
    abort.add_argument("--force-unsafe", action="store_true", required=True)
    args = parser.parse_args(argv)
    (args.job_dir / "abort").touch()
    print(f"abort requested for {args.job_dir}; the runner stops before its next step", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
