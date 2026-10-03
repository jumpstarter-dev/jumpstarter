import time

import anyio
import pytest
from anyio import to_thread

from . import tasks
from .tasks import blocking_tasks, draining_message, running_tasks, settle, start_task

pytestmark = pytest.mark.anyio


async def test_task_runs_on_after_the_call_that_started_it():
    events = []

    async def work(n):
        await anyio.sleep(0.2)
        events.append(n)

    task = await start_task("ota-1", "OTA update 1", work, 7, timeout=60)
    assert task.running and running_tasks() == [task] and events == []
    assert await task.wait(10)
    assert events == [7] and not task.stopped and task.exception is None
    assert running_tasks() == []


async def test_timeout_cancels_the_task():
    task = await start_task("slow", "slow work", anyio.sleep, 30, timeout=0.2)
    assert await task.wait(10)
    assert task.stopped


async def test_failures_are_kept():
    async def boom():
        raise RuntimeError("flash failed")

    task = await start_task("bad", "bad work", boom, timeout=60)
    assert await task.wait(10)
    assert isinstance(task.exception, RuntimeError)


async def test_names_are_unique_and_arguments_checked():
    task = await start_task("job", "job", anyio.sleep, 0.5, timeout=60)
    with pytest.raises(ValueError, match="already running"):
        await start_task("job", "job", anyio.sleep, 0, timeout=60)
    with pytest.raises(TypeError):
        await start_task("a", "b", anyio.sleep, 0)  # ty: ignore[missing-argument]
    with pytest.raises(ValueError, match="positive timeout"):
        await start_task("a", "b", anyio.sleep, 0, timeout=0)
    with pytest.raises(ValueError, match="on_lease_end"):
        await start_task("a", "b", anyio.sleep, 0, timeout=1, on_lease_end="later")  # ty: ignore[invalid-argument-type]
    await task.wait(10)


async def test_settle_kills_and_waits():
    flash = await start_task("flash", "flash", anyio.sleep, 0.5, timeout=60)
    logcat = await start_task("logcat", "log capture", anyio.sleep, 60, timeout=600, on_lease_end="kill")
    reports = []

    async def on_waiting(waiting):
        reports.append(draining_message(waiting))

    with anyio.fail_after(10):
        await settle(on_waiting, poll=0.05)
    assert logcat.stopped  # cancelled at once
    assert not flash.running and not flash.stopped  # waited for: completed
    assert reports == ["draining: flash"]


async def test_overdue_tasks_are_not_waited_for(monkeypatch):
    monkeypatch.setattr(tasks, "GRACE", 0.2)
    # Blocked in a worker thread, where cancellation can't reach it.
    hung = await start_task("hung", "hung work", to_thread.run_sync, time.sleep, 2, timeout=0.2)
    with anyio.fail_after(10):
        await settle(poll=0.05)
    assert hung.running and hung.overdue and blocking_tasks() == []
    assert await hung.wait(10)
