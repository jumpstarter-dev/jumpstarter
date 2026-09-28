"""Exercise the Windows terminal adapter without requiring a live console handle."""

import asyncio
import io
import sys
import threading
from collections import deque
from contextlib import asynccontextmanager, contextmanager
from importlib import import_module
from types import SimpleNamespace

import anyio
import click
import pytest
from anyio.from_thread import start_blocking_portal
from anyio.lowlevel import checkpoint

from . import console as console_module

pytest.importorskip("prompt_toolkit")
windows_console = import_module("jumpstarter_driver_pyserial.windows_console")
KeyPress = import_module("prompt_toolkit.key_binding.key_processor").KeyPress
Keys = import_module("prompt_toolkit.keys").Keys
Vt100Parser = import_module("prompt_toolkit.input.vt100_parser").Vt100Parser


@pytest.fixture
def anyio_backend():
    # The production attachment uses prompt-toolkit's asyncio console wait.
    return "asyncio"


class Keyboard:
    def __init__(self):
        self.keys = []
        self.parser = Vt100Parser(self.keys.append)
        self.events = []
        self.batches = []
        self.callback = None
        self.closed = False
        self.eof = False
        self.attached = False
        self.flush_count = 0
        self.attach_thread = None
        self.attach_error = None
        self.read_error = None
        self.read_count = 0

    @contextmanager
    def raw_mode(self):
        self.events.append("raw")
        try:
            yield
        finally:
            self.events.append("restore")

    @contextmanager
    def attach(self, callback):
        if self.attach_error:
            raise self.attach_error
        self.attach_thread = threading.get_ident()
        self.events.append("attach")
        self.attached = True
        self.callback = callback
        loop = asyncio.get_running_loop()
        handles = [loop.call_later(0.01 * (i + 1), self.feed, batch) for i, batch in enumerate(self.batches)]
        if self.eof:
            handles.append(loop.call_later(0.01 * (len(self.batches) + 1), self.end))
        try:
            yield
        finally:
            self.attached = False
            self.callback = None
            for handle in handles:
                handle.cancel()
            self.events.append("detach")

    def feed(self, text):
        self.parser.feed(text)
        if self.callback:
            self.callback()

    def end(self):
        self.closed = True
        if self.callback:
            self.callback()

    def read_keys(self):
        self.read_count += 1
        if self.read_error:
            raise self.read_error
        keys, self.keys = self.keys, []
        # Keep the parser's callback bound to the current queue.
        self.parser.feed_key_callback = self.keys.append
        return keys

    def flush_keys(self):
        self.flush_count += 1
        self.parser.flush()
        return self.read_keys()

    def close(self):
        self.closed = True
        self.events.append("close")


class Stdout:
    def __init__(self, tty=True):
        self.tty = tty
        self.buffer = io.BytesIO()

    def isatty(self):
        return self.tty

    def flush(self):
        self.buffer.flush()


class OutputMode:
    def __init__(self):
        self.events = []

    def __enter__(self):
        self.events.append("enable")
        return self

    def __exit__(self, *exc):
        self.events.append("restore")


@pytest.fixture
def terminal_environment(monkeypatch):
    keyboard = Keyboard()
    stdout = Stdout()
    output_mode = OutputMode()
    stdin = SimpleNamespace(isatty=lambda: True)
    monkeypatch.setattr(windows_console, "sys", SimpleNamespace(stdin=stdin, stdout=stdout))
    monkeypatch.setattr(console_module, "sys", SimpleNamespace(platform="win32", stdin=stdin, stdout=stdout))
    monkeypatch.setattr("prompt_toolkit.input.create_input", lambda **kwargs: keyboard)
    core_console = SimpleNamespace(OutputMode=lambda: output_mode)
    monkeypatch.setitem(sys.modules, "jumpstarter_core.console", core_console)
    return SimpleNamespace(keyboard=keyboard, stdout=stdout, output_mode=output_mode, core_console=core_console)


class RemoteStream:
    def __init__(self, keyboard, chunks=(), failure=None):
        self.keyboard = keyboard
        self.chunks = deque(chunks)
        self.sent = bytearray()
        self.failure = failure

    async def receive(self):
        while not self.keyboard.attached:
            await checkpoint()
        if self.chunks:
            return self.chunks.popleft()
        if self.failure:
            raise self.failure
        await anyio.sleep_forever()

    async def send(self, data):
        self.sent.extend(data)


def run_console(environment, *, observe=False, chunks=(), failure=None, cancel=False):
    """Call the public synchronous entry point across a real AnyIO portal."""
    stream = RemoteStream(environment.keyboard, chunks, failure)
    methods = []
    lifecycle = []

    @asynccontextmanager
    async def stream_async(*, method):
        methods.append(method)
        with anyio.fail_after(2), anyio.CancelScope() as scope:
            timer = asyncio.get_running_loop().call_later(0.04, scope.cancel) if cancel else None
            try:
                yield stream
            finally:
                if timer is not None:
                    timer.cancel()
                lifecycle.append("closed")

    environment.stream = stream
    environment.methods = methods
    environment.lifecycle = lifecycle
    with start_blocking_portal() as portal:
        client = SimpleNamespace(portal=portal, stream_async=stream_async)
        console_module.Console(client, observe=observe).run()
    return stream


@pytest.mark.parametrize(
    ("batches", "expected"),
    [
        (["abc\x02\x02\x02ignored"], b"abc\x02\x02"),
        (["\x02", "\x02", "\x02"], b"\x02\x02"),
        (["\x02\x02x\x02", "\x02\x02"], b"\x02\x02x\x02\x02"),
    ],
)
def test_console_exit_sequence_spans_batches_and_resets(terminal_environment, batches, expected):
    env = terminal_environment
    env.keyboard.batches = batches
    stream = run_console(env)
    assert stream.sent == expected
    assert env.methods == ["connect"]
    assert env.lifecycle == ["closed"]
    assert env.keyboard.events == ["raw", "attach", "detach", "restore", "close"]
    assert env.keyboard.attach_thread != threading.get_ident()


def test_observer_reads_remote_output_but_never_sends_keys(terminal_environment):
    env = terminal_environment
    env.keyboard.batches = ["text\x03\r\x1b[A", "\x02\x02", "\x02"]
    stream = run_console(env, observe=True, chunks=[b"remote observation"])
    assert stream.sent == b""
    assert env.stdout.buffer.getvalue() == b"remote observation"
    assert env.output_mode.events == ["enable", "restore"]
    assert env.methods == ["observe"]
    assert env.lifecycle == ["closed"]


def test_control_keys_ansi_paste_and_split_surrogates_reach_remote_stream(terminal_environment):
    env = terminal_environment
    env.keyboard.batches = [
        "\x03\r\x1b[A\x1bOP",
        "\x1b[200~paste ü\r",
        "\n\x1b[201~",
        "\ud83d",
        "\ude80",
        "\x02\x02\x02",
    ]
    stream = run_console(env)
    assert stream.sent == "\x03\r\x1b[A\x1bOP\x1b[200~paste ü\r\n\x1b[201~🚀\x02\x02".encode()


def test_cursor_position_and_vt_mouse_reports_reach_remote_terminal(terminal_environment):
    env = terminal_environment
    reports = "\x1b[12;34R\x1b[<0;8;9M\x1b[<0;8;9m"
    env.keyboard.batches = [reports, "\x02\x02\x02"]
    stream = run_console(env)
    assert stream.sent == reports.encode() + b"\x02\x02"


def test_legacy_paste_heuristic_is_disabled_without_wrapping_ordinary_input(terminal_environment):
    env = terminal_environment
    reader = SimpleNamespace(recognize_paste=True)
    env.keyboard.console_input_reader = reader
    env.keyboard.batches = ["ordinary typed line\r", "\x02\x02\x02"]
    stream = run_console(env)
    assert reader.recognize_paste is False
    assert stream.sent == b"ordinary typed line\r\x02\x02"


@pytest.mark.parametrize(
    "ending", ["remote-eof", "input-eof", "cancel", "read-error", "attach-error", "key-read-error"]
)
def test_console_restores_terminal_and_closes_stream_on_every_end(terminal_environment, ending):
    env = terminal_environment
    env.keyboard.eof = ending == "input-eof"
    failure = anyio.EndOfStream() if ending == "remote-eof" else None
    if ending == "read-error":
        failure = RuntimeError("remote read failed")
    if ending == "attach-error":
        env.keyboard.attach_error = RuntimeError("keyboard attach failed")
    if ending == "key-read-error":
        env.keyboard.batches = ["trigger input callback"]
        env.keyboard.read_error = RuntimeError("keyboard read failed")
    if ending.endswith("error"):
        with pytest.raises(ExceptionGroup) as caught:
            run_console(env, failure=failure)
        assert ending.split("-")[0] in str(caught.value.exceptions[0])
    else:
        run_console(env, failure=failure, cancel=ending == "cancel")
    assert env.lifecycle == ["closed"]
    assert env.keyboard.events[-2:] == ["restore", "close"]
    assert env.output_mode.events == ["enable", "restore"]
    assert not env.keyboard.attached
    if ending != "attach-error":
        assert "detach" in env.keyboard.events


@pytest.mark.anyio
async def test_incomplete_escape_flushes_and_synthetic_terminal_events_are_not_forwarded(terminal_environment):
    env = terminal_environment
    with windows_console.WindowsTerminal() as terminal, terminal.attach(), anyio.fail_after(1):
        env.keyboard.keys.extend(KeyPress(key, "discard") for key in (Keys.WindowsMouseEvent, Keys.Ignore))
        env.keyboard.feed("\x1b")
        assert await terminal.receive() == b"\x1b"
        assert env.keyboard.flush_count >= 1
        env.keyboard.feed("\x1b[A")
        assert await terminal.receive() == b"\x1b[A"


@pytest.mark.anyio
async def test_queued_callback_after_detach_does_not_consume_next_shell_input(terminal_environment):
    env = terminal_environment
    with windows_console.WindowsTerminal() as terminal, terminal.attach(), anyio.fail_after(1):
        stale_callback = env.keyboard.callback
        env.keyboard.feed("serial input")
        assert await terminal.receive() == b"serial input"
    reads_before = env.keyboard.read_count
    env.keyboard.feed("next shell command")
    stale_callback()
    assert env.keyboard.read_count == reads_before
    assert "".join(key.data for key in env.keyboard.keys) == "next shell command"
    assert env.keyboard.events == ["raw", "attach", "detach", "restore", "close"]


@pytest.mark.anyio
async def test_split_utf8_output_is_decoded_once_and_incomplete_tail_is_flushed(terminal_environment):
    env = terminal_environment
    with windows_console.WindowsTerminal() as terminal:
        for chunk in (b"caf\xc3", b"\xa9 \xf0\x9f", b"\x9a\x80\x1b[31m", b"\xe2"):
            await terminal.send(chunk)
        assert env.stdout.buffer.getvalue() == "café 🚀\x1b[31m".encode()
    assert env.stdout.buffer.getvalue() == "café 🚀\x1b[31m�".encode()
    assert env.keyboard.events == ["raw", "restore", "close"]


@pytest.mark.anyio
async def test_redirected_output_preserves_arbitrary_bytes_without_closing_stdout(terminal_environment):
    env = terminal_environment
    env.stdout.tty = False
    payload = bytes(range(256)) + b"\r\n\x00\xff"
    with windows_console.WindowsTerminal() as terminal:
        await terminal.send(payload[:97])
        await terminal.send(payload[97:])
    assert env.stdout.buffer.getvalue() == payload
    assert not env.stdout.buffer.closed
    assert env.output_mode.events == []


@pytest.mark.parametrize("stdin", [None, SimpleNamespace(isatty=lambda: False)])
def test_redirected_input_is_rejected_before_opening_console(terminal_environment, monkeypatch, stdin):
    env = terminal_environment
    monkeypatch.setattr(windows_console, "sys", SimpleNamespace(stdin=stdin, stdout=env.stdout))
    with (
        pytest.raises(click.ClickException, match="Use 'pipe' for redirected input"),
        windows_console.WindowsTerminal(),
    ):
        pytest.fail("redirected input was accepted")
    assert env.keyboard.events == []


def test_output_setup_failure_restores_raw_mode_and_closes_input(terminal_environment, monkeypatch):
    env = terminal_environment

    def fail_output(**kwargs):
        raise OSError("output unavailable")

    monkeypatch.setattr(env.core_console, "OutputMode", fail_output)
    with pytest.raises(OSError, match="output unavailable"), windows_console.WindowsTerminal():
        pytest.fail("output setup failure was ignored")
    assert env.keyboard.events == ["raw", "restore", "close"]
