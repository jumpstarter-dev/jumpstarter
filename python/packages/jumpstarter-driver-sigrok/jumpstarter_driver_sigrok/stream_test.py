import os
import sys

import pytest
from anyio import fail_after, move_on_after, sleep

from .common import CaptureConfig
from .driver import Sigrok


def _streaming(monkeypatch, code: str) -> Sigrok:
    driver = Sigrok(driver="demo", executable=sys.executable)
    monkeypatch.setattr(driver, "_build_stream_command", lambda cfg: [sys.executable, "-c", code])
    return driver


async def _wait_until_gone(pid: int) -> None:
    with fail_after(10):
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            await sleep(0.05)


@pytest.mark.anyio
async def test_capture_stream_yields_the_output_bytes(monkeypatch):
    driver = _streaming(monkeypatch, "import sys; sys.stdout.buffer.write(bytes(range(256)) * 40)")

    with fail_after(10):
        chunks = [chunk async for chunk in driver.capture_stream(CaptureConfig())]

    assert b"".join(chunks) == bytes(range(256)) * 40


@pytest.mark.anyio
async def test_closing_the_stream_early_stops_sigrok(monkeypatch):
    driver = _streaming(monkeypatch, "import os, sys, time; print(os.getpid(), flush=True); time.sleep(60)")
    stream = driver.capture_stream(CaptureConfig())

    with fail_after(10):
        pid = int(await stream.__anext__())
        await stream.aclose()

    await _wait_until_gone(pid)


@pytest.mark.anyio
async def test_cancelling_the_reader_stops_a_sigrok_that_ignores_sigterm(monkeypatch):
    code = (
        "import os, signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print(os.getpid(), flush=True); time.sleep(60)"
    )
    driver = _streaming(monkeypatch, code)
    output = b""

    with move_on_after(0.5):
        async for chunk in driver.capture_stream(CaptureConfig()):
            output += chunk

    await _wait_until_gone(int(output))
