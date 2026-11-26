import fcntl
import logging
import os
import select
import signal
import struct
import termios
import threading
import time
import tty
from contextlib import ExitStack, asynccontextmanager, contextmanager, nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from anyio import BrokenResourceError, EndOfStream, Event, sleep_forever
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_power.client import PowerClient

from .console import Console, ConsoleStreamDrop, HotkeyDetector
from .driver import PySerial
from jumpstarter.client.core import DriverError
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


class _SerialDevice:
    """The device end of a PTY used as a serial port, read continuously in the background."""

    def __init__(self, master: int, path: str):
        self.path = path
        self._master = master
        self._data = bytearray()
        self._arrived = threading.Condition()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._drain, daemon=True)

    def _drain(self):
        while not self._stop.is_set():
            if not select.select([self._master], [], [], 0.05)[0]:
                continue
            try:
                chunk = os.read(self._master, 1024)
            except OSError:  # macOS reports EIO while no one has the port open
                time.sleep(0.01)
                continue
            with self._arrived:
                self._data += chunk
                self._arrived.notify_all()

    def read_until(self, marker: bytes, timeout: float = 5) -> bytes:
        """Wait until marker arrives or timeout passes; return all that arrived since the last call."""
        with self._arrived:
            self._arrived.wait_for(lambda: marker in self._data, timeout)
            data = bytes(self._data)
            self._data.clear()
        return data


@contextmanager
def _serial_device():
    """Open a raw PTY to stand in for a serial device at `path`.

    Its output is read until the end, not only while a test is looking: macOS
    blocks tcdrain() on a PTY until everything written to it has been read, and
    pyserial calls tcdrain() when the exporter closes the port. Bytes nobody
    reads, such as the first two of the Ctrl-B x3 that ends a console, would
    otherwise hang the exporter's shutdown.
    """
    master, slave = os.openpty()
    tty.setraw(slave)
    device = _SerialDevice(master, os.ttyname(slave))
    device._thread.start()
    try:
        yield device
    finally:
        device._stop.set()
        device._thread.join()
        os.close(slave)
        os.close(master)


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


def _wait_registered(client, run, observe=False, timeout=5):
    """Wait until the exporter has registered the console, which may still be waiting for its port."""
    field = "observer_count" if observe else "write_token_held"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert run.thread.is_alive(), f"console stopped before registering: {run.error!r}"
        if client.call("console_status")[field]:
            return
        time.sleep(0.01)
    raise AssertionError("console did not register with the exporter")


def _wait_detached(client, timeout=5):
    """Wait until no console is attached to the exporter; return its console status."""
    deadline = time.monotonic() + timeout
    while (status := client.call("console_status"))["total_clients"]:
        if time.monotonic() > deadline:
            raise AssertionError(f"console did not detach from the exporter: {status}")
        time.sleep(0.01)
    return status


@contextmanager
def _pipe_pair():
    """Open a pipe: an unbuffered reader and writer."""
    r, w = os.pipe()
    with os.fdopen(r, "rb", buffering=0) as reader, os.fdopen(w, "wb", buffering=0) as writer:
        yield SimpleNamespace(reader=reader, writer=writer)


def _read_until(stream, marker, timeout=5):
    """Read stream until marker arrives or timeout passes; return all that was read."""
    data = b""
    deadline = time.monotonic() + timeout
    while marker not in data and time.monotonic() < deadline:
        if select.select([stream], [], [], 0.05)[0]:
            data += os.read(stream.fileno(), 1024)
    return data


def _wait_read(stream, timeout=5):
    """Wait until a pipe's reader has consumed everything written to it; a PTY reports 0 too early."""
    deadline = time.monotonic() + timeout
    while struct.unpack("i", fcntl.ioctl(stream, termios.FIONREAD, b"\0" * 4))[0]:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.005)
    return True


def _terminal_attrs(fd):
    """tcgetattr() without PENDIN, which macOS sets by itself when a terminal returns to canonical mode."""
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~termios.PENDIN
    return attrs


def test_setraw_restores_the_terminal():
    master, slave = os.openpty()
    with os.fdopen(master, "r+b", buffering=0), os.fdopen(slave, "r") as reader, patch("sys.stdin", reader):
        cooked = _terminal_attrs(reader)
        with Console(Mock()).setraw():
            assert _terminal_attrs(reader) != cooked
        assert _terminal_attrs(reader) == cooked


def test_ctrl_b_exits():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"a" + EXIT_HOTKEY)
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit after Ctrl-B x3"
        assert run.error is None


def _interrupt_run(console, keyboard, prepare, stdout=None):
    """Run console.run() on this, the main, thread and send it SIGINT once prepare() returns in another.

    SIGINT raises KeyboardInterrupt even if the process started with it ignored.
    If prepare() raises, the console gets Ctrl-B x3 instead and the error is re-raised.
    """
    main = threading.get_ident()
    errors = []

    def interrupter():
        try:
            prepare()
        except BaseException as error:  # noqa: BLE001 - re-raised on the main thread
            errors.append(error)
            keyboard.writer.write(EXIT_HOTKEY)
            return
        signal.pthread_kill(main, signal.SIGINT)

    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with ExitStack() as stack:
            if stdout is None:
                stdout = stack.enter_context(open(os.devnull, "w"))
            stack.enter_context(patch("sys.stdin", keyboard.reader))
            stack.enter_context(patch("sys.stdout", stdout))
            stack.enter_context(patch.object(console, "setraw", nullcontext))
            thread = threading.Thread(target=interrupter, daemon=True)
            thread.start()
            try:
                console.run()
            except KeyboardInterrupt:
                interrupted = True
            else:
                interrupted = False
            finally:
                thread.join()
    finally:
        signal.signal(signal.SIGINT, previous)
    if errors:
        raise errors[0]
    assert interrupted, "console.run() returned before it was interrupted"


def _wait_write_token_held(client, timeout=5):
    deadline = time.monotonic() + timeout
    while not client.call("console_status")["write_token_held"]:
        if time.monotonic() > deadline:
            raise AssertionError("console did not register with the exporter")
        time.sleep(0.01)


def test_interrupted_run_stops_console_task():
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("needs to interrupt the main thread")  # ty: ignore[too-many-positional-arguments, invalid-argument-type]

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        _interrupt_run(Console(client), keyboard, lambda: _wait_write_token_held(client))
        try:
            status = _wait_detached(client)
        finally:
            keyboard.writer.write(EXIT_HOTKEY)  # ends a console task that outlived run()
        assert not status["write_token_held"], status


def test_run_interrupted_while_starting_its_task_stops_it():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        console = Console(client)
        spawn = client.portal._spawn_task_from_thread

        def spawn_then_interrupt(func, *args):
            spawn(func, *args)
            if getattr(func, "__self__", None) is console:
                raise KeyboardInterrupt  # as SIGINT does while the caller waits for the task to be queued

        with (
            patch.object(client.portal, "_spawn_task_from_thread", spawn_then_interrupt),
            patch("sys.stdin", keyboard.reader),
            patch("sys.stdout", Mock()),
            patch.object(console, "setraw", nullcontext),
        ):
            with pytest.raises(KeyboardInterrupt):
                console.run()
            time.sleep(0.2)  # time for a console task that outlived run() to attach
            try:
                status = _wait_detached(client)
            finally:
                keyboard.writer.write(EXIT_HOTKEY)  # ends a console task that outlived run()
                time.sleep(0.2)  # while it still reads the patched stdin
        assert not status["write_token_held"], status


class _StalledStdout:
    """A stdout whose flush() blocks until unstall is set, as one nobody reads does."""

    def __init__(self):
        self.flushing = threading.Event()
        self.unstall = threading.Event()
        self.flushed = threading.Event()
        self.buffer = Mock()

    def flush(self):
        self.flushing.set()
        self.unstall.wait(timeout=5)
        self.flushed.set()


def test_interrupted_run_returns_while_stdout_stalls():
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("needs to interrupt the main thread")  # ty: ignore[too-many-positional-arguments, invalid-argument-type]

    stdout = _StalledStdout()

    def stall_stdout():
        _wait_write_token_held(client)
        keyboard.writer.write(b"a")  # echoed by loop://
        assert stdout.flushing.wait(timeout=5), "console did not flush its output"

    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        try:
            _interrupt_run(Console(client), keyboard, stall_stdout, stdout=stdout)
            assert not stdout.flushed.is_set(), "console.run() waited for the stalled stdout"
            status = _wait_detached(client)
            assert not stdout.flushed.is_set(), "console stayed attached until stdout drained"
        finally:
            stdout.unstall.set()
        assert not status["write_token_held"], status


def test_ctrl_b_exits_while_stdout_stalls():
    stdout = _StalledStdout()
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        try:
            with _running_console(Console(client), keyboard, stdout=stdout) as run:
                _wait_registered(client, run)
                keyboard.writer.write(b"a")  # echoed by loop://
                assert stdout.flushing.wait(timeout=5), "console did not flush its output"
                keyboard.writer.write(EXIT_HOTKEY)
                run.thread.join(timeout=2)
                assert not run.thread.is_alive(), "console waited for the stalled stdout to exit"
            status = _wait_detached(client)
            assert not stdout.flushed.is_set(), "console stayed attached until stdout drained"
        finally:
            stdout.unstall.set()
        assert run.error is None
        assert not status["write_token_held"], status


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


class _BlockingPowerControl(PowerClient):
    """Power client double whose cycle() blocks until the test releases it."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cycles = 0
        self.timed_out = False

    def cycle(self, wait=2):
        self.cycles += 1
        self.started.set()
        if not self.release.wait(timeout=10):
            self.timed_out = True


class _CountingPowerControl(PowerClient):
    """Power client double that reports each cycle and optionally fails the first."""

    def __init__(self, fail_first=False):
        self.cycled = threading.Semaphore(0)
        self.fail_first = fail_first

    def cycle(self, wait=2):
        self.cycled.release()
        if self.fail_first:
            self.fail_first = False
            raise RuntimeError("PDU unreachable")


class _FakeSerial:
    """Serial client double that connects and completes sends when the test says so."""

    def __init__(self, portal, hold_sends=False):
        self.portal = portal
        self.sends = []
        self.send_started = threading.Event()
        self._opened = threading.Event()
        self._connected = portal.call(Event)
        self._sends_released = portal.call(Event)
        if not hold_sends:
            self.release_sends()

    @property
    def sent(self):
        return b"".join(self.sends)

    def connect(self):
        """Release the open gate and wait until stream_async() is ready to yield."""
        self.portal.call(self._connected.set)
        assert self._opened.wait(timeout=5), "console did not open its stream"

    def release_sends(self):
        self.portal.call(self._sends_released.set)

    def wait_sent(self, data, timeout=5):
        """Wait until data has been sent or timeout passes; return all that was sent."""
        deadline = time.monotonic() + timeout
        while data not in self.sent and time.monotonic() < deadline:
            time.sleep(0.005)
        return bytes(self.sent)

    @asynccontextmanager
    async def stream_async(self, method):
        await self._connected.wait()
        self._opened.set()
        yield self

    async def send(self, data):
        self.send_started.set()
        await self._sends_released.wait()
        self.sends.append(data)

    async def receive(self):
        await sleep_forever()


@contextmanager
def _fake_serial(**kwargs):
    with start_blocking_portal() as portal:
        yield _FakeSerial(portal, **kwargs)


def test_keys_typed_while_the_held_keys_are_sent_follow_them():
    with _pipe_pair() as keyboard, _fake_serial(hold_sends=True) as serial:
        with _running_console(Console(serial), keyboard) as run:
            keyboard.writer.write(b"ab")
            assert _wait_read(keyboard.reader), "console did not read input while connecting"
            serial.connect()
            assert serial.send_started.wait(timeout=5), "console did not send the keys it held"
            keyboard.writer.write(b"cd")
            assert _wait_read(keyboard.reader), "console did not read input while sending held keys"
            serial.release_sends()
            sent = serial.wait_sent(b"abcd")
        assert run.error is None
    assert sent == b"abcd", f"held keys were lost or reordered: {sent!r}"


def test_held_keys_are_sent_in_bounded_messages():
    with (
        patch("jumpstarter_driver_pyserial.console.SEND_CHUNK", 4),
        _pipe_pair() as keyboard,
        _fake_serial() as serial,
    ):
        with _running_console(Console(serial), keyboard) as run:
            keyboard.writer.write(b"0123456789")
            assert _wait_read(keyboard.reader), "console did not read input while connecting"
            serial.connect()
            keyboard.writer.write(b"after")
            serial.wait_sent(b"after")
            sends = list(serial.sends)
        assert run.error is None
    assert sends[0] == b"0123", sends
    assert all(len(data) <= 4 for data in sends), sends
    assert b"".join(sends) == b"0123456789after", sends


def test_keys_typed_during_a_power_cycle_are_not_replayed_after_it():
    power = _BlockingPowerControl()
    cycle_ended = threading.Event()
    with _pipe_pair() as keyboard, _fake_serial() as serial:
        console = Console(serial, power_control=power)
        power_cycle = console._power_cycle

        async def observed_power_cycle():
            await power_cycle()
            cycle_ended.set()

        with patch.object(console, "_power_cycle", observed_power_cycle), _running_console(console, keyboard) as run:
            try:
                keyboard.writer.write(b"before" + POWER_HOTKEY)
                assert power.started.wait(timeout=5), "power cycle was not triggered"
                keyboard.writer.write(b"early" + POWER_HOTKEY)
                assert _wait_read(keyboard.reader), "console did not read input during the cycle"
                serial.connect()
                keyboard.writer.write(b"late")
                assert serial.wait_sent(b"late") == b"late", "connected console did not send keys during the cycle"
            finally:
                power.release.set()
            assert cycle_ended.wait(timeout=5), "power cycle did not end"
            keyboard.writer.write(b"done")
            sent = serial.wait_sent(b"done")
        assert run.error is None
    assert not power.timed_out, "the power cycle ended without being released"
    assert power.cycles == 1, f"Ctrl-] x3 during the cycle started another one: {power.cycles} cycles"
    assert sent == b"latedone", f"expected only keys typed once connected: {sent!r}"


def test_ctrl_b_during_a_power_cycle_exits_once_the_cycle_is_done():
    power = _BlockingPowerControl()
    with _pipe_pair() as keyboard, _fake_serial() as serial:
        with _running_console(Console(serial, power_control=power), keyboard) as run:
            try:
                keyboard.writer.write(POWER_HOTKEY)
                assert power.started.wait(timeout=5), "power cycle was not triggered"
                keyboard.writer.write(EXIT_HOTKEY)
                assert _wait_read(keyboard.reader), "console did not read Ctrl-B x3 during the cycle"
                run.thread.join(timeout=0.2)
                assert run.thread.is_alive(), "console exited while the power cycle was running"
            finally:
                power.release.set()
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit once the cycle was done"
        assert run.error is None
    assert not power.timed_out, "the power cycle ended without being released"


def test_only_the_byte_completing_a_hotkey_is_kept_from_the_device():
    power = _FakePowerControl()
    with _pipe_pair() as keyboard, _fake_serial() as serial:
        with _running_console(Console(serial, power_control=power), keyboard) as run:
            serial.connect()
            keyboard.writer.write(b"a" + POWER_HOTKEY)
            assert power.cycled.wait(timeout=5), "power cycle was not triggered"
            keyboard.writer.write(b"b" + EXIT_HOTKEY)
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit after Ctrl-B x3"
        assert run.error is None
    assert bytes(serial.sent) == b"a\x1d\x1db\x02\x02", serial.sent


@pytest.mark.parametrize("fail_first", [False, True], ids=["success", "failure"])
def test_ctrl_bracket_power_cycles_again_once_the_cycle_is_done(fail_first):
    power = _CountingPowerControl(fail_first)
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client, power_control=power), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(POWER_HOTKEY)
            assert power.cycled.acquire(timeout=5), "power cycle was not triggered"
            # cycle() may have returned before its event-loop task finishes.
            again = False
            deadline = time.monotonic() + 5
            while not again and time.monotonic() < deadline:
                keyboard.writer.write(POWER_HOTKEY)
                again = power.cycled.acquire(timeout=0.5)
            assert again, "Ctrl-] x3 did nothing after the first cycle"
        assert run.error is None


def test_an_observer_drops_its_input_and_stays_up():
    shown_r, shown_w = os.pipe()
    with (
        _pipe_pair() as keyboard,
        os.fdopen(shown_r, "rb", buffering=0) as output,
        os.fdopen(shown_w, "w") as shown,
        serve(PySerial(url="loop://")) as client,
        client.stream("connect") as writer,
    ):
        with _running_console(Console(client, observe=True), keyboard, shown) as run:
            _wait_registered(client, run, observe=True)
            seen = b""
            deadline = time.monotonic() + 5
            while b"ping" not in seen and time.monotonic() < deadline:
                writer.send(b"ping")
                seen += _read_until(output, b"ping", timeout=0.2)
            assert b"ping" in seen, "observer did not show the device's output"
            keyboard.writer.write(b"typed")
            assert _wait_read(keyboard.reader), "observer did not read its input"
            writer.send(b"pong")
            seen = _read_until(output, b"pong")
        assert run.error is None
    assert b"pong" in seen, "observer stopped showing output after its user typed"
    assert b"typed" not in seen, "observer sent its input to the device"


def test_stdin_eof_ends_the_console_and_frees_the_write_token():
    with _pipe_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.close()
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console kept running after stdin ended"
        status = _wait_detached(client)
        assert not status["write_token_held"], status
    assert isinstance(run.error, ExceptionGroup), run.error
    matched, rest = run.error.split(EndOfStream)
    assert matched is not None and rest is None, run.error


def test_stream_closed_by_exporter_ends_console_without_keypress():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            time.sleep(0.1)
            client.call("close")
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console kept running after stream closure"
        assert isinstance(run.error, ConsoleStreamDrop), run.error


def test_keys_typed_after_ctrl_b_stay_in_the_terminal():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client:
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(EXIT_HOTKEY + b"next")
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit after Ctrl-B x3"
        left = _read_until(keyboard.reader, b"next", timeout=1)
        assert left == b"next", f"console read past Ctrl-B x3, leaving {left!r}"
        assert run.error is None


class _ImpatientPySerial(PySerial):
    """PySerial whose exporter gives up waiting for the port after half a second."""

    _fanout_source_ready_timeout = 0.5


def test_a_console_whose_port_never_appears_ends_with_a_stream_drop(tmp_path):
    with (
        _pty_pair() as keyboard,
        serve(_ImpatientPySerial(url=str(tmp_path / "ttyMISSING"), check_present=False)) as client,
    ):
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"ahead")
            run.thread.join(timeout=10)
            assert not run.thread.is_alive(), "console kept running after the exporter gave up"
        status = _wait_detached(client)
        assert not status["write_token_held"], status
        assert isinstance(run.error, ConsoleStreamDrop), run.error


def test_a_refused_connect_raises_its_driver_error():
    with _pty_pair() as keyboard, serve(PySerial(url="loop://")) as client, client.stream("connect"):
        with _running_console(Console(client), keyboard) as run:
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not end when its connect was refused"
        assert isinstance(run.error, DriverError), run.error
        assert "Console in use" in str(run.error)


def test_leaving_a_console_that_is_still_connecting_frees_the_write_token(tmp_path):
    port = tmp_path / "ttyLATE"
    with (
        _serial_device() as device,
        _pty_pair() as keyboard,
        serve(PySerial(url=str(port), check_present=False)) as client,
    ):
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(EXIT_HOTKEY)
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console ignored Ctrl-B x3 while waiting for the port"
        assert run.error is None
        status = _wait_detached(client)
        assert not status["write_token_held"], status

        port.symlink_to(device.path)
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"next")
            received = device.read_until(b"next", timeout=10)
        assert run.error is None
    assert received == b"next", f"the next console did not reach the device: {received!r}"


def test_keys_typed_while_the_console_connects_reach_the_device(tmp_path):
    port = tmp_path / "ttyLATE"
    with (
        _serial_device() as device,
        _pty_pair() as keyboard,
        serve(PySerial(url=str(port), check_present=False)) as client,
    ):
        with _running_console(Console(client), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"ahead")
            port.symlink_to(device.path)
            received = device.read_until(b"ahead", timeout=10)
            keyboard.writer.write(b"after")
            received += device.read_until(b"after")
        assert run.error is None
    assert received == b"aheadafter", f"keys typed while connecting were lost or reordered: {received!r}"


def test_a_power_cycle_while_connecting_drops_the_keys_typed_before_it(tmp_path):
    port = tmp_path / "ttyLATE"
    power = _FakePowerControl()
    with (
        _serial_device() as device,
        _pty_pair() as keyboard,
        serve(PySerial(url=str(port), check_present=False)) as client,
    ):
        with _running_console(Console(client, power_control=power), keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"before" + POWER_HOTKEY)
            assert power.cycled.wait(timeout=5), "power cycle was not triggered"
            port.symlink_to(device.path)
            received = b""
            deadline = time.monotonic() + 10
            while not received and time.monotonic() < deadline:
                keyboard.writer.write(b"!")
                received += device.read_until(b"!", timeout=0.2)
        assert run.error is None
    assert received, "keys typed after the power cycle did not reach the device"
    assert received == b"!" * len(received), f"keys typed before the power cycle reached the device: {received!r}"


def test_a_console_run_again_sends_what_is_typed_to_its_new_stream(tmp_path):
    port = tmp_path / "ttyLATE"
    with (
        _serial_device() as device,
        _pipe_pair() as keyboard,
        serve(PySerial(url=str(port), check_present=False)) as client,
    ):
        port.symlink_to(device.path)
        console = Console(client)
        with _running_console(console, keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"first")
            received = device.read_until(b"first")
        assert run.error is None
        assert received == b"first", f"the first run did not reach the device: {received!r}"
        _wait_detached(client)

        port.unlink()
        with _running_console(console, keyboard) as run:
            _wait_registered(client, run)
            keyboard.writer.write(b"second")
            assert _wait_read(keyboard.reader), "console did not read input while connecting"
            port.symlink_to(device.path)
            received = device.read_until(b"second", timeout=10)
        assert run.error is None
    assert b"second" in received, f"the second run lost what was typed while connecting: {received!r}"


def test_a_console_run_again_does_not_send_what_the_previous_run_held():
    with _pipe_pair() as keyboard, _fake_serial() as serial:
        console = Console(serial)
        with _running_console(console, keyboard) as run:
            keyboard.writer.write(b"stale" + EXIT_HOTKEY)
            run.thread.join(timeout=5)
            assert not run.thread.is_alive(), "console did not exit while connecting"
        assert run.error is None
    with _pipe_pair() as keyboard, _fake_serial() as serial:
        console.serial_client = serial
        with _running_console(console, keyboard) as run:
            keyboard.writer.write(b"fresh")
            assert _wait_read(keyboard.reader), "console did not read input while connecting"
            serial.connect()
            sent = serial.wait_sent(b"fresh")
        assert run.error is None
    assert sent == b"fresh", f"the second run sent what the first one held: {sent!r}"


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
    assert detector.feed(b"a") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"


def test_hotkey_detector_alternating_keys_resets():
    """Alternating between registered keys resets rather than accumulating."""
    detector = HotkeyDetector({b"\x02", b"\x1d"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x1d") is None
    assert detector.feed(b"\x1d") is None
    assert detector.feed(b"\x1d") == b"\x1d"


def test_hotkey_detector_byte_outside_keys():
    """A byte that is not a registered key returns None."""
    detector = HotkeyDetector({b"\x02"})
    assert detector.feed(b"x") is None
    assert detector.feed(b"y") is None


def test_hotkey_detector_counter_resets_after_hit():
    """After a hit, the counter resets so a 4th press does not immediately re-fire."""
    detector = HotkeyDetector({b"\x02"})
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") is None
    assert detector.feed(b"\x02") == b"\x02"
