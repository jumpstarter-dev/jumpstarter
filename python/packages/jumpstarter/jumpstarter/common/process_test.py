import os
import signal
import sys

import pytest
from anyio import EndOfStream, create_task_group, fail_after, move_on_after, sleep

from jumpstarter.common.process import OutputChunk, open_streaming_process


def python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(") ", 1)[1][0] != "Z"
    except FileNotFoundError:
        return True


async def wait_until_gone(pid: int) -> None:
    with fail_after(5):
        while alive(pid):
            await sleep(0.05)


SPAWN_GRANDCHILD = (
    "import subprocess, sys\n"
    "grandchild = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "print(grandchild.pid, flush=True)\n"
)


async def collect(process) -> dict[str, bytes]:
    output = {"stdout": b"", "stderr": b""}
    async for chunk in process:
        output[chunk.source] += chunk.data
    return output


@pytest.mark.anyio
async def test_output_is_tagged_by_source_until_both_pipes_end():
    code = "import sys; sys.stdout.write('out'); sys.stderr.write('err')"
    with fail_after(10):
        async with open_streaming_process(python(code)) as process:
            assert await collect(process) == {"stdout": b"out", "stderr": b"err"}
            with pytest.raises(EndOfStream):
                await process.receive()


@pytest.mark.anyio
async def test_lines_keep_their_newline_and_the_last_one_may_lack_it():
    code = "import sys, time; sys.stdout.write('a\\nb'); sys.stdout.flush(); time.sleep(0.1); sys.stdout.write('\\nc')"
    with fail_after(10):
        async with open_streaming_process(python(code)) as process:
            lines = [chunk async for chunk in process.lines()]
    assert lines == [OutputChunk("stdout", b"a\n"), OutputChunk("stdout", b"b\n"), OutputChunk("stdout", b"c")]


@pytest.mark.anyio
async def test_long_lines_are_split_at_the_cap():
    with fail_after(10):
        async with open_streaming_process(python("print('x' * 20)"), max_line=8) as process:
            lines = [chunk.data async for chunk in process.lines()]
    assert lines == [b"x" * 8, b"x" * 8, b"xxxx\n"]


@pytest.mark.anyio
async def test_stderr_can_be_merged_into_stdout():
    code = "import sys; sys.stderr.write('err'); sys.stderr.flush(); sys.stdout.write('out')"
    with fail_after(10):
        async with open_streaming_process(python(code), stderr="stdout") as process:
            assert await collect(process) == {"stdout": b"errout", "stderr": b""}


@pytest.mark.anyio
async def test_stderr_can_be_inherited():
    code = "import sys; sys.stderr.write('to the terminal'); sys.stdout.write('out')"
    with fail_after(10):
        async with open_streaming_process(python(code), stderr="inherit") as process:
            assert await collect(process) == {"stdout": b"out", "stderr": b""}


@pytest.mark.anyio
async def test_stdin_is_empty():
    with fail_after(10):
        async with open_streaming_process(python("import sys; print(repr(sys.stdin.read()))")) as process:
            assert await collect(process) == {"stdout": b"''\n", "stderr": b""}


@pytest.mark.anyio
async def test_wait_returns_the_exit_code():
    with fail_after(10):
        async with open_streaming_process(python("raise SystemExit(3)")) as process:
            assert await process.wait() == 3
            assert process.returncode == 3


@pytest.mark.anyio
async def test_a_timed_out_receive_can_be_retried():
    code = "import sys, time; time.sleep(0.3); sys.stderr.write('late')"
    with fail_after(10):
        async with open_streaming_process(python(code)) as process:
            with move_on_after(0.05) as scope:
                await process.receive()
            assert scope.cancelled_caught
            assert await process.receive() == OutputChunk("stderr", b"late")


@pytest.mark.anyio
async def test_stop_kills_a_child_that_ignores_sigterm():
    code = (
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
    )
    with fail_after(10):
        async with open_streaming_process(python(code), stop_grace=0.2) as process:
            await process.receive_line()
            assert await process.stop() == -signal.SIGKILL


@pytest.mark.anyio
async def test_stop_without_grace_kills_at_once():
    with fail_after(3):
        async with open_streaming_process(python("import time; time.sleep(60)"), stop_grace=0) as process:
            assert await process.stop() == -signal.SIGKILL


@pytest.mark.anyio
async def test_stopping_a_new_process_group_kills_grandchildren():
    code = SPAWN_GRANDCHILD + "import time; time.sleep(60)\n"
    with fail_after(10):
        async with open_streaming_process(python(code), new_process_group=True, stop_grace=1) as process:
            grandchild = int((await process.receive_line()).data)
            await process.stop()
    await wait_until_gone(grandchild)


@pytest.mark.anyio
async def test_a_background_job_survives_a_normal_exit():
    with fail_after(10):
        async with open_streaming_process(python(SPAWN_GRANDCHILD), new_process_group=True) as process:
            grandchild = int((await process.receive_line()).data)
            assert await process.wait() == 0
    try:
        assert alive(grandchild)
    finally:
        os.kill(grandchild, signal.SIGKILL)


@pytest.mark.anyio
async def test_leaving_the_context_stops_the_child():
    with fail_after(3):
        async with open_streaming_process(python("import time; time.sleep(60)"), stop_grace=1) as process:
            pass
    assert process.returncode is not None


@pytest.mark.anyio
async def test_cancelling_the_caller_stops_the_child():
    with fail_after(3):
        with move_on_after(0.2):
            async with open_streaming_process(python("import time; time.sleep(60)"), stop_grace=1) as process:
                await process.wait()
    assert process.returncode is not None


@pytest.mark.anyio
async def test_a_generator_closed_from_another_task_stops_the_child():
    started = []

    async def stream_output():
        code = "import time; print('first', flush=True); time.sleep(60)"
        async with open_streaming_process(python(code), stop_grace=1) as process:
            started.append(process)
            async for chunk in process:
                yield chunk

    output = stream_output()

    async def close_output():
        await output.aclose()

    with fail_after(3):
        await output.__anext__()
        async with create_task_group() as tg:
            tg.start_soon(close_output)
    assert started[0].returncode is not None
