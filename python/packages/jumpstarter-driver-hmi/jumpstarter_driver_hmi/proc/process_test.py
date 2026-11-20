"""ManagedProcess, Backoff and StderrTail, with tiny Python children as the fakes.

There is one that never reads stdin, one that ignores SIGTERM, one that exits at once and one that floods stderr. Every
wait is for a state, with a deadline."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from .process import Backoff, ManagedProcess, StderrTail, pdeathsig_preexec
from .writer import CommandWriter

WAIT_S = 15.0
PROCESS_LOGGER = "jumpstarter_driver_hmi.proc.process"

#: Says ``ready`` and then neither reads stdin nor does anything else.
NEVER_READS = """
    import time
    print("ready", flush=True)
    time.sleep(120)
"""
#: Ignores SIGTERM (only SIGKILL ends it) and does not read stdin either.
IGNORES_EVERYTHING = """
    import signal, time
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(120)
"""
#: Reads lines until ``stop`` (says so, exits 0) or the end of stdin (exits 0 without a word).
READS_UNTIL_STOP = """
    import sys
    print("ready", flush=True)
    for line in sys.stdin:
        if line.strip() == "stop":
            print("stopping", flush=True)
            sys.exit(0)
"""
#: Answers every ``ping`` line with ``pong``.
ECHO = """
    import sys
    print("ready", flush=True)
    for line in sys.stdin:
        if line.strip() == "ping":
            print("pong", flush=True)
"""
#: Starts a grandchild that holds its stdout and stdin, says its pid, and waits.
HAS_GRANDCHILD = """
    import subprocess, sys, time
    grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    print("grandchild", grandchild.pid, flush=True)
    time.sleep(120)
"""


def py(code: str) -> list[str]:
    return [sys.executable, "-u", "-c", textwrap.dedent(code)]


def wait_until(predicate, timeout_s: float = WAIT_S) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


def process_is_gone(pid: int) -> bool:
    """Gone, or only a zombie that nobody has reaped yet (where this process tree has no init to do it)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout
    return state.strip() == "" or state.strip().startswith("Z")


class Run:
    """A ManagedProcess and everything it said."""

    def __init__(self, code: str | list[str], **options) -> None:
        self.out = bytearray()
        self.cond = threading.Condition()
        self.exit_codes: list[int | None] = []
        self.out_at_exit: int | None = None
        self.exited = threading.Event()
        argv = code if isinstance(code, list) else py(code)
        self.proc = ManagedProcess(argv, name="child", on_stdout=self._stdout, on_exit=self._exit, **options)

    def _stdout(self, chunk: bytes) -> None:
        with self.cond:
            self.out += chunk
            self.cond.notify_all()

    def _exit(self, status: int | None) -> None:
        self.out_at_exit = len(self.out)
        self.exit_codes.append(status)
        self.exited.set()

    def said(self, marker: bytes, timeout_s: float = WAIT_S) -> bool:
        with self.cond:
            return self.cond.wait_for(lambda: marker in self.out, timeout_s)

    def start_ready(self) -> Run:
        self.proc.start()
        assert self.said(b"ready"), f"the child never said ready: {bytes(self.out)!r} {self.proc.stderr_tail}"
        return self

    def pid_said(self, word: bytes) -> int:
        assert self.said(word + b" ")
        assert wait_until(lambda: b"\n" in bytes(self.out).split(word + b" ")[1])
        return int(bytes(self.out).split(word + b" ")[1].split(b"\n")[0])


@pytest.fixture
def run():
    """``run(code, **options)`` is a Run; at the end whatever is left running is ended."""
    made: list[Run] = []

    def factory(code, **options) -> Run:
        made.append(Run(code, **options))
        return made[-1]

    yield factory
    for r in made:
        r.proc.stop(3.0, grace_s=0.0, term_s=0.0)


# -- Backoff ---------------------------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_backoff_the_first_failure_is_free_then_it_doubles_up_to_the_maximum():
    clock = FakeClock()
    backoff = Backoff(1.0, 10.0, clock=clock)
    delays = [backoff.record_exit(0.5, ok=False) for _ in range(7)]
    assert delays == [0.0, 1.0, 2.0, 4.0, 8.0, 10.0, 10.0]
    assert backoff.failures == 7


def test_backoff_says_how_long_to_wait_and_counts_down_with_the_clock():
    clock = FakeClock()
    backoff = Backoff(2.0, 30.0, clock=clock)
    assert backoff.wait_s() == 0.0
    backoff.record_exit(1.0, ok=False)
    assert backoff.wait_s() == 0.0  # one failure in a row: try again at once
    backoff.record_exit(1.0, ok=False)
    assert backoff.wait_s() == 2.0
    clock.now += 0.5
    assert backoff.wait_s() == pytest.approx(1.5)
    assert backoff.wait_s(clock.now + 1.5) == 0.0  # an explicit ``now``
    clock.now += 5.0
    assert backoff.wait_s() == 0.0


def test_backoff_a_run_that_was_stable_starts_the_count_again():
    clock = FakeClock()
    backoff = Backoff(1.0, 30.0, stable_s=10.0, clock=clock)
    for _ in range(4):
        backoff.record_exit(0.1, ok=False)
    assert backoff.failures == 4
    assert backoff.record_exit(10.0, ok=False) == 0.0  # it ran for a while: this is the first failure of a new run
    assert backoff.failures == 1
    assert backoff.wait_s() == 0.0
    assert backoff.record_exit(0.1, ok=False) == 1.0


def test_backoff_a_run_that_ended_as_intended_forgets_everything():
    clock = FakeClock()
    backoff = Backoff(1.0, 30.0, clock=clock)
    for _ in range(5):
        backoff.record_exit(0.1, ok=False)
    assert backoff.wait_s() > 0
    assert backoff.record_exit(0.1, ok=True) == 0.0
    assert (backoff.failures, backoff.wait_s()) == (0, 0.0)
    backoff.record_exit(0.1, ok=False)
    backoff.record_exit(0.1, ok=False)
    backoff.reset()
    assert (backoff.failures, backoff.wait_s()) == (0, 0.0)


def test_backoff_a_very_long_run_of_failures_does_not_overflow():
    backoff = Backoff(1.0, 30.0, clock=FakeClock())
    for _ in range(5000):
        delay = backoff.record_exit(0.0, ok=False)
    assert delay == 30.0


def test_backoff_refuses_nonsense():
    with pytest.raises(ValueError):
        Backoff(-1.0, 30.0)
    with pytest.raises(ValueError):
        Backoff(5.0, 1.0)


# -- StderrTail ------------------------------------------------------------------------------------------------------


def test_the_stderr_tail_keeps_the_last_lines_cut_short_whatever_the_chunking():
    tail = StderrTail("t", lines=3, line_bytes=10)
    data = b"one\ntwo\nthree\nfour\nfive-and-a-long-long-line\nsix"
    for i in range(0, len(data), 4):  # four bytes at a time
        tail.feed(data[i : i + 4])
    assert tail.lines == ["three", "four", "five-and-a"]  # six has no newline yet
    tail.finish()
    assert tail.lines == ["four", "five-and-a", "six"]


def test_the_stderr_tail_survives_bytes_that_are_not_text_and_empty_lines():
    tail = StderrTail("t")
    tail.feed(b"\xff\xfe broken\n\n\r\nfine\r\n")
    assert tail.lines == ["�� broken", "fine"]


def test_the_stderr_log_is_rationed_per_window_and_says_what_it_cut():
    clock = FakeClock()
    records: list[tuple[str, str]] = []

    class Capture(logging.Logger):
        def info(self, msg, *args, **kwargs):
            records.append(("info", msg % args))

        def warning(self, msg, *args, **kwargs):
            records.append(("warning", msg % args))

    tail = StderrTail("kid", lines=5, lines_per_window=3, window_s=10.0, clock=clock, log=Capture("capture"))
    tail.feed(b"".join(b"line %d\n" % i for i in range(10)))
    assert [r for r in records if r[0] == "info"] == [("info", f"kid: line {i}") for i in range(3)]
    assert tail.lines == [f"line {i}" for i in range(5, 10)]  # the tail has what was not logged
    assert tail.suppressed_total == 7
    clock.now += 10.0
    tail.feed(b"after the window\n")
    assert ("warning", "kid: its log was cut: 7 lines not shown") in records
    assert records[-1] == ("info", "kid: after the window")
    assert tail.suppressed_total == 7


# -- the process -----------------------------------------------------------------------------------------------------


def test_stdout_arrives_in_order_and_on_exit_follows_with_the_status(run):
    r = run("""
        import sys
        for i in range(2000):
            print("line", i)
        sys.exit(3)
    """)
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    expected = b"".join(b"line %d\n" % i for i in range(2000))
    assert bytes(r.out) == expected
    assert r.exit_codes == [3]
    assert r.out_at_exit == len(expected)  # on_exit came after the last byte of stdout
    assert r.proc.returncode == 3
    assert not r.proc.alive
    assert r.proc.stats()["stdout_bytes"] == len(expected)
    assert r.proc.last_stdout_at is not None


def test_a_child_that_exits_at_once_is_reaped_and_stop_is_a_no_op(run):
    r = run("pass")
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    assert (r.proc.returncode, r.exit_codes, r.proc.alive) == (0, [0], False)
    assert r.proc.stop(1.0) is True
    assert r.proc.stop(1.0) is True  # idempotent
    assert r.exit_codes == [0]


def test_a_child_ended_by_a_signal_reports_it_as_a_negative_status(run):
    r = run("""
        import os, signal
        os.kill(os.getpid(), signal.SIGKILL)
    """)
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    assert r.exit_codes == [-9]


def test_the_child_gets_the_environment_and_directory_it_was_given(run, tmp_path, monkeypatch):
    monkeypatch.setenv("PARENT_ONLY", "leaked")
    r = run(
        """
        import os
        print("var", os.environ.get("CHILD_VAR"), "parent", os.environ.get("PARENT_ONLY"), "cwd", os.getcwd())
    """,
        env={"CHILD_VAR": "given"},
        cwd=str(tmp_path),
    )
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    text = bytes(r.out).decode()
    assert "var given parent None" in text
    assert Path(text.split("cwd ")[1].strip()).resolve() == tmp_path.resolve()


def test_a_program_that_does_not_exist_is_a_start_error_not_a_hang(run):
    r = run(["/nonexistent/program/for/the/test"])
    with pytest.raises(RuntimeError, match="could not be started"):
        r.proc.start()
    assert not r.proc.alive
    assert r.proc.pid is None
    assert r.proc.stop(1.0) is True
    assert r.proc.wait(1.0) is True
    assert r.exit_codes == []  # a child that never started has no exit


def test_a_run_is_started_once_and_not_after_a_stop(run):
    r = run(NEVER_READS)
    r.start_ready()
    with pytest.raises(RuntimeError, match="already started"):
        r.proc.start()
    late = run(NEVER_READS)
    assert late.proc.stop(1.0) is True  # stopped before it ever started
    with pytest.raises(RuntimeError, match="stopped before it started"):
        late.proc.start()
    assert late.proc.pid is None


def test_a_callback_that_raises_does_not_end_the_reading_or_the_exit(run):
    seen: list[bytes] = []
    exits: list[int | None] = []

    def on_stdout(chunk: bytes) -> None:
        seen.append(chunk)
        raise RuntimeError("the callback is broken")

    def on_exit(status: int | None) -> None:
        exits.append(status)
        raise RuntimeError("so is this one")

    proc = ManagedProcess(
        py("""
            import sys, time
            for i in range(3):
                sys.stdout.write("x\\n")
                sys.stdout.flush()
                time.sleep(0.05)
        """),
        name="child",
        on_stdout=on_stdout,
        on_exit=on_exit,
    )
    proc.start()
    assert proc.wait(WAIT_S)
    assert b"".join(seen) == b"x\nx\nx\n"
    assert len(seen) >= 2  # more than one chunk, and every one reached the callback
    assert exits == [0]
    assert proc.stats()["callback_errors"] == len(seen)


def test_stderr_is_kept_bounded_and_logged_within_its_budget(run, caplog):
    r = run(
        """
        import sys
        for i in range(3000):
            sys.stderr.write(f"line {i}\\n")
        sys.stderr.write("x" * 5000 + "\\n")
        sys.stderr.write("last line without a newline")
    """,
        stderr_lines=20,
        stderr_line_bytes=1000,
        stderr_lines_per_window=50,
        log_window_s=1e6,
    )
    with caplog.at_level(logging.INFO, logger=PROCESS_LOGGER):
        r.proc.start()
        assert r.proc.wait(WAIT_S)
    tail = r.proc.stderr_tail
    assert len(tail) == 20
    assert tail[-1] == "last line without a newline"
    assert tail[-2] == "x" * 1000
    assert tail[-3] == "line 2999"
    assert all(len(line) <= 1000 for line in tail)
    logged = [m for m in caplog.messages if m.startswith("child: ")]
    assert len([m for m in logged if "not shown" not in m]) == 50
    assert r.proc.stats()["stderr_lines_not_logged"] == 3002 - 50
    assert any("2952 lines not shown" in m for m in logged)
    assert r.exit_codes == [0]  # a flood on stderr did not hold up the exit


def test_a_grandchild_that_holds_the_pipes_does_not_hang_the_exit_and_dies_with_the_group(run):
    r = run("""
        import subprocess, sys
        grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        print("grandchild", grandchild.pid, flush=True)
    """)
    r.proc.start()
    pid = r.pid_said(b"grandchild")
    # the child exits at once; the grandchild still holds stdout, so the pipe would stay open for two minutes
    started = time.monotonic()
    assert r.proc.wait(WAIT_S)
    assert time.monotonic() - started < 10.0
    assert wait_until(lambda: process_is_gone(pid))


# -- stopping --------------------------------------------------------------------------------------------------------


def test_stop_asks_politely_through_the_writer_and_the_child_leaves_by_itself(run):
    r = run(READS_UNTIL_STOP).start_ready()
    writer = CommandWriter(r.proc.stdin_write)
    started = time.monotonic()
    assert r.proc.stop(10.0, stop_command=b"stop\n", send=writer.send, flush=writer.close) is True
    assert time.monotonic() - started < 5.0
    assert r.proc.returncode == 0  # no signal was needed
    assert b"stopping" in bytes(r.out)  # the command was written before stdin was closed
    assert r.exited.is_set()  # stop returns after on_exit
    assert r.proc.stopping
    assert not writer.alive


def test_the_end_of_stdin_alone_is_a_polite_stop_for_a_child_that_reads_it(run):
    r = run(READS_UNTIL_STOP).start_ready()
    assert r.proc.stop(10.0) is True
    assert r.proc.returncode == 0
    assert b"stopping" not in bytes(r.out)


def test_a_child_that_does_not_read_is_ended_by_sigterm_after_the_grace_period(run):
    r = run(NEVER_READS).start_ready()
    started = time.monotonic()
    assert r.proc.stop(10.0, grace_s=0.3, term_s=5.0) is True
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 5.0
    assert r.proc.returncode == -15


def test_a_child_that_ignores_sigterm_is_killed_within_the_deadline(run):
    r = run(IGNORES_EVERYTHING).start_ready()
    started = time.monotonic()
    assert r.proc.stop(5.0, grace_s=0.3, term_s=0.3) is True
    assert time.monotonic() - started < 5.0
    assert r.proc.returncode == -9


def test_the_deadline_bounds_the_whole_stop_however_long_the_steps_are(run):
    r = run(IGNORES_EVERYTHING).start_ready()
    started = time.monotonic()
    # the defaults want 2 s + 2 s; the deadline allows 1 s, so the steps shrink and the kill still comes in time
    assert r.proc.stop(1.0) is True
    assert time.monotonic() - started < 1.0 + 3.0  # generous margin for a loaded machine
    assert r.proc.returncode == -9


def test_a_deadline_of_nothing_is_a_kill_that_is_not_waited_for(run):
    r = run(IGNORES_EVERYTHING).start_ready()
    started = time.monotonic()
    r.proc.stop(0.0)  # SIGKILL at once; whether it is reaped by now is not promised
    assert time.monotonic() - started < 3.0
    assert r.proc.wait(WAIT_S)
    assert r.proc.returncode == -9


def test_a_second_stop_does_not_wait_behind_one_that_is_in_its_polite_step(run):
    """The lifecycle lock is not held for as long as a stop waits.

    Here the first stop is waiting politely (30 s) for a child that does not read; the second, from the end of the
    lease, must not wait for it."""
    r = run(NEVER_READS).start_ready()
    results: dict[str, object] = {}

    def slow_stop() -> None:
        results["slow"] = r.proc.stop(30.0, grace_s=20.0, term_s=5.0)

    slow = threading.Thread(target=slow_stop, daemon=True)
    slow.start()
    assert wait_until(r.proc._lifecycle.locked)  # the first stop has the lock and is waiting politely
    started = time.monotonic()
    assert r.proc.stop(3.0) is True
    assert time.monotonic() - started < 3.0 + 1.0
    assert r.proc.returncode == -15  # the second stop's SIGTERM
    slow.join(WAIT_S)
    assert not slow.is_alive()
    assert results["slow"] is True


def test_the_whole_process_group_is_ended_with_the_child(run):
    r = run(HAS_GRANDCHILD)
    r.proc.start()
    pid = r.pid_said(b"grandchild")
    assert r.proc.stop(5.0, grace_s=0.2, term_s=0.2) is True
    assert wait_until(lambda: process_is_gone(pid))
    assert r.proc.wait(5.0)  # the readers saw the end of the pipes too: nothing holds them open


def test_a_stop_from_the_exit_callback_does_not_deadlock(run):
    r = run("pass")
    stopped: list[bool] = []
    r.proc._on_exit = lambda status: stopped.append(r.proc.stop(1.0))
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    assert stopped == [True]


def test_a_slow_exit_callback_does_not_stretch_a_stop_past_its_deadline(run):
    release = threading.Event()
    r = run(NEVER_READS)
    r.proc._on_exit = lambda status: release.wait(WAIT_S)
    r.start_ready()
    started = time.monotonic()
    assert r.proc.stop(1.0, grace_s=0.1, term_s=0.1) is True  # the child is gone, whatever the callback is doing
    assert time.monotonic() - started < 1.0 + 3.0
    assert not r.proc.wait(0.0)  # the callback is still running
    release.set()
    assert r.proc.wait(WAIT_S)


# -- stdin and the thread that starts the child ----------------------------------------------------------------------


def test_stdin_write_gets_a_large_write_through_to_a_child_that_reads_it(run):
    r = run("""
        import sys
        print("ready", flush=True)
        data = sys.stdin.buffer.read()
        print("got", len(data), flush=True)
    """).start_ready()
    r.proc.stdin_write(b"a" * 5_000_000)  # far more than a pipe holds: it goes through as the child reads
    r.proc._close_stdin()
    assert r.said(b"got 5000000")


def test_a_write_to_a_child_that_does_not_read_gives_way_when_stdin_is_closed(run):
    r = run(NEVER_READS).start_ready()
    outcome: list[BaseException | None] = []

    def write() -> None:
        try:
            r.proc.stdin_write(b"x" * 5_000_000)
            outcome.append(None)
        except BaseException as e:  # noqa: BLE001 - the test looks at what it was
            outcome.append(e)

    writer = threading.Thread(target=write, daemon=True)
    writer.start()
    assert not wait_until(lambda: outcome, 0.3)  # it is stuck: the child is not reading
    r.proc._close_stdin()
    writer.join(WAIT_S)
    assert not writer.is_alive()
    assert isinstance(outcome[0], BrokenPipeError)
    with pytest.raises(BrokenPipeError):
        r.proc.stdin_write(b"more")


def test_stdin_write_before_the_start_or_after_the_end_is_a_broken_pipe(run):
    r = run("pass")
    with pytest.raises(BrokenPipeError):
        r.proc.stdin_write(b"x")
    r.proc.start()
    assert r.proc.wait(WAIT_S)
    with pytest.raises(BrokenPipeError):
        r.proc.stdin_write(b"x")


def test_a_child_that_does_not_read_holds_up_neither_the_senders_nor_the_stop(run):
    """End to end: a writer before a child that never reads, a producer that never stops, a stop on a deadline."""
    r = run(NEVER_READS).start_ready()
    writer = CommandWriter(r.proc.stdin_write)
    outcomes = {"queued": 0, "refused": 0}

    def produce() -> None:
        for i in range(400):
            try:
                writer.send(b"y" * 65536 if i < 100 else b"touch down 1 1\n")  # the first 6 MB fill the pipe
                outcomes["queued"] += 1
            except Exception:  # noqa: BLE001 - what is refused is counted
                outcomes["refused"] += 1

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    producer.join(WAIT_S)
    assert not producer.is_alive(), "a send blocked on a child that does not read"
    assert outcomes["queued"] <= 256
    assert outcomes["refused"] >= 400 - 256
    started = time.monotonic()
    stopped = r.proc.stop(3.0, stop_command=b"stop\n", send=writer.send, flush=writer.close, grace_s=0.2, term_s=0.2)
    assert stopped is True
    assert time.monotonic() - started < 3.0 + 1.0
    assert r.proc.returncode == -15
    # the stuck write gave way once stdin was closed, and the writer thread ended with it (whether the write failure was
    # reported depends on whether the stop's flush had closed the writer by then: that is not what this is about)
    assert writer.close(WAIT_S) is True


def test_the_child_survives_the_thread_that_started_it(run):
    """PDEATHSIG follows the thread that creates the child: ``start`` may be called from a worker thread that ends."""
    r = run(ECHO)
    starter = threading.Thread(target=r.proc.start)
    starter.start()
    starter.join(WAIT_S)
    assert not starter.is_alive()
    assert r.said(b"ready")
    r.proc.stdin_write(b"ping\n")
    assert r.said(b"pong")
    assert r.proc.alive


def test_on_linux_the_child_dies_with_its_parent_and_says_so():
    if not sys.platform.startswith("linux"):
        assert pdeathsig_preexec() is None  # a no-op elsewhere
        pytest.skip("PR_SET_PDEATHSIG is Linux only")  # ty: ignore[too-many-positional-arguments]
    assert callable(pdeathsig_preexec())
    out = bytearray()
    done = threading.Event()
    proc = ManagedProcess(
        py("""
            import ctypes
            value = ctypes.c_int(-1)
            ctypes.CDLL(None).prctl(2, ctypes.byref(value))  # PR_GET_PDEATHSIG
            print("pdeathsig", value.value, flush=True)
        """),
        name="child",
        on_stdout=out.extend,
        on_exit=lambda status: done.set(),
    )
    proc.start()
    assert done.wait(WAIT_S)
    assert bytes(out).strip() == b"pdeathsig 9"
