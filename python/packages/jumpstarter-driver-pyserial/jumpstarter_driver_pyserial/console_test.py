import logging
import os
import termios
import threading
import time
import tty
from contextlib import ExitStack, asynccontextmanager, contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from anyio import BrokenResourceError
from jumpstarter_driver_power.client import PowerClient

from .console import Console, ConsoleStreamDrop, HotkeyDetector
from .driver import PySerial
from jumpstarter.common.utils import serve

EXIT_HOTKEY = b"\x02" * 3
POWER_HOTKEY = b"\x1d" * 3


@contextmanager
def _pty_pair():
    """Open a raw PTY: its slave as the reader and its master as an unbuffered writer."""
    master, slave = os.openpty()
    tty.setraw(slave)
    with os.fdopen(master, "r+b", buffering=0) as writer, os.fdopen(slave, "r") as reader:
        yield SimpleNamespace(reader=reader, writer=writer)


@contextmanager
def _running_console(console, keyboard, stdout=None):
    """Run Console.run() in a worker with borrowed streams.

    On the way out, a live worker gets Ctrl-B x3 if the keyboard writer is
    still open and is joined; teardown fails if it has not stopped.
    """
    run = SimpleNamespace(error=None)

    def worker():
        try:
            console.run()
        except Exception as error:  # noqa: BLE001 - expose the worker's failure to the test
            run.error = error

    with ExitStack() as stack:
        if stdout is None:
            stdout = stack.enter_context(open(os.devnull, "w"))
        stack.enter_context(patch("sys.stdin", keyboard.reader))
        stack.enter_context(patch("sys.stdout", stdout))
        # A PTY keyboard is raw already and tty.setraw() would flush its input; a pipe needs no setup.
        stack.enter_context(patch.object(console, "setraw", nullcontext))
        run.thread = threading.Thread(target=worker, daemon=True)
        run.thread.start()
        try:
            yield run
        finally:
            if run.thread.is_alive():
                if not keyboard.writer.closed:
                    keyboard.writer.write(EXIT_HOTKEY)
                run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console worker did not stop during teardown"


def _wait_registered(client, run, timeout=5):
    """Wait until the exporter has registered the console, which may still be waiting for its port."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert run.thread.is_alive(), f"console stopped before registering: {run.error!r}"
        if client.call("console_status")["write_token_held"]:
            return
        time.sleep(0.01)
    raise AssertionError("console did not register with the exporter")


def test_setraw_restores_the_terminal():
    master, slave = os.openpty()
    with os.fdopen(master, "r+b", buffering=0), os.fdopen(slave, "r") as reader, patch("sys.stdin", reader):
        cooked = termios.tcgetattr(reader)
        with Console(Mock()).setraw():
            assert termios.tcgetattr(reader) != cooked
        assert termios.tcgetattr(reader) == cooked


def test_ctrl_b_exits():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"a" + EXIT_HOTKEY)
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit after Ctrl-B x3"
        assert run.error is None


class _FakePowerControl(PowerClient):
    """Power client double that records the thread cycle() ran on."""

    def __init__(self, error=None):
        self.cycled = threading.Event()
        self.cycle_thread = None
        self.error = error

    def cycle(self, wait=2):
        self.cycle_thread = threading.current_thread()
        self.cycled.set()
        if self.error is not None:
            raise self.error


def test_ctrl_bracket_triggers_power_cycle():
    power = _FakePowerControl()

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        loop_thread = client.portal.call(threading.current_thread)
        with _running_console(Console(client, power_control=power), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(POWER_HOTKEY)
            assert power.cycled.wait(timeout=5), "power cycle was not triggered"
            assert run.thread.is_alive(), "console exited after power cycle"
        assert run.error is None

    # cycle() blocks, so it must not run on the thread of the event loop driving the console
    assert power.cycle_thread is not None
    assert power.cycle_thread is not loop_thread


def test_power_cycle_failure_does_not_kill_console(caplog):
    caplog.set_level(logging.WARNING, logger="jumpstarter_driver_pyserial.console")
    power = _FakePowerControl(error=RuntimeError("PDU unreachable"))

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client, power_control=power), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(POWER_HOTKEY)
            assert power.cycled.wait(timeout=5), "power cycle was not triggered"
            time.sleep(0.1)
            assert run.thread.is_alive(), "console exited after a failed power cycle"
        assert run.error is None

    assert any("power cycle failed" in r.message for r in caplog.records)


def test_ctrl_bracket_without_power_client():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(POWER_HOTKEY)
            time.sleep(0.1)
            assert run.thread.is_alive(), "console exited unexpectedly on Ctrl-] without power client"
        assert run.error is None


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

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        original_stream_async = client.stream_async

        @asynccontextmanager
        async def wrapped_stream_async(method="connect"):
            async with original_stream_async(method=method) as stream:
                yield _BrokenSendStream(stream)

        with patch.object(client, "stream_async", wrapped_stream_async):
            with _running_console(Console(client), keyboard) as run:
                _wait_registered(client, run)
                keyboard.writer.write(b"a")
                time.sleep(0.1)
                assert run.thread.is_alive(), "console crashed on send failure"
            assert run.error is None, f"console raised unexpected exception: {run.error!r}"

    # three failed sends (a, ^B, ^B) warn once
    warnings = [r for r in caplog.records if "send failed" in r.message]
    assert len(warnings) == 1, f"expected exactly one send-failure warning, got {len(warnings)}"


def test_receive_failure_raises_stream_drop():
    """receive() raising BrokenResourceError should trigger ConsoleStreamDrop."""
    broken_stream = AsyncMock()
    broken_stream.receive.side_effect = BrokenResourceError("simulated receive failure")

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        original_stream_async = client.stream_async

        @asynccontextmanager
        async def wrapped_stream_async(method="connect"):
            async with original_stream_async(method=method) as _stream:
                yield broken_stream

        with (
            patch.object(client, "stream_async", wrapped_stream_async),
            _running_console(Console(client), keyboard) as run,
        ):
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not end when its stream broke"

    assert isinstance(run.error, ConsoleStreamDrop), f"expected ConsoleStreamDrop, got {run.error!r}"


def test_hotkey_detector_three_presses():
    """Three consecutive presses of a registered key return its action."""
    exit_action = AsyncMock()
    detector = HotkeyDetector({b"\x02": exit_action, b"\x1d": AsyncMock()})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is exit_action


def test_hotkey_detector_unrelated_byte_resets():
    """An unrelated byte resets the counter."""
    exit_action = AsyncMock()
    detector = HotkeyDetector({b"\x02": exit_action})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"a") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is exit_action


def test_hotkey_detector_alternating_keys_resets():
    """Alternating between registered keys resets rather than accumulating."""
    exit_action = AsyncMock()
    power_action = AsyncMock()
    detector = HotkeyDetector({b"\x02": exit_action, b"\x1d": power_action})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x1d") is None
    assert detector.feed(b"\x1d") is None
    assert detector.feed(b"\x1d") is power_action


def test_hotkey_detector_byte_outside_actions():
    """A byte with no registered action returns None."""
    detector = HotkeyDetector({b"\x02": AsyncMock()})
    assert detector.feed(b"x") is None
    assert detector.feed(b"y") is None


def test_hotkey_detector_counter_resets_after_hit():
    """After a hit, the counter resets so a 4th press does not immediately re-fire."""
    exit_action = AsyncMock()
    detector = HotkeyDetector({b"\x02": exit_action})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is exit_action
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is exit_action
