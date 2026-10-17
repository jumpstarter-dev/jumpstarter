import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from anyio import BrokenResourceError

from .console import Console, ConsoleStreamDrop, HotkeyDetector
from .driver import PySerial
from jumpstarter.common.utils import serve


def _start_console(client, on_power_cycle=None):
    """Run Console.run() in a thread with a PTY substituted for stdin.

    Returns (master_fd, thread, result_dict). Write keypresses to master_fd;
    the result dict gets an 'exc' key if the console thread raises.
    """
    master_fd, slave_fd = os.openpty()
    slave_file = os.fdopen(slave_fd, "rb", buffering=0)

    mock_stdin = MagicMock()
    mock_stdin.fileno.return_value = slave_fd
    mock_stdin.buffer = slave_file

    result = {}

    def _run():
        with patch("sys.stdin", mock_stdin):
            console = Console(serial_client=client, on_power_cycle=on_power_cycle)
            try:
                console.run()
            except Exception as e:  # noqa: BLE001 - test infrastructure captures all exceptions from console thread
                result["exc"] = e
        slave_file.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return master_fd, t, result


def test_ctrl_b_exits():
    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"a")
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive(), "console did not exit after Ctrl-B x3"
    assert "exc" not in result


def test_ctrl_bracket_triggers_power_cycle():
    power_cycled = threading.Event()

    async def on_power_cycle():
        power_cycled.set()

    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client, on_power_cycle=on_power_cycle)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"\x1d\x1d\x1d")
            assert power_cycled.wait(timeout=5), "power cycle was not triggered"
            assert t.is_alive(), "console exited after power cycle"
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive()
    assert "exc" not in result


def test_ctrl_bracket_without_power_client():
    with serve(PySerial(url="loop://")) as client:
        master_fd, t, result = _start_console(client, on_power_cycle=None)
        try:
            time.sleep(0.1)
            os.write(master_fd, b"\x1d\x1d\x1d")
            time.sleep(0.1)
            assert t.is_alive(), "console exited unexpectedly on Ctrl-] without power client"
            os.write(master_fd, b"\x02\x02\x02")
            t.join(timeout=5)
        finally:
            os.close(master_fd)

    assert not t.is_alive()
    assert "exc" not in result


def test_send_failure_does_not_kill_console(caplog):
    """send() raising BrokenResourceError should not crash the console."""
    caplog.set_level(logging.WARNING, logger="jumpstarter_driver_pyserial.console")

    class _BrokenSendStream:
        """Wrapper that breaks send() but keeps receive() working."""

        def __init__(self, inner):
            self._inner = inner

        async def send(self, data):
            raise BrokenResourceError("simulated send failure")

        async def receive(self):
            return await self._inner.receive()

        async def aclose(self):
            await self._inner.aclose()

    with serve(PySerial(url="loop://")) as client:
        original_stream_async = client.stream_async

        @asynccontextmanager
        async def wrapped_stream_async(method="connect"):
            async with original_stream_async(method=method) as stream:
                yield _BrokenSendStream(stream)

        with patch.object(client, "stream_async", wrapped_stream_async):
            master_fd, t, result = _start_console(client)
            try:
                time.sleep(0.1)
                os.write(master_fd, b"a")  # This send() will raise BrokenResourceError
                time.sleep(0.1)
                assert t.is_alive(), "console crashed on send failure"
                os.write(master_fd, b"\x02\x02\x02")
                t.join(timeout=5)
            finally:
                os.close(master_fd)

    assert not t.is_alive()
    assert "exc" not in result, f"console raised unexpected exception: {result.get('exc')}"

    # Multiple failed sends ("a" plus the two non-exit Ctrl-B bytes) warn exactly once
    warnings = [r for r in caplog.records if "send failed" in r.message]
    assert len(warnings) == 1, f"expected exactly one send-failure warning, got {len(warnings)}"


def test_receive_failure_raises_stream_drop():
    """receive() raising BrokenResourceError should trigger ConsoleStreamDrop."""
    broken_stream = AsyncMock()
    broken_stream.receive.side_effect = BrokenResourceError("simulated receive failure")

    with serve(PySerial(url="loop://")) as client:
        original_stream_async = client.stream_async

        @asynccontextmanager
        async def wrapped_stream_async(method="connect"):
            async with original_stream_async(method=method) as _stream:
                yield broken_stream

        with patch.object(client, "stream_async", wrapped_stream_async):
            master_fd, t, result = _start_console(client)
            try:
                t.join(timeout=5)
            finally:
                os.close(master_fd)

    assert not t.is_alive()
    assert "exc" in result, "console did not raise an exception"
    assert isinstance(result["exc"], ConsoleStreamDrop), f"expected ConsoleStreamDrop, got {type(result['exc'])}"


def test_hotkey_detector_three_presses():
    """Three consecutive presses of a registered key return the key."""
    detector = HotkeyDetector({b"\x02", b"\x1d"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"


def test_hotkey_detector_unrelated_byte_resets():
    """An unrelated byte resets the counter."""
    detector = HotkeyDetector({b"\x02"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"a") is None  # reset
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"


def test_hotkey_detector_alternating_keys_resets():
    """Alternating between registered keys resets rather than accumulating."""
    detector = HotkeyDetector({b"\x02", b"\x1d"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x1d") is None  # different key, reset
    assert detector.feed(b"\x1d") is None
    assert detector.feed(b"\x1d") == b"\x1d"


def test_hotkey_detector_byte_outside_keys():
    """A byte not in the keys set returns None."""
    detector = HotkeyDetector({b"\x02"})
    assert detector.feed(b"x") is None
    assert detector.feed(b"y") is None


def test_hotkey_detector_counter_resets_after_hit():
    """After a hit, the counter resets so a 4th press does not immediately re-fire."""
    detector = HotkeyDetector({b"\x02"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"  # hit
    assert detector.feed(b"\x02") is None  # counter was reset
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"  # hit again after 3 more
