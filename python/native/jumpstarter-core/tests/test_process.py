"""Check real Windows child and grandchild cleanup without external programs."""

import gc
import json
import os
import subprocess
import sys
import time
from contextlib import contextmanager

import pytest

if sys.platform != "win32":
    pytest.skip("Windows Job Object process ownership", allow_module_level=True)

import win32api
import win32con
import win32event
from jumpstarter_core.process import ChildProcessTree

_CHILD = """
import json, subprocess, sys, time
from pathlib import Path
assert sys.stdin.readline() == "start\\n"
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
Path(sys.argv[1]).write_text(json.dumps({"child": __import__("os").getpid(), "grandchild": grandchild.pid}))
time.sleep(60)
"""


def _wait_ready(path):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            time.sleep(0.01)
    raise TimeoutError("test child did not reach its startup gate")


@contextmanager
def _process(*args, **kwargs):
    with subprocess.Popen(
        [sys.executable, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    ) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


@contextmanager
def _tracked_pid(pid):
    handle = win32api.OpenProcess(win32con.SYNCHRONIZE | win32con.PROCESS_TERMINATE, False, pid)
    try:
        yield handle
    finally:
        # Keep failing tests bounded and remove only their known process.
        if win32event.WaitForSingleObject(handle, 0) == win32event.WAIT_TIMEOUT:
            win32api.TerminateProcess(handle, 1)
            assert win32event.WaitForSingleObject(handle, 5000) == win32event.WAIT_OBJECT_0
        handle.Close()


def _release_gate(process):
    process.stdin.write(b"start\n")
    process.stdin.flush()


@pytest.mark.parametrize("cleanup", ["close", "drop"])
def test_guard_terminates_child_and_later_grandchild(tmp_path, cleanup):
    ready = tmp_path / "ready.json"
    with _process("-c", _CHILD, str(ready)) as child:
        guard = ChildProcessTree(child.pid)
        try:
            _release_gate(child)
            info = _wait_ready(ready)
            with _tracked_pid(info["grandchild"]) as grandchild:
                assert child.poll() is None
                assert win32event.WaitForSingleObject(grandchild, 0) == win32event.WAIT_TIMEOUT
                if cleanup == "close":
                    guard.close()
                    guard.close()
                else:
                    del guard
                    guard = None
                    gc.collect()
                child.wait(timeout=5)
                assert win32event.WaitForSingleObject(grandchild, 5000) == win32event.WAIT_OBJECT_0
        finally:
            if guard is not None:
                guard.close()


def test_owner_process_exit_closes_noninherited_job(tmp_path):
    ready = tmp_path / "ready.json"
    supervisor = """
import subprocess, sys, time
from jumpstarter_core.process import ChildProcessTree
child = subprocess.Popen(
    [sys.executable, "-c", sys.argv[1], sys.argv[2]],
    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
guard = ChildProcessTree(child.pid)
child.stdin.write(b"start\\n")
child.stdin.flush()
time.sleep(60)
"""
    with _process("-c", supervisor, _CHILD, str(ready)) as owner:
        info = _wait_ready(ready)
        with _tracked_pid(info["child"]) as child, _tracked_pid(info["grandchild"]) as grandchild:
            assert win32event.WaitForSingleObject(child, 0) == win32event.WAIT_TIMEOUT
            assert win32event.WaitForSingleObject(grandchild, 0) == win32event.WAIT_TIMEOUT
            # TerminateProcess bypasses Python finalizers, proving OS ownership.
            owner.kill()
            owner.wait(timeout=5)
            assert win32event.WaitForSingleObject(child, 5000) == win32event.WAIT_OBJECT_0
            assert win32event.WaitForSingleObject(grandchild, 5000) == win32event.WAIT_OBJECT_0


@pytest.mark.parametrize("pid", [0, os.getpid()])
def test_guard_rejects_nonchild_process_ids(pid):
    with pytest.raises(OSError, match="child process ID"):
        ChildProcessTree(pid)
