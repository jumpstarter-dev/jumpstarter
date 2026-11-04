"""Tests for the console keywords, against drivers served in this process.

``FakeShell`` is a login shell on a byte stream: it greets with ``login:``,
asks for a password, then echoes each command and answers it followed by a
``$ `` prompt. ``EchoNetwork`` echoes bytes and a ``loop://`` ``PySerial``
port loops writes back, with the serial driver's exclusive write token.
``Sender`` sends fixed chunks and then stays connected, silent.
"""

from __future__ import annotations

import re
import textwrap
import threading
import time
import tracemalloc
from collections.abc import Callable, Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import pytest
import robot
from anyio import ClosedResourceError, create_memory_object_stream
from anyio.abc import ObjectStream
from jumpstarter_driver_composite.driver import Composite
from jumpstarter_driver_network.driver import EchoNetwork
from jumpstarter_driver_pyserial.driver import PySerial
from robot.api import ExecutionResult, Failure, ResultVisitor

from .. import sessions
from ..extensions import extensions_for
from ..testing import use_client
from . import console as console_module
from .console import ConsoleKeywords, control_character
from jumpstarter.common.utils import serve
from jumpstarter.driver import Driver, exportstream

PROMPT = "$ "


class ShellStream(ObjectStream[bytes]):
    """The driver end of one `FakeShell` connection.

    Input is handled as it is sent, so the stream needs no background task:
    a task group around the driver's ``yield`` would turn the exporter's
    teardown errors into logged exception groups.
    """

    def __init__(self, password: str):
        self.password = password
        self.step = "user"
        self.pending = b""
        self._tx, self._rx = create_memory_object_stream[bytes](1000)
        self._tx.send_nowait(b"U-Boot 2024.01 (rev 42)\r\nWelcome\r\nlogin: ")

    async def receive(self) -> bytes:
        return await self._rx.receive()

    async def send(self, item: bytes) -> None:
        if self._tx._closed:
            raise ClosedResourceError
        self.pending += item
        if b"\x03" in self.pending:
            self.pending = self.pending.replace(b"\x03", b"")
            self._out(b"^C\r\n" + PROMPT.encode())
        while b"\n" in self.pending and not self._tx._closed:
            line, self.pending = self.pending.split(b"\n", 1)
            await self._line(line.rstrip(b"\r").decode())

    async def send_eof(self) -> None:
        await self._tx.aclose()

    async def aclose(self) -> None:
        await self._tx.aclose()
        await self._rx.aclose()

    def _out(self, data: bytes) -> None:
        self._tx.send_nowait(data)

    async def _line(self, line: str) -> None:
        if self.step == "user":
            self.step = "password"
            self._out(f"{line}\r\nPassword: ".encode())
        elif self.step == "password":
            ok = line == self.password
            self.step = "shell" if ok else "user"
            self._out(b"\r\nLast login: today\r\n$ " if ok else b"\r\nLogin incorrect\r\nlogin: ")
        elif line == "exit":
            self._out(b"exit\r\nlogout\r\n")
            await self._tx.aclose()
        else:
            self._out(f"{line}\r\n".encode())
            output = await self._command(line)
            self._out(f"{output}\r\n{PROMPT}".encode() if output else PROMPT.encode())

    @staticmethod
    async def _command(line: str) -> str:
        if line.startswith("echo "):
            return line[5:]
        if line == "uname":
            return "Linux"
        if line == "lines":
            return "one\r\ntwo\r\nthree"
        if line == "slow":
            await anyio.sleep(0.3)
            return "done"
        return f"{line}: command not found" if line else ""


@dataclass(kw_only=True)
class FakeShell(Driver):
    """A login shell on a byte stream."""

    password: str = "secret"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        async with ShellStream(self.password) as stream:
            yield stream


class SenderStream(ObjectStream[bytes]):
    """Sends ``chunks`` one per receive, then nothing more until closed."""

    def __init__(self, chunks: Iterator[bytes]):
        self.chunks = chunks

    async def receive(self) -> bytes:
        for chunk in self.chunks:
            return chunk
        await anyio.sleep_forever()
        raise AssertionError("unreachable")

    async def send(self, item: bytes) -> None:
        pass

    async def send_eof(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


@dataclass(kw_only=True)
class Sender(Driver):
    """A byte stream that sends what ``chunks()`` gives and stays open."""

    chunks: Callable[[], Iterable[bytes]] = field(default=lambda: ())

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        yield SenderStream(iter(self.chunks()))


FLOOD_MB = 16
LINE = b"[    1.000000] usb 1-1: new high-speed USB device number 2 using xhci_hcd\r\n"


def flood() -> Iterator[bytes]:
    """``FLOOD_MB`` megabytes of kernel-log lines in uneven chunks, then ``MARKER``."""
    block = LINE * 64
    sizes = (4096, 1, 700, 37, 2048)
    sent = index = 0
    while sent < FLOOD_MB * 1024 * 1024:
        size = sizes[index % len(sizes)]
        yield block[:size]
        sent, index = sent + size, index + 1
    yield b"login prompt MARKER\r\n"


@pytest.fixture(scope="module")
def client():
    root = Composite(
        children={
            "shell": FakeShell(),
            "echo": EchoNetwork(),
            "serial": PySerial(url="loop://"),
            "board": Composite(children={"console": FakeShell(password="board")}),
            "flood": Sender(chunks=flood),
            # with encoding_errors=strict: an invalid byte after an incomplete sequence
            "garbled": Sender(chunks=lambda: [b"boot ok\r\n\xe2", b"\xff more"]),
        }
    )
    with serve(root) as served:
        yield served


@pytest.fixture
def kw(client, tmp_path, monkeypatch):
    """The console keywords with ``client`` as the default session, writing artifacts under ``tmp_path``."""
    monkeypatch.chdir(tmp_path)
    keywords = ConsoleKeywords()
    with use_client(client):
        try:
            yield keywords
        finally:
            keywords.close_all_consoles()


def logged_in(kw: ConsoleKeywords, **options) -> int:
    index = kw.open_console("shell", prompt=PROMPT, timeout="3s", **options)
    kw.console_login("root", "secret")
    return index


# ---- unit helpers ----


@pytest.mark.parametrize(
    "value,expected",
    [
        ("C", "\x03"),
        ("c", "\x03"),
        ("^C", "\x03"),
        ("CTRL+D", "\x04"),
        ("ctrl-z", "\x1a"),
        ("[", "\x1b"),
        ("ESC", "\x1b"),
        ("del", "\x7f"),
        ("0x03", "\x03"),
        ("\x03", "\x03"),
        ("@", "\x00"),
    ],
)
def test_control_character(value, expected):
    assert control_character(value) == expected


@pytest.mark.parametrize("value", ["CC", "0x20", "0x41", "é", ""])
def test_control_character_rejects(value):
    with pytest.raises(ValueError, match="not a control character"):
        control_character(value)


def test_newline_conversion():
    assert console_module._newline("LF") == "\n"
    assert console_module._newline("crlf") == "\r\n"
    assert console_module._newline("CR") == "\r"
    assert console_module._newline("\r\n") == "\r\n"


def test_strip_echo():
    assert console_module._strip_echo("ls\r\nfile\r\n", "ls") == "file\r\n"
    assert console_module._strip_echo("file\r\n", "ls") == "file\r\n"


def test_pyserial_extension_is_registered():
    from jumpstarter_driver_pyserial.client import PySerialClient

    from jumpstarter.client import DriverClient

    assert "Open Console" in [extension.method_words for extension in extensions_for(PySerialClient)]
    assert "Open Console" not in [extension.method_words for extension in extensions_for(DriverClient)]


# ---- reading and writing ----


def test_read_until_and_login(kw):
    assert kw.open_console("shell", prompt=PROMPT) == 1
    banner = kw.console_read_until("login:")
    assert banner.startswith("U-Boot 2024.01")
    assert banner.endswith("login:")
    kw.console_write_line("root")
    assert kw.console_read_until("Password:").strip() == "root\r\nPassword:"
    kw.console_write_line("secret")
    assert kw.console_read_until_prompt(strip_prompt=True).strip() == "Last login: today"


def test_console_login_returns_output_and_hides_password(kw, monkeypatch):
    written = []
    monkeypatch.setattr(console_module.robot_logger, "write", lambda text, *args, **kwargs: written.append(text))
    kw.open_console("shell", prompt=PROMPT)
    output = kw.console_login("root", "secret")
    assert "Welcome" in output and "Last login: today" in output and output.endswith(PROMPT)
    assert not any("secret" in text for text in written)
    assert any("Wrote the password" in text for text in written)


def test_console_login_incorrect(kw):
    kw.open_console("shell", prompt=PROMPT, timeout="3s")
    with pytest.raises(Failure, match="Login on console '1' failed: 'Login incorrect'"):
        kw.console_login("root", "wrong")


def test_login_without_prompt_reads_what_follows(kw):
    kw.open_console("shell")
    assert "Last login: today" in kw.console_login("root", "secret")


def test_execute_command(kw):
    logged_in(kw)
    assert kw.console_execute_command("uname") == "Linux"
    assert kw.console_execute_command("lines") == "one\ntwo\nthree"
    assert kw.console_execute_command("slow") == "done"
    assert kw.console_execute_command("") == ""
    with_prompt = kw.console_execute_command("echo hi", strip_prompt=False)
    assert with_prompt == f"hi\n{PROMPT}"


def test_execute_command_needs_prompt(kw):
    kw.open_console("shell")
    with pytest.raises(Failure, match="has no prompt"):
        kw.console_execute_command("uname")


def test_read_until_regexp_and_expect(kw):
    kw.open_console("shell")
    output = kw.console_read_until_regexp(r"rev \d+", "nothing-like-this")
    assert output.endswith("rev 42")
    kw.console_write_line("root")
    kw.console_write_line("secret")
    match = kw.console_expect(r"Last (\w+): (\w+)")
    assert match == ["Last login: today", "login", "today"]
    kw.console_write_line("echo version=1.2.3")
    assert kw.console_expect(r"version=(\S+)\r\n\$ ") == ["version=1.2.3\r\n$ ", "1.2.3"]


def test_read_until_regexp_earliest_match_wins(kw):
    kw.open_console("shell")
    assert kw.console_read_until_regexp("login:", "Welcome").endswith("Welcome")


def test_read_until_regexp_needs_a_pattern(kw):
    kw.open_console("shell")
    with pytest.raises(Failure, match="at least one pattern"):
        kw.console_read_until_regexp()


def test_read_returns_everything_pending(kw):
    kw.open_console("shell")
    output = kw.console_read(delay="0.3s")
    assert output.endswith("login: ")
    assert kw.console_read(delay="0") == ""


def test_clear_console_buffer_keeps_transcript(kw):
    kw.open_console("shell")
    kw.console_read_until("Welcome")
    kw.clear_console_buffer()
    kw.console_write_line("root")
    assert kw.console_read_until("Password:").strip().startswith("root")
    assert "login: " in kw.get_console_transcript()


def test_write_without_newline_and_control_character(kw):
    logged_in(kw)
    kw.console_write("ec")
    kw.console_write("ho partial")
    kw.console_write_line()
    assert kw.console_read_until_prompt(strip_prompt=True).strip().endswith("partial")
    kw.console_write_control_character("C")
    assert kw.console_read_until_prompt().strip() == "^C\r\n$"


def test_newline_setting(kw):
    logged_in(kw)
    assert kw.set_console_newline("CRLF") == "\n"
    assert kw.console_execute_command("uname") == "Linux"
    assert kw.set_console_newline("LF") == "\r\n"


def test_prompt_and_timeout_settings(kw):
    kw.open_console("shell", prompt=PROMPT)
    assert kw.set_console_prompt(r"\$ $", prompt_is_regexp=True) == (PROMPT, False)
    assert kw.set_console_prompt("") == (r"\$ $", True)
    assert kw.set_console_timeout("2 s") == "10 seconds"
    assert kw.set_console_timeout("10s") == "2 seconds"
    with pytest.raises(Failure, match="has no prompt"):
        kw.console_read_until_prompt()


def test_should_contain(kw):
    kw.open_console("shell")
    kw.console_should_contain("Welcome")
    with pytest.raises(Failure, match=r"No 'Kernel panic' on console '1' in 200 milliseconds"):
        kw.console_should_contain("Kernel panic", timeout="0.2s")


def test_should_not_contain(kw):
    kw.open_console("shell")
    kw.console_should_not_contain("Kernel panic", duration="0.3s")
    kw.console_write_line("root")
    with pytest.raises(Failure, match="contained 'Password:'"):
        kw.console_should_not_contain("Password:", duration="3s")


def test_timeout_failure_shows_the_tail(kw):
    kw.open_console("shell", timeout="0.2s")
    with pytest.raises(Failure) as error:
        kw.console_read_until("never")
    message = str(error.value)
    assert message.startswith("No 'never' on console '1' in 200 milliseconds.")
    assert "Output received:\nU-Boot 2024.01" in message


def test_timeout_tail_is_limited(kw):
    kw.open_console("echo", timeout="0.3s")
    kw.console_write_line("x" * 3000)
    kw.console_read_until("x" * 3000)
    with pytest.raises(Failure) as error:
        kw.console_read_until("never")
    assert "Last 1000 characters received:\n" in str(error.value)
    assert str(error.value).endswith("x" * 999 + "\n")


def test_driver_closing_the_stream(kw):
    logged_in(kw)
    kw.console_write_line("exit")
    with pytest.raises(Failure, match="was closed by the driver before 'never' appeared"):
        kw.console_read_until("never")
    assert "logout" in kw.get_console_transcript()
    with pytest.raises(Failure, match="was closed by the driver"):
        kw.console_write_line("uname")


# ---- several consoles ----


def test_several_consoles_and_switch(kw):
    first = kw.open_console("shell", alias="host", prompt=PROMPT, timeout="3s")
    second = kw.open_console("board.console", alias="board", prompt=PROMPT, timeout="3s")
    assert (first, second) == (1, 2)
    kw.console_login("root", "board")
    assert kw.switch_console("host") == 2
    kw.console_login("root", "secret")
    assert kw.console_execute_command("echo host") == "host"
    assert kw.switch_console("2") == 1
    assert kw.console_execute_command("echo board") == "board"
    kw.close_console("host")
    assert kw.console_execute_command("uname") == "Linux"
    kw.switch_console("host")
    with pytest.raises(Failure, match="Console 'host' is closed"):
        kw.console_read()
    kw.switch_console("board")
    kw.close_console()
    with pytest.raises(RuntimeError, match="No open console"):
        kw.console_read()
    assert "Welcome" in kw.get_console_transcript("host")


def test_indices_restart_once_every_console_is_closed(kw):
    assert kw.open_console("echo") == 1
    assert kw.open_console("echo") == 2
    kw.close_all_consoles()
    assert kw.open_console("echo") == 1
    kw.close_console()
    assert kw.open_console("echo") == 1


def test_echo_network(kw):
    kw.open_console("echo", newline="CRLF")
    kw.console_write_line("ping")
    assert kw.console_read_until("ping\r\n") == "ping\r\n"


def test_serial_write_token_and_observe(kw):
    kw.open_console("serial", alias="writer")
    with pytest.raises(Failure, match="Open Console"):
        kw.open_console("serial", alias="second")
    kw.open_console("serial", alias="watcher", method="observe")
    kw.switch_console("writer")
    kw.console_write_line("hello serial")
    assert kw.console_read_until("hello serial") == "hello serial"
    kw.switch_console("watcher")
    assert kw.console_read_until("hello serial") == "hello serial"


def test_transcript_file(kw, tmp_path):
    kw.open_console("shell", alias="boot")
    kw.console_read_until("login:")
    file = Path(kw.get_console_transcript(as_file=True))
    assert file.is_absolute() and file.parent == (tmp_path / "jumpstarter").resolve()
    assert re.fullmatch(r"console-boot-\d+\.log", file.name)
    text = file.read_bytes().decode()
    assert text.startswith("U-Boot 2024.01 (rev 42)\r\nWelcome\r\nlogin: ")
    assert kw.get_console_transcript() == text


def test_open_console_errors(kw):
    with pytest.raises(Failure, match="driver 'nope'"):
        kw.open_console("nope")
    with pytest.raises(Failure, match="driver 'board.missing'"):
        kw.open_console("board.missing")
    with pytest.raises(LookupError):
        kw.open_console("shell", encoding="no-such-codec")
    with pytest.raises(ValueError, match="loglevel"):
        kw.open_console("shell", loglevel="LOUD")
    with pytest.raises(ValueError, match="scope"):
        kw.open_console("echo", scope="forever")
    with pytest.raises(re.error):
        kw.open_console("echo", prompt="(", prompt_is_regexp=True)
    assert len(console_module._consoles) == 0
    with pytest.raises(RuntimeError, match="No open console"):
        kw.console_read()


def test_open_console_bad_method(kw):
    # the exporter refuses an unknown stream only once it is open, so the first read reports it
    kw.open_console("shell", method="no_such_stream", timeout="3s")
    with pytest.raises(Failure, match=r"Console '1' was closed by the driver \(\w+.*\) before 'login:' appeared"):
        kw.console_read_until("login:")


def test_session_close_closes_consoles(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kw = ConsoleKeywords()
    with use_client(client):
        kw.open_console("shell", alias="s")
        console = console_module._get("s")
    assert console.closed
    with pytest.raises(RuntimeError, match="No open console"):
        kw.console_read()
    kw.close_all_consoles()


def test_decoding_errors_are_replaced(kw):
    kw.open_console("echo", encoding="ascii")
    kw.console_write_line("plain")
    kw.console_read_until("plain")
    console = console_module._get()
    console.feed(b"\xff\n")
    assert kw.console_read_until("\n") == "\n" or "�" in kw.get_console_transcript()
    assert "�" in kw.get_console_transcript()


def test_strict_decoding_error_is_reported(kw):
    kw.open_console("garbled", encoding_errors="strict", timeout="5s")
    started = time.monotonic()
    reported = r"closed by the driver \(UnicodeDecodeError: .*invalid continuation byte\) before 'never'"
    with pytest.raises(Failure, match=reported):
        kw.console_read_until("never")
    assert time.monotonic() - started < 2  # reported at once, not after the timeout
    assert "boot ok" in kw.get_console_transcript()


def test_flood_is_searched_incrementally_in_bounded_memory(kw, monkeypatch):
    monkeypatch.setattr(console_module, "BUFFER_CHARS", 1024 * 1024, raising=False)
    tracemalloc.start()
    try:
        started = time.monotonic()
        kw.open_console("flood", loglevel="NONE", timeout="60s")
        kw.console_should_contain("login prompt")
        assert kw.console_read_until_regexp(r"MARK(ER)\r\n") == " MARKER\r\n"
        elapsed = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert elapsed < 10, f"{FLOOD_MB} MB took {elapsed:.1f}s"  # about 1s; the whole buffer per chunk took 10s
    # the buffer keeps 1 MB of it, the transcript only its tail; everything was kept before
    assert peak < FLOOD_MB * 1024 * 1024 / 2, f"peak {peak / 1e6:.1f} MB for {FLOOD_MB} MB of output"
    console = console_module._get()
    assert len(console.tail()) < 2 * console_module.TAIL_CHARS
    transcript = kw.get_console_transcript()
    assert len(transcript) > FLOOD_MB * 1024 * 1024 and transcript.endswith("login prompt MARKER\r\n")


def test_search_runs_without_holding_the_condition(kw):
    kw.open_console("echo")
    console = console_module._get()
    acquired: list[bool] = []

    def find(text: str, start: int) -> None:
        def other() -> None:  # what the receiving side does on the event loop thread
            ok = console._cond.acquire(timeout=1)
            acquired.append(ok)
            if ok:
                console._cond.release()

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()

    with pytest.raises(Failure, match="No search"):
        console.wait_for(console_module.Search(find, 0), 0, "search")
    assert acquired == [True]


def test_literal_search_resumes_across_chunks(kw):
    kw.open_console("echo", timeout="0.1s")
    console = console_module._get()
    console._append("abc lo")
    with pytest.raises(Failure):
        kw.console_read_until("login:")
    console._append("gin: rest")
    assert kw.console_read_until("login:") == "abc login:"
    console._append("mul")
    with pytest.raises(Failure):
        kw.console_read_until_regexp(r"^ restmulti\s+line$")
    console._append("ti\n line")
    assert kw.console_read_until_regexp(r"(?m)restmulti\s+line$") == " restmulti\n line"


def test_unread_output_is_bounded(kw, monkeypatch):
    monkeypatch.setattr(console_module, "BUFFER_CHARS", 100, raising=False)
    kw.open_console("echo")
    console = console_module._get()
    for _ in range(10):
        console._append("x" * 30)
    console._append("end")
    output = kw.console_read_until("end")
    assert output == "x" * 97 + "end"
    assert kw.get_console_transcript() == "x" * 300 + "end"


def test_keyword_names_in_jumpstarter_library():
    from ..library import Jumpstarter

    names = Jumpstarter().get_keyword_names()
    for name in ("open_console", "console_read_until", "console_execute_command", "get_console_transcript"):
        assert name in names


# ---- Robot Framework runs ----

SUITE = """
*** Settings ***
Library    OperatingSystem
Library    jumpstarter_robot.Jumpstarter
Library    jumpstarter_robot.Driver    serial    client=jumpstarter_driver_pyserial.client.PySerialClient
...        prefix=Serial    AS    serial

*** Test Cases ***
Login And Run Commands
    ${index}=    Open Console    shell    alias=host    prompt=$${SPACE}    timeout=3s    scope=test
    Should Be Equal As Integers    ${index}    1
    ${banner}=    Console Read Until Regexp    rev (\\\\d+)
    Should End With    ${banner}    rev 42
    Console Login    root    secret
    ${out}=    Console Execute Command    echo hello robot
    Should Be Equal    ${out}    hello robot

Expect Groups
    Open Console    shell    prompt=$${SPACE}    scope=test
    Console Login    root    secret
    Console Write Line    uname
    ${match}    ${word}=    Console Expect    (Lin\\\\w+)
    Should Be Equal    ${word}    Linux
    Console Read Until Prompt
    Console Write Control Character    C
    ${out}=    Console Read Until Prompt    strip_prompt=True
    Should Contain    ${out}    ^C

Console Closed At End Of Test Scope
    Run Keyword And Expect Error    No open console.    Console Read

Timeout Shows The Tail
    Open Console    shell    timeout=0.2s    scope=test
    Run Keyword And Expect Error    No 'never' on console '1' in 200 milliseconds.*Welcome*
    ...    Console Read Until    never
    Console Should Contain    login:
    Console Should Not Contain    Kernel panic    duration=0.2s

Serial Extension Keyword
    ${index}=    Serial Open Console    alias=ser    scope=test
    Console Write Line    hello serial
    ${out}=    Console Read Until    hello serial
    Should Be Equal    ${out}    hello serial
    ${file}=    Get Console Transcript    as_file=True
    File Should Exist    ${file}
    ${text}=    Get File    ${file}
    Should Contain    ${text}    hello serial

Suite Scope Survives Tests
    Open Console    shell    alias=kept
    Console Should Contain    Welcome

Suite Scope Survives Tests Check
    ${previous}=    Switch Console    kept
    Console Write Line    root
    Console Should Contain    Password:
"""


def _run(tmp_path: Path, text: str) -> dict[str, tuple[str, str]]:
    suite = tmp_path / "console.robot"
    suite.write_text(textwrap.dedent(text))
    output = tmp_path / "output.xml"
    robot.run(str(suite), outputdir=str(tmp_path), output=str(output), report="NONE", log="NONE", console="none")
    result = ExecutionResult(str(output))
    found: dict[str, tuple[str, str]] = {}

    class Visitor(ResultVisitor):
        def visit_test(self, test):
            found[test.name] = (test.status, test.message)

    result.visit(Visitor())
    return found


def test_robot_suite(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with use_client(client):
        results = _run(tmp_path, SUITE)
    failed = {name: message for name, (status, message) in results.items() if status != "PASS"}
    assert len(results) == 7
    assert failed == {}
    transcripts = sorted(path.name for path in (tmp_path / "jumpstarter").glob("console-*.log"))
    assert any(name.startswith("console-host-") for name in transcripts)
    assert any(name.startswith("console-ser-") for name in transcripts)
    # the suite-scoped console was closed when the suite ended
    assert all(console.closed for console in console_module._consoles)
    opened = sessions.opened()
    assert opened is None or opened.client is client
