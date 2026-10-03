"""Long-running driver tasks the lease lifecycle must not interrupt: a flash, a firmware update.

A driver runs such work as a **task**: a coroutine started with
:func:`start_task`, which runs in the exporter's event loop independently of
the call that started it. While a task runs, the exporter keeps the device
safe from the lease lifecycle, with nothing to configure:

* No new lease is assigned (the exporter reports ``AFTER_LEASE_HOOK``,
  ``draining: <reason>``, instead of ``AVAILABLE``).
* When the lease ends, ``on_lease_end`` decides: ``wait`` (the default) holds
  the ``afterLease`` hook and the lease's teardown until the task completes or
  times out; ``kill`` cancels it right away. While the exporter waits, the
  lease's session (and so every driver) stays up, so the task can still use
  them, e.g. to run a driver script when it is done.
* Every task has a ``timeout``, which is required. At the timeout the task is
  cancelled. Work blocked in a worker thread can't be cancelled; the exporter
  stops waiting for it a little after the timeout, so a hung task can't wedge
  the exporter.

Tasks live in the exporter process: if the exporter stops or crashes, its
tasks end with it. A task that must pick up again afterwards keeps its own
progress record (a flash, say, journals each step and resumes from it).

In a driver::

    @export
    async def update(self, image: str) -> None:
        await start_task(f"ota-{self.name}", f"OTA update of {self.name}", self._push_update, image, timeout=1800)

    async def _push_update(self, image: str) -> None:
        ...
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import anyio

logger = logging.getLogger(__name__)

OnLeaseEnd = Literal["wait", "kill"]

GRACE = 10.0
"""Seconds past its timeout before the exporter stops waiting for a task that couldn't be cancelled."""

_running: dict[str, Task] = {}


def _now() -> float:
    # The event loop's clock: anyio.current_time() on asyncio, and usable from plain loop callbacks.
    return asyncio.get_running_loop().time()


@dataclass(eq=False)
class Task:
    """A running (or finished) task, see :func:`start_task`."""

    name: str
    reason: str
    timeout: float
    on_lease_end: OnLeaseEnd
    deadline: float
    """When the task is cancelled, in ``_now()``."""
    exception: BaseException | None = None
    """The exception the task raised, if it failed."""
    stopped: bool = False
    """Whether it was cancelled: it timed out, or the lease ended with ``on_lease_end="kill"``."""
    _scope: anyio.CancelScope = field(default_factory=anyio.CancelScope, repr=False)
    _done: anyio.Event = field(default_factory=anyio.Event, repr=False)
    _handle: asyncio.Task | None = field(default=None, repr=False)
    _cancelled_at: float | None = field(default=None, repr=False)

    @property
    def running(self) -> bool:
        return not self._done.is_set()

    @property
    def overdue(self) -> bool:
        """Still running well after it was cancelled (blocked where that can't reach it): no longer waited for."""
        cancelled_at = min(self.deadline, self._cancelled_at) if self._cancelled_at is not None else self.deadline
        return self.running and _now() > cancelled_at + GRACE

    def cancel(self) -> None:
        if self._cancelled_at is None:
            self._cancelled_at = _now()
        self._scope.cancel()

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for the task to end; ``False`` if ``timeout`` passed first."""
        with anyio.move_on_after(timeout):
            await self._done.wait()
        return not self.running


async def start_task(
    name: str,
    reason: str,
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    timeout: float,
    on_lease_end: OnLeaseEnd = "wait",
) -> Task:
    """Run ``fn(*args)`` as a long-running task that the lease lifecycle can't interrupt.

    ``name`` identifies the task among running ones (include a job id, say);
    ``reason`` is what the exporter reports while it waits. ``timeout`` (seconds)
    bounds the task: at the timeout it is cancelled. ``on_lease_end`` is what
    happens if the lease ends first: ``wait`` for the task (up to the timeout)
    before the ``afterLease`` hook and teardown, or ``kill`` it.

    Returns at once; the task runs on in the exporter's event loop. Failures are
    logged and kept in ``task.exception``.
    """
    if not timeout > 0:
        raise ValueError("a task needs a positive timeout, in seconds")
    if on_lease_end not in ("wait", "kill"):
        raise ValueError(f"on_lease_end must be 'wait' or 'kill', got {on_lease_end!r}")
    existing = _running.get(name)
    if existing is not None and existing.running:
        raise ValueError(f"a task named {name!r} is already running")

    deadline = _now() + timeout
    task = Task(name=name, reason=reason, timeout=timeout, on_lease_end=on_lease_end, deadline=deadline)
    task._scope = anyio.CancelScope(deadline=deadline)

    async def run() -> None:
        try:
            with task._scope:
                await fn(*args)
            if task._scope.cancelled_caught:
                task.stopped = True
                logger.warning("%s was stopped (timed out after %ss, or its lease ended)", reason, timeout)
        except Exception as exc:
            task.exception = exc
            logger.exception("%s failed", reason)
        finally:
            task._done.set()
            if _running.get(name) is task:
                del _running[name]

    _running[name] = task
    task._handle = asyncio.get_running_loop().create_task(run(), name=f"task {name}")
    return task


def running_tasks() -> list[Task]:
    """The tasks running in this process."""
    return [task for task in _running.values() if task.running]


def blocking_tasks() -> list[Task]:
    """The tasks the exporter waits for: running, and not overdue."""
    return [task for task in running_tasks() if not task.overdue]


def draining_message(tasks: list[Task]) -> str:
    return "draining: " + "; ".join(task.reason for task in tasks)


async def settle(
    on_waiting: Callable[[list[Task]], Awaitable[None]] | None = None, *, poll: float = 0.5
) -> None:
    """What happens to tasks when a lease ends: cancel ``kill`` tasks, wait for ``wait`` tasks.

    Returns once no task is left to wait for (done, or overdue). ``on_waiting``
    is called with the tasks still waited for, whenever that set changes.
    """
    killed = [task for task in running_tasks() if task.on_lease_end == "kill"]
    for task in killed:
        logger.info("Stopping %s: the lease ended", task.reason)
        task.cancel()
    for task in killed:  # let them unwind
        if not await task.wait(GRACE):
            logger.warning("%s didn't stop when cancelled; no longer waiting for it", task.reason)
    reported: list[str] | None = None
    warned: set[str] = set()
    while True:
        waiting = blocking_tasks()
        for task in running_tasks():
            if task.overdue and task.name not in warned:
                warned.add(task.name)
                logger.warning("%s didn't stop at its timeout; no longer waiting for it", task.reason)
        if not waiting:
            return
        names = [task.name for task in waiting]
        if names != reported and on_waiting is not None:
            await on_waiting(waiting)
        reported = names
        await anyio.sleep(poll)
