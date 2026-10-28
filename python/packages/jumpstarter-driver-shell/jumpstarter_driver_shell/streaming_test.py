import os
import signal
import sys

import pytest
from anyio import fail_after, sleep

from .driver import Shell

EURO = chr(0x20AC)


def _shell(script: str, timeout: int = 10) -> Shell:
    return Shell(methods={"run": {"command": script, "timeout": timeout}})


async def _output(shell: Shell) -> tuple[str, str, int | None]:
    stdout = stderr = ""
    returncode = None
    async for out, err, code in shell.call_method("run", {}):
        stdout += out
        stderr += err
        if code is not None:
            returncode = code
    return stdout, stderr, returncode


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return True


async def _wait_until_gone(pid: int) -> None:
    with fail_after(10):
        while _alive(pid):
            await sleep(0.05)


@pytest.mark.anyio
async def test_abandoning_the_output_kills_the_process_group():
    stream = _shell("sleep 60 & echo $!; wait").call_method("run", {})

    with fail_after(10):
        stdout, _, _ = await stream.__anext__()
        await stream.aclose()

    await _wait_until_gone(int(stdout))


@pytest.mark.anyio
async def test_a_timeout_kills_the_process_group():
    with fail_after(20):
        stdout, stderr, returncode = await _output(_shell("sleep 60 & echo $!; wait", timeout=1))

    assert returncode == 199
    assert "Timeout expired" in stderr
    await _wait_until_gone(int(stdout))


@pytest.mark.anyio
async def test_a_character_split_across_writes_is_decoded_whole(tmp_path):
    writer = tmp_path / "split.py"
    writer.write_text(
        "import sys, time\n"
        "euro = chr(0x20AC).encode()\n"
        "sys.stdout.buffer.write(euro[:2]); sys.stdout.flush()\n"
        "time.sleep(0.3)\n"
        "sys.stdout.buffer.write(euro[2:] + b'\\n'); sys.stdout.flush()\n"
    )

    with fail_after(10):
        stdout, _, returncode = await _output(_shell(f"{sys.executable} {writer}"))

    assert stdout == EURO + "\n"
    assert returncode == 0


@pytest.mark.anyio
async def test_a_background_job_neither_holds_the_method_open_nor_dies_with_it():
    with fail_after(5):
        stdout, _, returncode = await _output(_shell("sleep 60 & echo $!"))

    pid = int(stdout)
    try:
        assert returncode == 0
        assert _alive(pid)
    finally:
        os.kill(pid, signal.SIGKILL)
