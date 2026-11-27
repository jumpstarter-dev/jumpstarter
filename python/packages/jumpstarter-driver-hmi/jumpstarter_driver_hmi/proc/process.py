"""A supervised child process: one run of it, its pipes read by threads, and a stop that cannot hang.

:class:`ManagedProcess` is **one run** of a child. It starts the process in its own session and process group, reads
its stdout and stderr on threads, reaps it, and ends it in bounded time. Starting it again, and deciding when to
(:class:`Backoff` is the policy), is the owner's job: make a new :class:`ManagedProcess` for each run.

**Threads.** ``start`` creates one *waiter* thread that spawns the child, waits for it, and calls ``on_exit``; two
reader threads read stdout (``on_stdout``) and stderr. The child is spawned **by the waiter thread**, which lives as
long as the child does, because ``PR_SET_PDEATHSIG`` (Linux) ties the child to the *thread* that created it: spawned
from a pooled worker thread that later goes idle and exits, the child would be killed with it.

**Process group.** The child is a session leader, so its group is its own: the whole group is signaled when the child
is stopped, and what is left of the group is killed as soon as the child is reaped. A child that must outlive its
owner does not belong here.

**Input.** :meth:`ManagedProcess.stdin_write` is the one call that can block (while the child does not read). Only the
writer thread of a :class:`~jumpstarter_driver_hmi.proc.writer.CommandWriter` should call it. It gives way when
stdin is closed, which :meth:`ManagedProcess.stop` does first: a write stuck on a child that does not read fails
within a fraction of a second of the stop.

**Stopping.** :meth:`ManagedProcess.stop` asks politely (a stop command through a callback, and the end of stdin),
then SIGTERM, then SIGKILL, all within one deadline, and never raises. It takes its lifecycle lock with a timeout:
when another thread is in the middle of a stop, it goes straight to the signals instead of waiting behind it.
"""

from __future__ import annotations

import contextlib
import ctypes
import errno
import functools
import logging
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence

logger = logging.getLogger(__name__)

#: How much of the child's stdout one read takes.
READ_SIZE = 256 * 1024
#: A run that lasted this long (seconds) before it failed does not count as part of a run of failures.
STABLE_S = 10.0
#: The child's own log (its stderr): lines kept, bytes kept of a line, lines logged per window (``LOG_WINDOW_S``).
STDERR_TAIL_LINES = 20
STDERR_LINE_BYTES = 1000
STDERR_LINES_PER_WINDOW = 100
LOG_WINDOW_S = 10.0
#: What ``stop`` gives its polite step and its SIGTERM step by default, and how long it waits for its own lock at most.
DEFAULT_GRACE_S = 2.0
DEFAULT_TERM_S = 2.0
LOCK_WAIT_S = 0.5
#: Most of the polite step that is spent letting the writer deliver the stop command before stdin is closed.
FLUSH_S = 1.0
#: How long a blocked ``stdin_write`` sleeps before it looks again whether stdin was closed.
WRITE_POLL_S = 0.1
#: How long the waiter lets the readers finish after the child is gone (a grandchild may hold a pipe open).
READER_JOIN_S = 2.0
#: How long ``start`` waits for the spawn itself.
SPAWN_TIMEOUT_S = 10.0

_PR_SET_PDEATHSIG = 1


# -- the restart policy ----------------------------------------------------------------------------------------------


class Backoff:
    """When a child that failed may be started again: a pure policy over an injectable clock.

    The first failure in a row costs nothing (a start that failed once is tried again at once); after the second the
    next start waits ``base_s``, then twice that, and so on up to ``max_s``. A run that lasted ``stable_s`` before it
    failed starts the count again at one failure, and a run that ended as intended (``ok``) forgets all of it.
    """

    def __init__(
        self,
        base_s: float = 1.0,
        max_s: float = 30.0,
        *,
        stable_s: float = STABLE_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Set the delays.

        Raises:
            ValueError: If a delay is negative or ``max_s`` is less than ``base_s``.
        """
        if base_s < 0 or stable_s < 0:
            raise ValueError("delays must not be negative")
        if max_s < base_s:
            raise ValueError("max_s must not be less than base_s")
        self.base_s = base_s
        self.max_s = max_s
        self.stable_s = stable_s
        self._clock = clock
        self._failures = 0
        self._retry_at = 0.0

    @property
    def failures(self) -> int:
        """Failures in a row."""
        return self._failures

    def record_exit(self, lasted_s: float, ok: bool) -> float:
        """Account for the end of a run.

        Args:
            lasted_s: How long the run lasted.
            ok: Whether it ended as intended (a stop, a clean end).

        Returns:
            How long the next start must wait (0 for none).
        """
        if ok:
            self.reset()
            return 0.0
        self._failures = 1 if lasted_s >= self.stable_s else self._failures + 1
        delay = 0.0 if self._failures <= 1 else min(self.base_s * 2 ** min(self._failures - 2, 60), self.max_s)
        self._retry_at = self._clock() + delay
        return delay

    def wait_s(self, now: float | None = None) -> float:
        """Seconds until the next start is allowed (0 if it is allowed now); ``now`` defaults to the clock."""
        return max(0.0, self._retry_at - (self._clock() if now is None else now))

    def reset(self) -> None:
        """Forget the failures (a deliberate stop does this)."""
        self._failures = 0
        self._retry_at = 0.0


# -- the child's log -------------------------------------------------------------------------------------------------


class StderrTail:
    """Keeps the last lines of a child's stderr, each cut short, and logs them within a budget per time window.

    Lines over the budget are kept in the tail but not logged; a warning says how many were not shown.
    """

    def __init__(
        self,
        name: str,
        *,
        lines: int = STDERR_TAIL_LINES,
        line_bytes: int = STDERR_LINE_BYTES,
        lines_per_window: int = STDERR_LINES_PER_WINDOW,
        window_s: float = LOG_WINDOW_S,
        clock: Callable[[], float] = time.monotonic,
        log: logging.Logger | None = None,
    ) -> None:
        """Set the limits (the defaults are the module's ``STDERR_*`` and ``LOG_WINDOW_S``)."""
        self._name = name
        self._line_bytes = line_bytes
        self._per_window = lines_per_window
        self._window_s = window_s
        self._clock = clock
        self._logger = log or logger
        self._lines: deque[str] = deque(maxlen=lines)
        self._lock = threading.Lock()
        self._partial = b""
        self._window_start = clock()
        self._loggerged = 0
        self._suppressed = 0
        #: Lines that were never logged because of the budget, over the whole life.
        self.suppressed_total = 0

    def feed(self, chunk: bytes) -> None:
        """Take what the child wrote (any chunking)."""
        *lines, rest = (self._partial + chunk).split(b"\n")
        self._partial = rest[: self._line_bytes]  # of a line still being written, only what could be shown is kept
        for raw in lines:
            text = raw[: self._line_bytes].decode(errors="replace").rstrip()
            if text:
                self._line(text)

    def finish(self) -> None:
        """The stream ended: what was written without a final newline is a line, and the lines not shown are said."""
        if self._partial:
            text = self._partial.decode(errors="replace").rstrip()
            self._partial = b""
            if text:
                self._line(text)
        self._say_suppressed()

    @property
    def lines(self) -> list[str]:
        """The last lines, oldest first."""
        with self._lock:
            return list(self._lines)

    def _line(self, text: str) -> None:
        with self._lock:
            self._lines.append(text)
        now = self._clock()
        if now - self._window_start >= self._window_s:
            self._say_suppressed()
            self._window_start, self._loggerged = now, 0
        if self._loggerged < self._per_window:
            self._loggerged += 1
            self._logger.info("%s: %s", self._name, text)
        else:
            self._suppressed += 1
            self.suppressed_total += 1

    def _say_suppressed(self) -> None:
        if self._suppressed:
            self._logger.warning("%s: its log was cut: %d lines not shown", self._name, self._suppressed)
            self._suppressed = 0


# -- the process -----------------------------------------------------------------------------------------------------


@functools.cache
def _prctl() -> Callable[..., int] | None:
    """libc's ``prctl`` on Linux (resolved once, in the parent, so the child needs nothing), else ``None``."""
    if not sys.platform.startswith("linux"):
        return None
    try:
        function = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None
    function.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    function.restype = ctypes.c_int
    return function


def pdeathsig_preexec(parent_pid: int | None = None) -> Callable[[], None] | None:
    """A ``preexec_fn`` that gets the child SIGKILL when its parent dies (Linux ``PR_SET_PDEATHSIG``).

    ``None`` on other platforms.

    The signal follows the **thread** that created the child, so the child must be spawned by a thread that lives as
    long as it does (:class:`ManagedProcess` does that). A parent that died before the call is noticed and ends the
    child.
    """
    prctl = _prctl()
    if prctl is None:
        return None
    parent = os.getpid() if parent_pid is None else parent_pid
    sigkill = int(signal.SIGKILL)

    def set_pdeathsig() -> None:  # runs in the child between fork and exec: nothing but the call and a check
        prctl(_PR_SET_PDEATHSIG, sigkill, 0, 0, 0)
        if os.getppid() != parent:  # the parent is already gone: the signal will never come
            os._exit(1)

    return set_pdeathsig


class ManagedProcess:
    """One run of a child process. See the module documentation.

    Callbacks run on this class's threads and must not block: ``on_stdout`` stalls the child's output (and so, in the
    end, the child) for as long as it blocks, and ``on_exit`` delays :meth:`stop`'s return. An exception in one is
    logged and does not end the thread.
    """

    def __init__(
        self,
        argv: Sequence[str],
        *,
        name: str,
        on_stdout: Callable[[bytes], None],
        on_exit: Callable[[int | None], None] | None = None,
        env: Mapping[str, str] | None = None,
        cwd: str | None = None,
        stderr_lines: int = STDERR_TAIL_LINES,
        stderr_line_bytes: int = STDERR_LINE_BYTES,
        stderr_lines_per_window: int = STDERR_LINES_PER_WINDOW,
        log_window_s: float = LOG_WINDOW_S,
        read_size: int = READ_SIZE,
        log: logging.Logger | None = None,
    ) -> None:
        """Describe the run; nothing is started until :meth:`start`.

        Args:
            argv: The program and its arguments (no shell).
            name: What the child is called in logs and thread names.
            on_stdout: Called with each chunk of the child's stdout (1 to ``read_size`` bytes), in order, on the stdout
                reader thread.
            on_exit: Called once, on the waiter thread, with the exit status (negative: the signal that ended it;
                ``None``: it could not be learned) after the child is reaped and its stdout read to the end (or given up
                on). Not called for a child that never started.
            env: The child's whole environment (``None``: this process's).
            cwd: The child's working directory.
            stderr_lines: How many of the child's last stderr lines are kept.
            stderr_line_bytes: A longer stderr line is cut to this.
            stderr_lines_per_window: Most stderr lines logged per ``log_window_s``.
            log_window_s: The length of that window.
            read_size: The largest read of stdout.
            log: The logger to use.

        Raises:
            ValueError: If ``argv`` is empty.
        """
        if not argv:
            raise ValueError("argv must name a program")
        self._argv = [str(a) for a in argv]
        self.name = name
        self._on_stdout = on_stdout
        self._on_exit = on_exit
        self._env = None if env is None else dict(env)
        self._cwd = cwd
        self._read_size = read_size
        self._logger = log or logger
        self._stderr = StderrTail(
            name,
            lines=stderr_lines,
            line_bytes=stderr_line_bytes,
            lines_per_window=stderr_lines_per_window,
            window_s=log_window_s,
            log=self._logger,
        )
        self._lifecycle = threading.Lock()
        self._stdin_lock = threading.Lock()
        self._stdin = None
        self._proc: subprocess.Popen | None = None
        self._pgid: int | None = None
        self._waiter: threading.Thread | None = None
        self._spawned = threading.Event()
        self._spawn_error: BaseException | None = None
        self._exited = threading.Event()
        self._done = threading.Event()
        self._stop_requested = threading.Event()
        self._returncode: int | None = None
        self._callback_errors = 0
        self.started_at: float | None = None
        #: ``time.monotonic()`` of the last chunk of stdout (``None`` before the first) and the bytes read: a stall
        #: watchdog's inputs.
        self.last_stdout_at: float | None = None
        self.stdout_bytes = 0

    # -- state -------------------------------------------------------------------------------------------------------

    @property
    def pid(self) -> int | None:
        """The child's process id (``None`` before it started)."""
        return self._proc.pid if self._proc is not None else None

    @property
    def alive(self) -> bool:
        """Whether the child is running."""
        return self._proc is not None and not self._exited.is_set()

    @property
    def returncode(self) -> int | None:
        """The exit status once the child is reaped (negative: ended by that signal), else ``None``."""
        return self._returncode

    @property
    def stopping(self) -> bool:
        """Whether :meth:`stop` has been called: the child's exit is then expected."""
        return self._stop_requested.is_set()

    @property
    def stderr_tail(self) -> list[str]:
        """The last lines of the child's stderr, oldest first (each cut to ``stderr_line_bytes``)."""
        return self._stderr.lines

    def stats(self) -> dict[str, object]:
        """Counters for a status report."""
        return {
            "pid": self.pid,
            "alive": self.alive,
            "returncode": self._returncode,
            "stdout_bytes": self.stdout_bytes,
            "stderr_lines_not_logged": self._stderr.suppressed_total,
            "callback_errors": self._callback_errors,
        }

    def wait(self, timeout_s: float | None = None) -> bool:
        """Wait for the child to end and its ``on_exit`` to return. ``True`` if it has."""
        return self._done.wait(timeout_s)

    # -- start -------------------------------------------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the child and return once it exists. Call it once, from a worker thread.

        Raises:
            RuntimeError: If the child could not be spawned, was already started, or :meth:`stop` was called first.
        """
        with self._lifecycle:
            if self._stop_requested.is_set():
                raise RuntimeError(f"{self.name} was stopped before it started")
            if self._waiter is not None:
                raise RuntimeError(f"{self.name} was already started")
            self._waiter = threading.Thread(target=self._run, name=f"{self.name}-wait", daemon=True)
            self._waiter.start()
            if not self._spawned.wait(SPAWN_TIMEOUT_S):
                self._stop_requested.set()  # the waiter ends whatever it spawns late
                raise RuntimeError(f"{self.name} could not be started within {SPAWN_TIMEOUT_S:g} s")
        if self._spawn_error is not None:
            raise RuntimeError(f"{self.name} could not be started: {self._spawn_error}") from self._spawn_error

    def _run(self) -> None:
        """The waiter thread: spawn, read, wait, sweep, report."""
        try:
            try:
                proc = self._spawn()
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                self._spawn_error = error
                return
            finally:
                self._spawned.set()
            readers = [
                threading.Thread(target=self._guarded, args=(target, proc), name=f"{self.name}-{label}", daemon=True)
                for label, target in (("out", self._read_stdout), ("err", self._read_stderr))
            ]
            for reader in readers:
                reader.start()
            if self._stop_requested.is_set():  # stop() came while the spawn was under way
                self._signal_group(signal.SIGKILL)
            try:
                returncode: int | None = proc.wait()
            except Exception:
                self._logger.exception("%s: waiting for the child failed", self.name)
                returncode = None
            self._returncode = returncode
            self._exited.set()
            self._sweep_group()  # whatever the child left in its group goes with it
            deadline = time.monotonic() + READER_JOIN_S
            for reader in readers:
                reader.join(max(0.0, deadline - time.monotonic()))
            self._close_stdin()
            if self._on_exit is not None:
                try:
                    self._on_exit(returncode)
                except Exception:
                    self._logger.exception("%s: the exit callback raised", self.name)
        finally:
            self._exited.set()
            self._done.set()

    def _spawn(self) -> subprocess.Popen:
        preexec = pdeathsig_preexec()
        proc = subprocess.Popen(  # the owner's own program and arguments, no shell
            self._argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=self._env,
            cwd=self._cwd,
            close_fds=True,
            start_new_session=True,  # its own session and group: a Ctrl+C at our terminal reaches it through us only
            preexec_fn=preexec,  # noqa: PLW1509 (PR_SET_PDEATHSIG must be set between fork and exec; it only calls prctl)
        )
        assert proc.stdin is not None  # the pipes were asked for
        # stdin_write waits for room itself, in slices, so that closing stdin can end the wait
        os.set_blocking(proc.stdin.fileno(), False)
        with self._stdin_lock:
            self._stdin = proc.stdin
        self._pgid = proc.pid
        self.started_at = time.monotonic()
        self._proc = proc
        self._logger.info("started %s (pid %d)", self.name, proc.pid)
        return proc

    # -- the pipes ---------------------------------------------------------------------------------------------------

    def _guarded(self, target: Callable[[subprocess.Popen], None], proc: subprocess.Popen) -> None:
        try:
            target(proc)
        except Exception:
            self._logger.exception("%s: a reader thread failed", self.name)

    def _read_stdout(self, proc: subprocess.Popen) -> None:
        stream = proc.stdout
        assert stream is not None  # the pipes were asked for
        try:
            while chunk := stream.read(self._read_size):
                self.stdout_bytes += len(chunk)
                self.last_stdout_at = time.monotonic()
                try:
                    self._on_stdout(chunk)
                except Exception:
                    self._callback_errors += 1
                    if self._callback_errors == 1:
                        self._logger.exception("%s: the stdout callback raised (more are only counted)", self.name)
        except (OSError, ValueError):
            pass  # the pipe closed under us
        finally:
            with contextlib.suppress(Exception):
                stream.close()

    def _read_stderr(self, proc: subprocess.Popen) -> None:
        stream = proc.stderr
        assert stream is not None  # the pipes were asked for
        try:
            while chunk := stream.read(16384):
                self._stderr.feed(chunk)
        except (OSError, ValueError):
            pass
        finally:
            self._stderr.finish()
            with contextlib.suppress(Exception):
                stream.close()

    def stdin_write(self, data: bytes) -> None:
        """Write all of ``data`` to the child's stdin, waiting for as long as the child does not read.

        Only a :class:`~jumpstarter_driver_hmi.proc.writer.CommandWriter`'s thread should call this: it is the one
        call here that can block. It gives way when stdin is closed (by :meth:`stop`, or because the child ended).

        Raises:
            BrokenPipeError: If stdin is closed or the child no longer reads it.
        """
        view = memoryview(data)
        while view:
            with self._stdin_lock:  # held for the instant of one non-blocking write, never while waiting
                stdin = self._stdin
                if stdin is None:
                    raise BrokenPipeError(errno.EPIPE, f"{self.name}: stdin is closed")
                fd = stdin.fileno()
                try:
                    written = os.write(fd, view)
                except BlockingIOError:
                    written = 0
            view = view[written:]
            if view:
                self._wait_writable(fd)

    @staticmethod
    def _wait_writable(fd: int) -> None:
        try:
            if hasattr(select, "poll"):
                poller = select.poll()
                poller.register(fd, select.POLLOUT)
                poller.poll(WRITE_POLL_S * 1000)
            else:
                select.select([], [fd], [], WRITE_POLL_S)
        except (OSError, ValueError):
            pass  # closed under us: the next turn finds stdin gone

    def _close_stdin(self) -> None:
        """Close the child's stdin. Never blocks: the lock is only ever held for an instant."""
        with self._stdin_lock:
            stdin, self._stdin = self._stdin, None
        if stdin is not None:
            with contextlib.suppress(OSError, ValueError):
                stdin.close()

    # -- signals -----------------------------------------------------------------------------------------------------

    def _signal_group(self, sig: int) -> bool:
        """Send ``sig`` to the child's whole group while the child is not reaped. ``False``: nothing was signaled."""
        proc, pgid = self._proc, self._pgid
        if proc is None or pgid is None or self._exited.is_set():
            return False
        try:
            if hasattr(os, "killpg"):
                os.killpg(pgid, sig)
            else:
                proc.send_signal(sig)
        except OSError as error:
            self._logger.debug("%s: signal %s: %s", self.name, sig, error)
            return False
        return True

    def _sweep_group(self) -> None:
        """Kill what the child left in its group. It must follow the reap at once: the id may be reused after that."""
        pgid = self._pgid
        if pgid is not None and hasattr(os, "killpg"):
            with contextlib.suppress(OSError):
                os.killpg(pgid, signal.SIGKILL)

    # -- stop --------------------------------------------------------------------------------------------------------

    def stop(
        self,
        deadline_s: float = 5.0,
        *,
        stop_command: bytes | None = None,
        send: Callable[[bytes], object] | None = None,
        flush: Callable[[float], object] | None = None,
        grace_s: float = DEFAULT_GRACE_S,
        term_s: float = DEFAULT_TERM_S,
    ) -> bool:
        """End the child within ``deadline_s`` seconds: politely, then SIGTERM, then SIGKILL. Never raises.

        Idempotent.

        The polite step passes ``stop_command`` to ``send`` (a non-blocking callback, normally a ``CommandWriter``'s
        ``send``: the command is queued with the others, in order). ``flush`` (normally the writer's ``close``) is then
        called with a time budget and must return once what was sent has been written, or when the budget is up:
        without it the stdin that is closed next may overtake the command (a child that ends at the end of its stdin
        does not need the command). Then stdin is closed and ``grace_s`` is waited. SIGTERM goes to the child's whole
        group, followed by ``term_s`` of waiting, and then SIGKILL. When ``grace_s`` and ``term_s`` do not fit in 80
        percent of the deadline they are scaled down, so that the kill always has its share.

        The lifecycle lock is taken with a short timeout. If another thread is in the middle of a stop, this call
        skips the polite step and starts with SIGTERM.

        Returns:
            ``True`` if the child is gone (and ``on_exit`` has returned, as far as the deadline allowed).
        """
        try:
            return self._stop(max(0.0, deadline_s), stop_command, send, flush, grace_s, term_s)
        except Exception:
            self._logger.exception("%s: stopping failed", self.name)
            return self._exited.is_set()

    def _stop(
        self,
        deadline_s: float,
        stop_command: bytes | None,
        send: Callable[[bytes], object] | None,
        flush: Callable[[float], object] | None,
        grace_s: float,
        term_s: float,
    ) -> bool:
        end = time.monotonic() + deadline_s
        self._stop_requested.set()
        budget = 0.8 * deadline_s
        if grace_s + term_s > budget:
            scale = budget / (grace_s + term_s)
            grace_s, term_s = grace_s * scale, term_s * scale
        locked = self._lifecycle.acquire(timeout=min(LOCK_WAIT_S, 0.1 * deadline_s))
        try:
            if self._waiter is None:
                return True  # never started, and now never will
            if locked:
                self._polite(stop_command, send, flush, min(grace_s, max(0.0, end - time.monotonic())))
            else:
                self._close_stdin()
            for sig, wait in ((signal.SIGTERM, term_s), (signal.SIGKILL, None)):
                if self._exited.is_set():
                    break
                self._signal_group(sig)
                limit = end if wait is None else min(end, time.monotonic() + wait)
                self._exited.wait(max(0.0, limit - time.monotonic()))
            if self._waiter is not threading.current_thread():
                self._done.wait(max(0.0, end - time.monotonic()))
            return self._exited.is_set()
        finally:
            if locked:
                self._lifecycle.release()

    def _polite(
        self,
        stop_command: bytes | None,
        send: Callable[[bytes], object] | None,
        flush: Callable[[float], object] | None,
        grace_s: float,
    ) -> None:
        if stop_command is not None and send is not None:
            try:
                send(stop_command)
                if flush is not None:
                    flush(min(FLUSH_S, grace_s))
            except Exception as error:  # noqa: BLE001 - a full queue refuses: the signals are next anyway
                self._logger.debug("%s: the stop command was not sent: %s", self.name, error)
        self._close_stdin()
        self._exited.wait(grace_s)
