import asyncio
import signal
import sys

import pytest
from anyio import CancelScope

from .signal import signal_handler


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
async def test_windows_signal_cancels_and_restores_handlers(monkeypatch, capsys, signum):
    monkeypatch.setattr(sys, "platform", "win32")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    scope = CancelScope()
    task = asyncio.create_task(signal_handler(scope))
    try:
        await asyncio.sleep(0)
        assert not task.done()
        for sig, handler in previous.items():
            assert signal.getsignal(sig) != handler

        # Repeated delivery before the loop resumes must not complete the
        # waiting future twice or leave the process using our handlers.
        signal.raise_signal(signum)
        signal.raise_signal(signum)
        await asyncio.wait_for(task, timeout=1)
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert scope.cancel_called
    assert signal.Signals(signum).name in capsys.readouterr().err
    for sig, handler in previous.items():
        assert signal.getsignal(sig) == handler


@pytest.mark.anyio
async def test_windows_signal_handlers_restored_on_cancellation(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    scope = CancelScope()
    task = asyncio.create_task(signal_handler(scope))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not scope.cancel_called
    for sig, handler in previous.items():
        assert signal.getsignal(sig) == handler


@pytest.mark.anyio
async def test_windows_signal_handlers_restored_on_setup_failure(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    previous = signal.getsignal(signal.SIGINT)
    install_handler = signal.signal

    def fail_second_handler(signum, handler):
        if signum == signal.SIGTERM:
            raise ValueError("cannot install SIGTERM handler")
        return install_handler(signum, handler)

    monkeypatch.setattr(signal, "signal", fail_second_handler)
    with pytest.raises(ValueError, match="cannot install SIGTERM handler"):
        await signal_handler(CancelScope())
    assert signal.getsignal(signal.SIGINT) == previous
