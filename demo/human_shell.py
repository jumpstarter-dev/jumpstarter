"""Drive a real interactive shell in a pty and record it as an asciicast.

Only the keystroke timing is synthesised: the commands, their output and their
exit codes are real. That makes the same script usable both for generating the
demo recording and for verifying the flow in CI.

The outer shell runs with a PS1 that carries invisible OSC 133 semantic prompt
markers, so the harness can tell prompts apart from command output and read the
exit code of every command without printing anything extra.
"""

from __future__ import annotations

import codecs
import fcntl
import json
import os
import pty
import random
import re
import select
import signal
import struct
import sys
import termios
import time

ESC = "\033"
BEL = "\a"

# OSC 133 semantic prompt markers, terminated with BEL rather than ST: ST ends
# in a backslash, which bash would eat as an escape while expanding PS1.
_OSC_PROMPT = f"{ESC}]133;A{BEL}"
_OSC_EXIT = f"{ESC}]133;D;$?{BEL}"

#: PS1 for the outer shell. Renders as "jumpstarter $ ".
OUTER_PS1 = f"\\[{_OSC_EXIT}\\]\\[{_OSC_PROMPT}\\]jumpstarter $ "

# Shells spawned by `jmp shell` and `j mount` set a PS1 of their own, which
# drops the markers and would leave every command inside them unchecked. This
# puts them back in front of whatever prompt the nested shell chose, so it
# still looks the same. Written with bash's own \e and \a escapes rather than
# literal control characters, because ESC cannot be typed into readline.
_REARM_PS1 = "PS1='\\[\\e]133;D;$?\\a\\]\\[\\e]133;A\\a\\]'\"$PS1\""

# `jmp shell` and `j mount` colour their prompts, so there is an ANSI reset
# between the arrow and the trailing space: match the arrow only.
#: Tail of the prompt of a shell spawned by `jmp shell`.
JMP_PROMPT = "\u27a4"

#: How narration typed by comment() is styled in the recording. Bold cyan
#: reads as an annotation against the default output and the coloured prompts.
COMMENT_STYLE = "\033[1;36m"
RESET_STYLE = "\033[0m"

#: Seconds per character a line of narration is left on screen after being
#: typed. About 110 words per minute, well below normal reading speed: the
#: viewer is also watching the terminal, and may not have the context.
READING_PACE = 0.045

#: Tail of the prompt of the subshell spawned by `j mount`.
MOUNT_PROMPT = "(mount)\u27a4"

PROMPT_RE = re.compile(re.escape(_OSC_PROMPT.encode()))
EXIT_RE = re.compile(rb"\x1b\]133;D;(\d+)\x07")

_ANSI_RE = re.compile(
    rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL/ST
    rb"|\x1b\[[0-?]*[ -/]*[@-~]"  # CSI
    rb"|\x1b[@-Z\\-_]"  # two byte sequences
    rb"|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"  # stray control characters
)


class DemoFailure(RuntimeError):
    """Raised when a step does not behave as the demo expects."""


def strip_ansi(data: bytes) -> str:
    """Return `data` as plain text, without escape sequences or carriage returns."""
    text = _ANSI_RE.sub(b"", data).decode("utf-8", "replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


class Asciicast:
    """Incrementally writes an asciicast v2 file.

    Gaps longer than `idle_limit` are compressed so that slow operations such as
    flashing do not turn into minutes of dead air during playback.
    """

    def __init__(self, path, *, cols, rows, idle_limit=2.0, title=None):
        self.path = path
        self.idle_limit = idle_limit
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._start = time.monotonic()
        self._last_real = 0.0
        self._clock = 0.0
        self._hold = 0.0
        # Deliberately not a context manager: the recording is written
        # incrementally for as long as the demo runs, so the handle has to
        # outlive this call. close() is driven from board_demo.py's finally.
        self._file = open(path, "w", encoding="utf-8")  # noqa: SIM115
        header = {
            "version": 2,
            "width": cols,
            "height": rows,
            "timestamp": int(time.time()),
            "env": {"SHELL": "/bin/bash", "TERM": "xterm-256color"},
        }
        if title:
            header["title"] = title
        self._emit_raw(header)

    def _emit_raw(self, obj):
        self._file.write(json.dumps(obj) + "\n")
        self._file.flush()

    def _timestamp(self):
        real = time.monotonic() - self._start
        gap = real - self._last_real
        self._last_real = real
        if self.idle_limit is not None:
            gap = min(gap, self.idle_limit)
        self._clock += gap
        return round(self._clock, 6)

    def pause(self, seconds: float):
        """Put `seconds` of playback time in, past the idle cap.

        The cap exists to cut dead air out of slow operations, but a pause
        left on purpose - time to read a line of narration - is pacing, and
        has to survive it. Real time already spent is absorbed so that the
        pause lands at exactly `seconds` rather than being counted twice.
        """
        self._last_real = time.monotonic() - self._start
        self._clock += seconds

    def write(self, data: bytes):
        text = self._decoder.decode(data)
        if text:
            self._emit_raw([self._timestamp(), "o", text])

    def marker(self, label: str):
        self._emit_raw([self._timestamp(), "m", label])

    def close(self):
        tail = self._decoder.decode(b"", final=True)
        if tail:
            self._emit_raw([self._timestamp(), "o", tail])
        self._file.close()

    @property
    def duration(self):
        return self._clock


class HumanShell:
    """An interactive shell in a pty, typed into at human speed."""

    def __init__(
        self,
        *,
        cast: Asciicast | None = None,
        cols: int = 110,
        rows: int = 32,
        shell: str = "/bin/bash",
        seed: int = 20260929,
        speed: float = 1.0,
        env: dict | None = None,
        mirror=None,
        comment_style: str = COMMENT_STYLE,
    ):
        self.cast = cast
        self.cols = cols
        self.rows = rows
        self.speed = speed
        self.rng = random.Random(seed)
        self.mirror = mirror if mirror is not None else sys.stdout.buffer
        self.buf = bytearray()
        self.prompt = PROMPT_RE
        self.comment_style = comment_style
        # While set, re-applied after every chunk the shell echoes, because
        # readline redraws the prompt mid-line and the prompt ends by
        # resetting colour.
        self._echo_style = None
        self._prompt_stack = []
        self.closed = False

        child_env = dict(os.environ)
        child_env.update(
            {
                "PS1": OUTER_PS1,
                "SHELL": shell,
                "TERM": "xterm-256color",
                "COLUMNS": str(cols),
                "LINES": str(rows),
                "HISTFILE": "/dev/null",
                "PAGER": "cat",
                "GIT_PAGER": "cat",
                "PYTHONUNBUFFERED": "1",
            }
        )
        child_env.pop("PROMPT_COMMAND", None)
        child_env.pop("NO_COLOR", None)
        if env:
            child_env.update(env)

        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # child
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
                os.execvpe(shell, [shell, "--norc", "--noprofile", "-i"], child_env)
            finally:
                os._exit(127)

        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.wait_prompt(timeout=15)
        self.buf.clear()

    # -- plumbing ---------------------------------------------------------

    def _pump(self, timeout=0.02) -> bool:
        """Move one chunk of child output to the recording. False on EOF."""
        try:
            ready, _, _ = select.select([self.fd], [], [], timeout)
        except (OSError, ValueError):
            return False
        if not ready:
            return True
        try:
            data = os.read(self.fd, 65536)
        except OSError:
            return False
        if not data:
            return False
        self.buf.extend(data)
        if self.mirror is not None:
            self.mirror.write(data)
            self.mirror.flush()
        if self.cast is not None:
            self.cast.write(data)
        # Plain echoed characters keep the current colour; only re-apply it
        # when the shell emitted an escape sequence, which is what clears it.
        if self._echo_style and b"\x1b" in data:
            self._emit(self._echo_style)
        return True

    def _emit(self, text: str):
        """Write straight to the recording, bypassing the shell.

        Colour cannot be typed: ESC is readline's meta prefix, so sending an
        escape sequence as input triggers key bindings instead of styling.
        Writing it to the stream instead styles whatever the shell echoes
        next, which is what makes narration stand out.
        """
        data = text.encode()
        if self.mirror is not None:
            self.mirror.write(data)
            self.mirror.flush()
        if self.cast is not None:
            self.cast.write(data)

    def sleep(self, seconds: float):
        """Sleep while keeping the child's output flowing."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._pump(min(remaining, 0.05))

    def hold(self, seconds: float):
        """Pause without the recording compressing it away.

        Used to leave narration on screen long enough to read. Scaled by
        `speed` like everything else, so --speed 8 does not spend the time.
        """
        seconds = seconds / self.speed
        self.sleep(seconds)
        if self.cast is not None:
            self.cast.pause(seconds)

    def expect(self, pattern, *, timeout=120, description=None):
        """Wait until `pattern` (bytes regex) shows up. Returns its match end."""
        start = len(self.buf)
        deadline = time.monotonic() + timeout
        while True:
            match = pattern.search(self.buf, start)
            if match:
                return match.end()
            if time.monotonic() > deadline:
                raise DemoFailure(
                    f"timed out after {timeout}s waiting for "
                    f"{description or pattern.pattern!r}"
                )
            if not self._pump(0.2):
                raise DemoFailure(
                    f"shell exited while waiting for {description or pattern.pattern!r}"
                )

    def wait_prompt(self, timeout=120):
        return self.expect(self.prompt, timeout=timeout, description="shell prompt")

    # -- typing -----------------------------------------------------------

    def _keystroke_delay(self, char: str) -> float:
        base = 0.07 / self.speed
        delay = self.rng.gauss(base, base * 0.4)
        delay = max(base * 0.3, delay)
        if char == " " and self.rng.random() < 0.15:
            delay += base * 4
        return delay

    def type(self, text: str, *, enter=True, rate=1.0):
        """Type `text` a character at a time. `rate` scales the speed."""
        for char in text:
            os.write(self.fd, char.encode())
            self.sleep(self._keystroke_delay(char) / rate)
        if enter:
            self.sleep(self.rng.uniform(0.2, 0.5) / (self.speed * rate))
            os.write(self.fd, b"\r")

    def send(self, data: bytes):
        os.write(self.fd, data)

    def comment(self, text: str, *, think=0.7, rate=1.25, dwell=None, timeout=30):
        """Type a shell comment, to narrate the recording.

        bash ignores the line and leaves `$?` alone, so unlike run() this
        deliberately does not check an exit code: the status still belongs
        to whatever ran before it. Prose is typed a little faster than
        commands, which is both how people type and cheaper in recording
        time, and then left on screen for `dwell` seconds so it can be read
        before the next thing happens. By default that is derived from its
        length, at a deliberately unhurried reading pace.
        """
        self.sleep(think / self.speed)
        self._emit(self.comment_style)
        self._echo_style = self.comment_style
        try:
            self.type(f"# {text}", rate=rate)
        finally:
            self._echo_style = None
            self._emit(RESET_STYLE)
        self.wait_prompt(timeout=timeout)
        self.hold(dwell if dwell is not None else READING_PACE * len(text) + 0.6)

    # -- steps ------------------------------------------------------------

    def run(
        self,
        command: str,
        *,
        expect=None,
        refute=None,
        timeout=120,
        allow_failure=False,
        think=1.0,
        settle=1.2,
        retries=0,
        retry_delay=10.0,
        retry_budget=None,
    ) -> str:
        """Type `command`, wait for it to finish, and check what it produced.

        `expect` and `refute` are regex strings matched against the plain-text
        output with escape sequences removed. With `retries`, the command is
        simply typed again, the way someone would retry a flaky connection.
        `settle` leaves the output on screen before the next step starts.

        `retry_budget` is an alternative to `retries` for callers whose limit
        is a deadline - "the board has N seconds to come back" - rather than a
        number of attempts. It caps the wall clock spent in the whole loop,
        trimming the last attempt's `timeout` to fit, so the budget means what
        it says. Deriving a count from a deadline instead does not: a count
        multiplies by `timeout + retry_delay`, which overshoots whenever an
        attempt actually runs long.
        """
        deadline = None if retry_budget is None else time.monotonic() + retry_budget
        attempt = 0
        while True:
            if deadline is None:
                attempt_timeout = timeout
            else:
                attempt_timeout = max(1.0, min(timeout, deadline - time.monotonic()))
            try:
                return self._run_once(
                    command,
                    expect=expect,
                    refute=refute,
                    timeout=attempt_timeout,
                    allow_failure=allow_failure,
                    think=think,
                    settle=settle,
                )
            except DemoFailure:
                attempt += 1
                if deadline is None:
                    spent = attempt > retries
                else:
                    # Only retry if the delay plus a usable attempt still fit.
                    spent = time.monotonic() + retry_delay >= deadline
                if spent or not self._at_prompt():
                    raise
                self.sleep(retry_delay)

    def _at_prompt(self) -> bool:
        """Best effort check that the shell is idle enough to retype a command."""
        if self.prompt.search(self.buf, max(0, len(self.buf) - 4096)):
            return True
        self.send(b"\x03")
        try:
            self.wait_prompt(timeout=15)
        except DemoFailure:
            return False
        return True

    def _run_once(self, command, *, expect, refute, timeout, allow_failure, think, settle):
        self.sleep(think / self.speed)
        self.type(command)
        start = len(self.buf)
        end = self.wait_prompt(timeout=timeout)
        raw = bytes(self.buf[start:end])
        output = strip_ansi(raw)

        codes = EXIT_RE.findall(raw)
        if codes and not allow_failure:
            code = int(codes[-1])
            if code != 0:
                raise DemoFailure(f"`{command}` exited with status {code}\n{output}")
        self._check(command, output, expect, refute)
        self.hold(settle)
        return output

    def watch(
        self,
        command: str,
        *,
        until: str,
        timeout=600,
        interrupt=b"\x03",
        expect=None,
        refute=None,
        think=0.8,
    ) -> str:
        """Run a streaming command, wait for `until` in its output, then stop it.

        Used for things like `j serial pipe`, which only ends on Ctrl+C.
        """
        self.sleep(think / self.speed)
        self.type(command)
        start = len(self.buf)
        self.expect(
            re.compile(until.encode()), timeout=timeout, description=f"{until!r} in `{command}`"
        )
        self.sleep(1.0)
        self.send(interrupt)
        end = self.wait_prompt(timeout=60)
        output = strip_ansi(bytes(self.buf[start:end]))
        self._check(command, output, expect, refute)
        return output

    def enter(self, command: str, *, prompt: str, expect=None, timeout=300, think=0.8) -> str:
        """Run a command that spawns a nested shell, e.g. `jmp shell`.

        `prompt` is the literal tail of the nested shell's prompt; prompt
        detection switches to it until the matching `leave()`.
        """
        self.sleep(think / self.speed)
        self.type(command)
        start = len(self.buf)
        self._prompt_stack.append(self.prompt)
        self.prompt = re.compile(re.escape(prompt.encode()))
        end = self.expect(self.prompt, timeout=timeout, description=f"{prompt!r} prompt")
        output = strip_ansi(bytes(self.buf[start:end]))
        self._check(command, output, expect, None)
        self._rearm_exit_markers(timeout=timeout)
        return output

    def _rearm_exit_markers(self, *, timeout=60):
        """Restore the exit-code markers on the prompt of a nested shell.

        Housekeeping rather than part of the scenario, so it is kept out of
        the recording: what it redraws is the same prompt that is already on
        screen.
        """
        cast, self.cast = self.cast, None
        try:
            self.send(_REARM_PS1.encode() + b"\r")
            self.wait_prompt(timeout=timeout)
        finally:
            self.cast = cast

    def leave(self, command: str = "exit", *, timeout=120, think=0.8):
        """Leave the shell most recently entered with `enter()`."""
        self.sleep(think / self.speed)
        self.type(command)
        self.prompt = self._prompt_stack.pop()
        self.wait_prompt(timeout=timeout)

    def _check(self, command, output, expect, refute):
        for pattern in [expect] if isinstance(expect, str) else (expect or []):
            if not re.search(pattern, output, re.MULTILINE):
                raise DemoFailure(f"`{command}` did not print /{pattern}/\n{output}")
        for pattern in [refute] if isinstance(refute, str) else (refute or []):
            if re.search(pattern, output, re.MULTILINE):
                raise DemoFailure(f"`{command}` unexpectedly printed /{pattern}/\n{output}")

    def marker(self, label: str):
        if self.cast is not None:
            self.cast.marker(label)

    # -- teardown ---------------------------------------------------------

    def close(self, *, timeout=10):
        if self.closed:
            return
        self.closed = True
        try:
            os.write(self.fd, b"exit\r")
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self._pump(0.2):
            pass
        try:
            os.kill(self.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
