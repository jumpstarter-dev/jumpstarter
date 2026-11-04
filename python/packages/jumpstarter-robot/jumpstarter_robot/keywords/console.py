"""Console keywords: read and write a driver's byte stream like a terminal.

Any driver that exports a byte stream can be a console: a serial port
(``connect``, or read-only ``observe``), a network connection, the QEMU
console. The keywords follow the Telnet and SSHLibrary conventions: several
consoles can be open at once, `Open Console` makes the new one current, and
`Switch Console` changes it by index or alias.

How a console is connected: the driver stream is opened through the
session's portal, so a refused connection (another client holds a serial
port's write token, the method does not exist) fails `Open Console` itself.
A task on the portal copies everything received into the console's buffer
and its transcript file, so nothing is lost while a test does something else.
The keywords search that buffer on Robot Framework's own thread, and a read
consumes the output up to the end of its match, like ``pexpect``. This needs
neither ``pexpect`` nor a local TCP port forward.

Memory stays bounded on chatty consoles: a search resumes where the last one
stopped instead of rescanning everything unread, the buffer keeps at most
``BUFFER_CHARS`` unread characters (dropping the oldest, with a warning), and
only the transcript's last ``TAIL_CHARS`` stay in memory; the file has it all.
"""

from __future__ import annotations

import codecs
import contextlib
import logging
import re
import threading
import time
from collections import deque
from collections.abc import Callable, Generator
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from anyio import BrokenResourceError, ClosedResourceError, EndOfStream, create_task_group
from robot.api import Failure
from robot.api import logger as robot_logger
from robot.api.deco import keyword
from robot.utils import ConnectionCache, secs_to_timestr, timestr_to_secs

from .. import context, sessions
from ..errors import mapped_errors
from ..extensions import register_node_extension
from jumpstarter.client import DriverClient

logger = logging.getLogger(__name__)

TAGS = ("jumpstarter", "console")
TAIL_CHARS = 1000
BUFFER_CHARS = 4 * 1024 * 1024  # unread characters kept; older unread output is dropped
REGEXP_WINDOW = 64 * 1024  # how far back a regular expression search resumes
LOOKBEHIND_CHARS = 256  # context before a resumed search, for lookbehinds, \b and ^
LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "WARN", "NONE")

# Named control characters accepted by `Console Write Control Character`,
# besides ``C``, ``^C`` and ``CTRL+C`` style names and literal characters.
CONTROL_NAMES = {
    "NUL": "\x00",
    "ETX": "\x03",
    "EOT": "\x04",
    "BEL": "\x07",
    "BS": "\x08",
    "TAB": "\t",
    "LF": "\n",
    "CR": "\r",
    "ESC": "\x1b",
    "DEL": "\x7f",
}

Found = tuple[int, int, "re.Match[str] | None"]  # where a match starts and ends, and the match of a regexp


@dataclass(frozen=True)
class Search:
    """What a read waits for.

    ``find(text, start)`` returns the ``(start, end, match)`` of the first
    match at or after ``start`` in ``text``, or None. A match that is not
    there yet can only start within ``overlap`` characters of the end of the
    text searched, so the next search resumes there.
    """

    find: Callable[[str, int], Found | None]
    overlap: int


def _secs(value: Any) -> float:
    secs = timestr_to_secs(value)
    if secs < 0:
        raise ValueError(f"timeout cannot be negative: {value}")
    return secs


def _newline(value: str) -> str:
    """``LF``, ``CRLF``, ``CR`` or a literal string, as the Telnet library takes them."""
    newline = str(value).upper().replace("LF", "\n").replace("CR", "\r")
    if not newline:
        raise ValueError("newline cannot be empty")
    return newline


def control_character(value: str) -> str:
    """The character named by ``C``, ``^C``, ``CTRL+C``, ``ESC``, ``0x03`` or a literal control character."""
    text = str(value)
    if len(text) == 1 and (ord(text) < 0x20 or ord(text) == 0x7F):
        return text
    name = text.strip().upper()
    if name in CONTROL_NAMES:
        return CONTROL_NAMES[name]
    for marker in ("CTRL+", "CTRL-", "^"):
        if name.startswith(marker) and len(name) == len(marker) + 1:
            name = name[len(marker) :]
            break
    if len(name) == 1 and 0x40 <= ord(name) <= 0x5F:  # @, A-Z, [, \, ], ^, _
        return chr(ord(name) & 0x1F)
    with contextlib.suppress(ValueError):
        code = int(name, 0)
        if 0 <= code < 0x20 or code == 0x7F:
            return chr(code)
    raise ValueError(f"'{value}' is not a control character; use e.g. C, ^C, CTRL+C, ESC or 0x03")


def _compile(pattern: str | re.Pattern[str], regexp: bool = True) -> re.Pattern[str]:
    if isinstance(pattern, re.Pattern):
        return pattern
    return re.compile(str(pattern) if regexp else re.escape(str(pattern)))


def _literal(text: str) -> Search:
    def find(buffer: str, start: int):
        index = buffer.find(text, start)
        return None if index < 0 else (index, index + len(text), None)

    return Search(find, max(len(text) - 1, 0))


def _patterns(patterns: list[re.Pattern[str]]) -> Search:
    """The earliest match of any pattern; the first pattern wins a tie."""

    def find(buffer: str, start: int):
        found = None
        for pattern in patterns:
            match = pattern.search(buffer, start)
            if match and (found is None or match.start() < found.start()):
                found = match
        return None if found is None else (found.start(), found.end(), found)

    return Search(find, REGEXP_WINDOW)


def _strip_echo(output: str, command: str) -> str:
    """Remove the echoed command line from the start of ``output``."""
    if command and output.startswith(command):
        output = output[len(command) :]
        for ending in ("\r\r\n", "\r\n", "\n", "\r"):
            if output.startswith(ending):
                return output[len(ending) :]
    return output


class _Unread:
    """The output received and not yet read, as chunks joined only when read."""

    def __init__(self) -> None:
        self._chunks: deque[str] = deque()
        self._length = 0

    def __len__(self) -> int:
        return self._length

    def append(self, text: str) -> None:
        if self._chunks and len(self._chunks[-1]) < 4096:
            self._chunks[-1] += text  # keeps the chunk count down when the stream trickles
        else:
            self._chunks.append(text)
        self._length += len(text)

    def text_from(self, start: int) -> str:
        """The unread output from index ``start`` on; costs what it returns, not the whole buffer."""
        parts: list[str] = []
        needed = self._length - start
        for chunk in reversed(self._chunks):
            if needed <= 0:
                break
            parts.append(chunk if len(chunk) <= needed else chunk[len(chunk) - needed :])
            needed -= len(chunk)
        return "".join(reversed(parts))

    def take(self, end: int) -> str:
        """Remove and return the first ``end`` characters."""
        parts: list[str] = []
        while end > 0 and self._chunks:
            chunk = self._chunks.popleft()
            if len(chunk) > end:
                chunk, rest = chunk[:end], chunk[end:]
                self._chunks.appendleft(rest)
            parts.append(chunk)
            end -= len(chunk)
            self._length -= len(chunk)
        return "".join(parts)


class Console:
    """One open console: the driver stream, its buffer and its transcript.

    Offsets into the output count characters since the console opened, so a
    search can tell what it has seen however much was read or dropped since.
    """

    def __init__(
        self,
        name: str,
        session: sessions.Session,
        node: DriverClient,
        *,
        method: str,
        timeout: float,
        newline: str,
        encoding: str,
        encoding_errors: str,
        loglevel: str,
        transcript: Path,
    ):
        self.name = name
        self.session = session
        self.node = node
        self.method = method
        self.timeout = timeout
        self.newline = newline
        self.prompt: tuple[str, bool] | None = None
        self.encoding = encoding
        self.loglevel = loglevel
        self.transcript_path = transcript
        self._decoder = codecs.getincrementaldecoder(encoding)(encoding_errors)
        self._cond = threading.Condition()
        self._unread = _Unread()
        self._base = 0  # offset of the first unread character
        self._received = 0  # characters received
        self._dropped = 0  # unread characters dropped since the last read said so
        self._tail = ""  # the transcript's last TAIL_CHARS
        self._file = transcript.open("wb")
        self._file_bytes = 0  # transcript bytes written and flushed
        self._eof = False
        self._error: BaseException | None = None
        self._closed = False
        self._stack = ExitStack()
        self._stream: Any = None
        self.closer: Callable[[], None] = self.close

    # ---- the receiving side, called on the portal's event loop ----

    def feed(self, data: bytes) -> None:
        text = self._decoder.decode(data)
        if text:
            self._append(text)

    def finish(self, error: BaseException | None = None) -> None:
        """Mark the end of the stream; readers then fail with the first ``error``."""
        try:
            text = self._decoder.decode(b"", final=True)
            if text:
                self._append(text)
        except Exception as e:  # noqa: BLE001 - e.g. a strict decoder still holding an incomplete sequence
            self._decoder.reset()
            error = error or e
        finally:
            with self._cond:
                self._eof = True
                self._error = error
                self._cond.notify_all()

    def _append(self, text: str) -> None:
        """Add received ``text``; costs its own length, so the event loop never waits on a large buffer."""
        with self._cond:
            self._unread.append(text)
            self._received += len(text)
            excess = len(self._unread) - BUFFER_CHARS
            if excess > 0:
                if not self._dropped:
                    logger.warning("console %s: dropping unread output beyond %d characters", self.name, BUFFER_CHARS)
                self._unread.take(excess)
                self._base += excess
                self._dropped += excess
            self._tail = text[-TAIL_CHARS:] if len(text) >= TAIL_CHARS else (self._tail + text)[-TAIL_CHARS:]
            if not self._file.closed:
                data = text.encode("utf-8", "replace")
                self._file.write(data)
                self._file.flush()
                self._file_bytes += len(data)
            self._cond.notify_all()

    # ---- lifecycle ----

    def open(self) -> None:
        try:
            # cancels the attach if an RF timeout interrupts it, so the portal
            # is not left holding a half-open stream
            attach = _attach(self.node, self.method, self)
            self._stream = context.enter_cancellable(self.node.portal, attach, self._stack)
        except BaseException:
            self._stack.close()
            self._file.close()
            raise

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._stack.close()
        except Exception:
            logger.warning("error while closing console %s", self.name, exc_info=True)
        finally:
            with self._cond:
                self._file.close()

    def check(self) -> None:
        if self._closed:
            raise Failure(f"Console '{self.name}' is closed.")
        self.session.check_alive()

    # ---- writing ----

    def write(self, text: str, *, log: bool = True) -> None:
        if self._eof:
            raise Failure(f"Console '{self.name}' was closed by the driver{self._reason()}.")
        data = str(text).encode(self.encoding)
        try:
            self.node.portal.call(self._stream.send, data)
        except (BrokenResourceError, ClosedResourceError, EndOfStream) as e:
            raise Failure(f"Console '{self.name}' is no longer connected: {e or type(e).__name__}") from e
        if log:
            self.log(f"Wrote {text!r} to console '{self.name}'.")

    # ---- reading ----

    def wait_for(self, search: Search, timeout: float | None, what: str) -> tuple[str, re.Match[str] | None]:
        """Wait for ``search`` to match the buffer; consume and return the output up to the match's end."""
        timeout = self.timeout if timeout is None else timeout
        found = self._wait(search, time.monotonic() + timeout)
        if found is not None:
            end, match = found
            return self._consume(end), match
        if self._eof:
            raise Failure(
                f"Console '{self.name}' was closed by the driver{self._reason()} before {what} appeared.{self.tail()}"
            )
        raise Failure(f"No {what} on console '{self.name}' in {secs_to_timestr(timeout)}.{self.tail()}")

    def watch(self, search: Search, duration: float) -> bool:
        """Whether ``search`` matches within ``duration``; consumes everything received meanwhile."""
        found = self._wait(search, time.monotonic() + duration)
        self.take()
        return found is not None

    def _wait(self, search: Search, deadline: float) -> tuple[int, re.Match[str] | None] | None:
        """Search the output as it arrives until a match, the end of the stream or ``deadline``.

        Returns the offset of the match's end and the match. Each search
        covers only what arrived since the last one plus ``search.overlap``,
        and runs without the condition, which the event loop needs to deliver
        more output.
        """
        start = searched = -1
        while True:
            with self._cond:
                while self._received == searched and not self._eof:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    self._cond.wait(remaining)
                start = max(start, self._base)
                origin = max(start - LOOKBEHIND_CHARS, self._base)
                text = self._unread.text_from(origin - self._base)
                searched, eof = self._received, self._eof
            found = search.find(text, start - origin)
            if found is not None:
                _, end, match = found
                return origin + end, match
            if eof or deadline <= time.monotonic():
                return None
            start = searched - search.overlap

    def wait_quiet(self, duration: float) -> None:
        """Let output arrive for ``duration`` seconds, or until the driver closes the stream."""
        deadline = time.monotonic() + duration
        with self._cond:
            while not self._eof and (remaining := deadline - time.monotonic()) > 0:
                self._cond.wait(remaining)

    def take(self) -> str:
        """Consume everything received so far."""
        with self._cond:
            end = self._received
        return self._consume(end)

    def _consume(self, end: int) -> str:
        """Consume and log the unread output up to offset ``end``."""
        with self._cond:
            output = self._unread.take(max(end - self._base, 0))
            self._base += len(output)
            dropped, self._dropped = self._dropped, 0
        if dropped:
            robot_logger.warn(
                f"Console '{self.name}' dropped {dropped} characters of unread output, keeping the last "
                f"{BUFFER_CHARS}; the transcript {self.transcript_path} has them."
            )
        if output:
            self.log(output)
        return output

    # ---- reporting ----

    def log(self, text: str) -> None:
        if self.loglevel != "NONE":
            # _check_options limited it to LOG_LEVELS, and NONE is handled here.
            robot_logger.write(text, cast("robot_logger.LogLevel", self.loglevel))

    def transcript(self) -> str:
        """Everything received, read back from the transcript file."""
        with self._cond:
            size = self._file_bytes
        with self.transcript_path.open("rb") as file:
            return file.read(size).decode("utf-8", "replace")

    def tail(self) -> str:
        with self._cond:
            shown, received = self._tail, self._received
        if not received:
            return " Nothing was received."
        what = f"Last {len(shown)} characters" if len(shown) < received else "Output"
        return f"\n{what} received:\n{shown}"

    def _reason(self) -> str:
        error = self._error
        if error is None:
            return ""
        return f" ({type(error).__name__}: {error})" if str(error) else f" ({type(error).__name__})"


@asynccontextmanager
async def _attach(node: DriverClient, method: str, console: Console):
    """Open the driver stream and copy what it sends into ``console`` until closed."""
    async with node.stream_async(method) as stream, create_task_group() as tg:
        tg.start_soon(_pump, stream, console)
        try:
            yield stream
        finally:
            tg.cancel_scope.cancel()


async def _pump(stream: Any, console: Console) -> None:
    error: BaseException | None = None
    try:
        while True:
            console.feed(await stream.receive())
    except EndOfStream:
        pass
    except Exception as e:  # noqa: BLE001 - reported by the next read instead
        error = e
    finally:
        console.finish(error)


_consoles: ConnectionCache[Console] = ConnectionCache("No open console.")


def _get(alias: str | int | None = None) -> Console:
    return _consoles.get_connection(alias)


def _forget(console: Console) -> None:
    """Drop ``console`` as the current one once it is closed."""
    if _consoles.current is console:
        _consoles.current_index = None


def _check_options(encoding: str, encoding_errors: str, loglevel: str, scope: str) -> str:
    """Reject bad options before anything is opened; returns the log level."""
    codecs.lookup(encoding)
    codecs.lookup_error(encoding_errors)
    if str(scope).lower() not in context.SCOPES:
        raise ValueError(f"scope must be one of {', '.join(context.SCOPES)}, not '{scope}'")
    level = str(loglevel).upper()
    if level not in LOG_LEVELS:
        raise ValueError(f"loglevel must be one of {', '.join(LOG_LEVELS)}, not '{loglevel}'")
    return level


def _resolve(session: sessions.Session, path: str) -> DriverClient:
    try:
        return session.resolve(path)
    except KeyError as e:
        raise Failure(f"Open Console: {e.args[0]}") from None


def open_console(
    path: str,
    alias: str | None = None,
    method: str = "connect",
    timeout: str = "10s",
    newline: str = "LF",
    prompt: str | None = None,
    prompt_is_regexp: bool = False,
    encoding: str = "UTF-8",
    encoding_errors: str = "replace",
    loglevel: str = "INFO",
    scope: str = "suite",
    session: str | None = None,
) -> int:
    """Open a console on the stream ``method`` of the driver at ``path``; returns its index."""
    with sessions.lock(), mapped_errors("Open Console"):
        level = _check_options(encoding, encoding_errors, loglevel, scope)
        if prompt:
            _compile(prompt, bool(prompt_is_regexp))
        opened = context.session(session)
        opened.check_alive()
        node = _resolve(opened, path)
        if len(_consoles) and all(console.closed for console in _consoles):
            _consoles.empty_cache()  # indices start from 1 again, as after Close All Consoles
        name = alias or str(len(_consoles) + 1)
        console = Console(
            name,
            opened,
            node,
            method=method,
            timeout=_secs(timeout),
            newline=_newline(newline),
            encoding=encoding,
            encoding_errors=encoding_errors,
            loglevel=level,
            transcript=context.artifact_path(f"console-{name}", ".log").resolve(),
        )
        if prompt:
            console.prompt = (prompt, bool(prompt_is_regexp))
        console.open()

        def close() -> None:
            console.close()
            _forget(console)

        try:
            console.closer = context.add_scoped(close, scope, opened)
        except BaseException:
            console.close()
            raise
        index = _consoles.register(console, alias)
        robot_logger.info(
            f"Opened console '{name}' on {path} ({method}) of exporter '{opened.exporter}'; "
            f"transcript: {console.transcript_path}"
        )
        return index


@contextlib.contextmanager
def _using(keyword_name: str, alias: str | int | None = None) -> Generator[Console]:
    """Hold the session lock, map errors and yield the current (or named) open console."""
    with sessions.lock(), mapped_errors(keyword_name):
        console = _get(alias)
        console.check()
        yield console


def _prompt(console: Console) -> re.Pattern[str]:
    if console.prompt is None:
        raise Failure(f"Console '{console.name}' has no prompt; set one with Open Console or Set Console Prompt.")
    text, regexp = console.prompt
    return _compile(text, regexp)


def _timeout(timeout: str | None) -> float | None:
    return None if timeout in (None, "") else _secs(timeout)


class ConsoleKeywords:
    """Consoles over any driver byte stream: serial ports, network connections, the QEMU console."""

    # ---- opening and switching ----

    @keyword(tags=TAGS)
    def open_console(
        self,
        path: str,
        alias: str | None = None,
        method: str = "connect",
        timeout: str = "10s",
        newline: str = "LF",
        prompt: str | None = None,
        prompt_is_regexp: bool = False,
        encoding: str = "UTF-8",
        encoding_errors: str = "replace",
        loglevel: str = "INFO",
        scope: str = "suite",
        session: str | None = None,
    ) -> int:
        """Open a console on the byte stream of the driver at ``path`` and make it current.

        Returns the console's index, which `Switch Console` accepts like
        ``alias``. ``method`` is the driver's stream: ``connect`` for serial
        ports and network drivers, ``observe`` for a read-only view of a
        serial port whose write token another client holds.

        ``timeout`` is how long the read keywords wait by default,
        ``newline`` what `Console Write Line` appends (``LF``, ``CRLF``,
        ``CR`` or a literal string), and ``prompt`` (a regular expression
        when ``prompt_is_regexp`` is true) what `Console Read Until Prompt`
        and `Console Execute Command` wait for. Output is decoded with
        ``encoding``; undecodable bytes are handled as ``encoding_errors``
        says (``replace``, ``ignore``, ``strict``...). What the keywords read
        is logged at ``loglevel`` (``NONE`` to not log it).

        The console is closed at the end of ``scope`` (``test``, ``suite``
        or ``global``), by `Close Console`, or when the session closes.
        Everything received is written to
        ``${OUTPUT DIR}/jumpstarter/console-<alias>-<n>.log``.

        | ${index}= | Open Console | dut.console | prompt=root@dut:~# |
        | Open Console | dut.console | alias=boot | timeout=2 min | scope=test |
        | Open Console | dut.console | method=observe | alias=watch |
        """
        return open_console(
            path,
            alias=alias,
            method=method,
            timeout=timeout,
            newline=newline,
            prompt=prompt,
            prompt_is_regexp=prompt_is_regexp,
            encoding=encoding,
            encoding_errors=encoding_errors,
            loglevel=loglevel,
            scope=scope,
            session=session,
        )

    @keyword(tags=TAGS)
    def switch_console(self, index_or_alias: str) -> int | None:
        """Make the console with ``index_or_alias`` current; returns the index of the previous one.

        | ${previous}= | Switch Console | boot |
        | Switch Console | ${previous} |
        """
        with sessions.lock():
            previous = _consoles.current_index
            _consoles.switch(index_or_alias)
            return previous

    @keyword(tags=TAGS)
    def close_console(self, alias: str | None = None) -> None:
        """Close the current console, or the one with index or ``alias``.

        Closing the current console leaves no console current until
        `Open Console` or `Switch Console`.

        | Close Console |
        | Close Console | boot |
        """
        with sessions.lock(), mapped_errors("Close Console"):
            console = _get(alias)
            console.closer()
            _forget(console)

    @keyword(tags=TAGS)
    def close_all_consoles(self) -> None:
        """Close every open console; indices start from 1 again.

        | [Teardown] | Close All Consoles |
        """
        with sessions.lock(), mapped_errors("Close All Consoles"):
            for console in list(_consoles):
                console.closer()
            _consoles.empty_cache()

    # ---- settings ----

    @keyword(tags=TAGS)
    def set_console_timeout(self, timeout: str) -> str:
        """Set the current console's default read timeout; returns the previous one.

        | ${old}= | Set Console Timeout | 5 min |
        | Set Console Timeout | ${old} |
        """
        with _using("Set Console Timeout") as console:
            previous, console.timeout = console.timeout, _secs(timeout)
            return secs_to_timestr(previous)

    @keyword(tags=TAGS)
    def set_console_prompt(self, prompt: str | None, prompt_is_regexp: bool = False) -> tuple[str | None, bool]:
        """Set the current console's prompt; returns the previous ``(prompt, prompt_is_regexp)``.

        An empty ``prompt`` removes it.

        | ${old}= | Set Console Prompt | ^\\\\S+@\\\\S+[$#] | prompt_is_regexp=True |
        | Set Console Prompt | @{old} |
        """
        with _using("Set Console Prompt") as console:
            previous = console.prompt or (None, False)
            if prompt:
                _compile(prompt, bool(prompt_is_regexp))
                console.prompt = (prompt, bool(prompt_is_regexp))
            else:
                console.prompt = None
            return previous

    @keyword(tags=TAGS)
    def set_console_newline(self, newline: str) -> str:
        """Set what `Console Write Line` appends (``LF``, ``CRLF``, ``CR`` or a literal); returns the previous one.

        | ${old}= | Set Console Newline | CRLF |
        """
        with _using("Set Console Newline") as console:
            previous, console.newline = console.newline, _newline(newline)
            return previous

    # ---- writing ----

    @keyword(tags=TAGS)
    def console_write(self, text: str) -> None:
        """Write ``text`` to the current console, without a newline.

        | Console Write | y |
        """
        with _using("Console Write") as console:
            console.write(text)

    @keyword(tags=TAGS)
    def console_write_line(self, text: str = "") -> None:
        """Write ``text`` and the console's newline to the current console.

        | Console Write Line | uname -a |
        | Console Write Line | # just a newline |
        """
        with _using("Console Write Line") as console:
            console.write(f"{text}{console.newline}")

    @keyword(tags=TAGS)
    def console_write_control_character(self, character: str) -> None:
        """Write a control character to the current console.

        ``character`` is a letter for its control combination (``C`` is
        Ctrl+C), ``^C`` or ``CTRL+C``, a name (``ESC``, ``EOT``, ``NUL``,
        ``DEL``...), a code (``0x03``) or the character itself.

        | Console Write Control Character | C |
        | Console Write Control Character | ESC |
        | Console Write Control Character | \\x04 |
        """
        with _using("Console Write Control Character") as console:
            console.write(control_character(character))

    # ---- reading ----

    @keyword(tags=TAGS)
    def console_read(self, delay: str = "0.1s") -> str:
        """Wait ``delay`` for output and return everything received and not yet read.

        | ${output}= | Console Read |
        | ${output}= | Console Read | delay=2s |
        """
        with _using("Console Read") as console:
            console.wait_quiet(_secs(delay))
            return console.take()

    @keyword(tags=TAGS)
    def console_read_until(self, expected: str, timeout: str | None = None) -> str:
        """Read until ``expected`` appears; returns the output up to and including it.

        Fails after ``timeout`` (default: the console's timeout), showing the
        end of the output received.

        | ${output}= | Console Read Until | login: |
        | Console Read Until | Starting kernel | timeout=1 min |
        """
        with _using("Console Read Until") as console:
            output, _ = console.wait_for(_literal(str(expected)), _timeout(timeout), f"'{expected}'")
            return output

    @keyword(tags=TAGS)
    def console_read_until_regexp(self, *patterns: str, timeout: str | None = None) -> str:
        """Read until one of the regular expressions ``patterns`` matches; returns the output up to the match's end.

        The earliest match in the output wins. Output is searched as it
        arrives, so a match longer than 64 KiB may be missed.

        | ${output}= | Console Read Until Regexp | (login|Password): |
        | Console Read Until Regexp | Kernel panic | \\\\$ $ | timeout=2 min |
        """
        if not patterns:
            raise Failure("Console Read Until Regexp needs at least one pattern.")
        compiled = [_compile(pattern) for pattern in patterns]
        with _using("Console Read Until Regexp") as console:
            what = "match for " + " or ".join(f"'{p.pattern}'" for p in compiled)
            output, _ = console.wait_for(_patterns(compiled), _timeout(timeout), what)
            return output

    @keyword(tags=TAGS)
    def console_read_until_prompt(self, strip_prompt: bool = False, timeout: str | None = None) -> str:
        """Read until the console's prompt; returns the output, with the prompt unless ``strip_prompt``.

        | Console Write Line | ls |
        | ${output}= | Console Read Until Prompt | strip_prompt=True |
        """
        with _using("Console Read Until Prompt") as console:
            pattern = _prompt(console)
            output, match = console.wait_for(_patterns([pattern]), _timeout(timeout), f"prompt '{pattern.pattern}'")
            if strip_prompt and match is not None:
                output = output[: len(output) - len(match.group(0))]
            return output

    @keyword(tags=TAGS)
    def console_expect(self, pattern: str, timeout: str | None = None) -> list[str | None]:
        """Read until regular expression ``pattern`` matches; returns the match and its groups.

        The list is ``[whole match, group 1, group 2, ...]``.

        | ${match} | ${version}= | Console Expect | U-Boot (\\\\S+) |
        | ${m}= | Console Expect | IP: (\\\\d+\\\\.\\\\d+\\\\.\\\\d+\\\\.\\\\d+) | timeout=30s |
        """
        compiled = _compile(pattern)
        with _using("Console Expect") as console:
            _, match = console.wait_for(_patterns([compiled]), _timeout(timeout), f"match for '{compiled.pattern}'")
            assert match is not None
            return [match.group(0), *match.groups()]

    @keyword(tags=TAGS)
    def console_execute_command(self, command: str, timeout: str | None = None, strip_prompt: bool = True) -> str:
        """Write ``command`` and a newline, read until the prompt and return the command's output.

        The echoed command is removed, and so is the prompt unless
        ``strip_prompt`` is false; line endings become ``\\n``. Needs a
        prompt (see `Open Console` and `Set Console Prompt`).

        | ${kernel}= | Console Execute Command | uname -r |
        | ${output}= | Console Execute Command | dmesg | timeout=1 min |
        """
        with _using("Console Execute Command") as console:
            pattern = _prompt(console)
            console.write(f"{command}{console.newline}")
            output, match = console.wait_for(_patterns([pattern]), _timeout(timeout), f"prompt '{pattern.pattern}'")
            if strip_prompt and match is not None:
                output = output[: len(output) - len(match.group(0))]
            output = _strip_echo(output, str(command)).replace("\r\n", "\n")
            return output.rstrip("\r\n") if strip_prompt else output

    @keyword(tags=TAGS)
    def console_should_contain(self, text: str, timeout: str | None = None) -> None:
        """Wait for ``text`` to appear on the console; fails if it does not within ``timeout``.

        The output up to and including ``text`` is read.

        | Console Should Contain | Linux version |
        | Console Should Contain | login: | timeout=3 min |
        """
        with _using("Console Should Contain") as console:
            console.wait_for(_literal(str(text)), _timeout(timeout), f"'{text}'")

    @keyword(tags=TAGS)
    def console_should_not_contain(self, text: str, duration: str = "5s") -> None:
        """Watch the console for ``duration``; fails as soon as ``text`` appears.

        Everything received meanwhile is read.

        | Console Should Not Contain | Kernel panic | duration=30s |
        """
        with _using("Console Should Not Contain") as console:
            if console.watch(_literal(str(text)), _secs(duration)):
                raise Failure(f"Console '{console.name}' contained '{text}'.{console.tail()}")

    @keyword(tags=TAGS)
    def console_login(
        self,
        username: str,
        password: str,
        login_prompt: str = "login:",
        password_prompt: str = "Password:",
        login_incorrect: str = "Login incorrect",
        timeout: str | None = None,
    ) -> str:
        """Log in on the console; returns the output read. The password is never logged.

        Waits for ``login_prompt``, writes ``username``, waits for
        ``password_prompt`` and writes ``password``. With a prompt set, waits
        for it and fails if ``login_incorrect`` appears first; otherwise
        reads what arrives within a second.

        | Console Login | root | ${PASSWORD} |
        | Console Login | admin | ${PASSWORD} | login_prompt=Username: |
        """
        with _using("Console Login") as console:
            limit = _timeout(timeout)
            output, _ = console.wait_for(_literal(login_prompt), limit, f"login prompt '{login_prompt}'")
            console.write(f"{username}{console.newline}")
            more, _ = console.wait_for(_literal(password_prompt), limit, f"password prompt '{password_prompt}'")
            output += more
            console.write(f"{password}{console.newline}", log=False)
            console.log(f"Wrote the password to console '{console.name}'.")
            return output + _after_login(console, login_incorrect, limit)

    @keyword(tags=TAGS)
    def clear_console_buffer(self) -> None:
        """Discard the output received and not yet read; the transcript keeps it.

        | Clear Console Buffer |
        | Console Write Line | reboot |
        """
        with _using("Clear Console Buffer") as console:
            console.take()

    @keyword(tags=TAGS)
    def get_console_transcript(self, alias: str | None = None, as_file: bool = False) -> str:
        """Return everything the current console (or ``alias``) received, or its transcript file with ``as_file``.

        Works on closed consoles too.

        | ${log}= | Get Console Transcript |
        | ${file}= | Get Console Transcript | boot | as_file=True |
        """
        with sessions.lock(), mapped_errors("Get Console Transcript"):
            console = _get(alias)
            return str(console.transcript_path) if as_file else console.transcript()


def _after_login(console: Console, login_incorrect: str, timeout: float | None) -> str:
    if console.prompt is None:
        console.wait_quiet(1.0)
        return console.take()
    prompt = _prompt(console)
    patterns = [prompt, _compile(login_incorrect, regexp=False)] if login_incorrect else [prompt]
    output, match = console.wait_for(_patterns(patterns), timeout, f"prompt '{prompt.pattern}' after login")
    if login_incorrect and match is not None and match.re is patterns[1]:
        raise Failure(f"Login on console '{console.name}' failed: '{login_incorrect}'.{console.tail()}")
    return output


def _open_node_console(
    path: str,
    session: str | None,
    alias: str | None = None,
    method: str = "connect",
    timeout: str = "10s",
    newline: str = "LF",
    prompt: str | None = None,
    prompt_is_regexp: bool = False,
    encoding: str = "UTF-8",
    encoding_errors: str = "replace",
    loglevel: str = "INFO",
    scope: str = "suite",
) -> int:
    """Open a console on this serial port and make it current; returns its index.

    The console keywords of the ``Jumpstarter`` library (``Console Read
    Until``, ``Console Execute Command``...) then read and write it.
    ``method=observe`` opens a read-only view, for when another client holds
    the port's write token. See ``Open Console`` in the ``Jumpstarter``
    library for the other arguments.

    | ${index}= | Dut Console Open Console | prompt=# |
    | Console Read Until | login: |
    """
    return open_console(
        path,
        alias=alias,
        method=method,
        timeout=timeout,
        newline=newline,
        prompt=prompt,
        prompt_is_regexp=prompt_is_regexp,
        encoding=encoding,
        encoding_errors=encoding_errors,
        loglevel=loglevel,
        scope=scope,
        session=session,
    )


register_node_extension({"PySerialClient"}, "Open Console", _open_node_console, tags=TAGS)
