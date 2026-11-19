"""CommandWriter: callers only enqueue, whatever the pipe does (the stdin write that never returns is the case)."""

from __future__ import annotations

import threading
import time

import pytest

from ..common import InputError
from .writer import COALESCE_KEYFRAME, COALESCE_MOVE, CommandWriter

WAIT_S = 10.0


def wait_until(predicate, timeout_s: float = WAIT_S) -> bool:
    """Poll for a state with a deadline (never sleep and hope)."""
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.002)
    return predicate()


class Pipe:
    """A pipe's write: it records every command it was handed (in order) and, while ``stuck``, does not return.

    That is a child that does not read its stdin."""

    def __init__(self, *, stuck: bool = False, fail_with: BaseException | None = None) -> None:
        self.started: list[bytes] = []
        self.finished: list[bytes] = []
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.fail_with = fail_with
        self.fail_on: int | None = None  # fail the n-th write (1-based) instead of returning
        if not stuck:
            self.gate.set()

    def release(self) -> None:
        self.gate.set()

    def __call__(self, data: bytes) -> None:
        self.started.append(data)
        self.entered.set()
        if not self.gate.wait(WAIT_S * 3):
            raise TimeoutError("the test never released the pipe")
        if self.fail_with is not None and (self.fail_on is None or len(self.started) == self.fail_on):
            raise self.fail_with
        self.finished.append(data)


@pytest.fixture
def make():
    """``make(pipe, **options)`` is a CommandWriter on ``pipe``; at the end every pipe is released and writer closed."""
    made: list[tuple[CommandWriter, Pipe]] = []

    def factory(pipe: Pipe | None = None, **options) -> tuple[CommandWriter, Pipe]:
        pipe = pipe or Pipe()
        writer = CommandWriter(pipe, name="test-writer", **options)
        made.append((writer, pipe))
        return writer, pipe

    yield factory
    for writer, pipe in made:
        pipe.release()
        writer.close(WAIT_S)


def test_commands_are_written_in_the_order_they_were_sent(make):
    writer, pipe = make()
    commands = [f"cmd {i}\n".encode() for i in range(200)]
    for command in commands:
        assert writer.send(command) is True
    assert writer.close(WAIT_S) is True
    assert pipe.finished == commands
    assert writer.stats()["sent"] == 200
    assert writer.stats()["queued"] == 0


def test_send_never_blocks_while_the_child_does_not_read(make):
    """The write never returns. A producer that keeps sending is never held up, whatever it sends."""
    writer, _ = make(Pipe(stuck=True))
    outcomes = {"queued": 0, "dropped": 0, "refused": 0}

    def produce() -> None:
        for i in range(5000):
            try:
                if writer.send(b"move %d\n" % i, coalesce=COALESCE_MOVE if i % 2 else None):
                    outcomes["queued"] += 1
                else:
                    outcomes["dropped"] += 1
            except InputError:
                outcomes["refused"] += 1

    producer = threading.Thread(target=produce, daemon=True)
    producer.start()
    producer.join(WAIT_S)
    assert not producer.is_alive(), "send blocked on a pipe that is not read"
    assert sum(outcomes.values()) == 5000
    stats = writer.stats()
    assert stats["queued"] <= 256
    assert stats["sent"] == 0  # the pipe never took anything
    assert stats["refused"] == outcomes["refused"]
    assert stats["dropped"] == outcomes["dropped"]


def test_the_257th_non_coalescible_command_is_refused_with_busy(make):
    writer, pipe = make(Pipe(stuck=True))
    for i in range(256):
        assert writer.send(b"key %d\n" % i) is True
    with pytest.raises(InputError, match="busy"):
        writer.send(b"key 256\n")
    stats = writer.stats()
    assert stats["queued"] == 256
    assert stats["refused"] == 1
    # whatever the writer thread has done meanwhile: the one it holds counts, so the 257th is refused either way
    pipe.release()
    assert wait_until(lambda: writer.stats()["sent"] == 256)
    assert pipe.finished == [b"key %d\n" % i for i in range(256)]


def test_a_command_being_written_counts_towards_the_depth(make):
    writer, pipe = make(Pipe(stuck=True), depth=4)
    writer.send(b"0\n")
    assert pipe.entered.wait(WAIT_S)  # the writer now holds it, and the queue itself is empty
    for i in range(1, 4):
        writer.send(b"%d\n" % i)
    with pytest.raises(InputError, match="busy"):
        writer.send(b"4\n")
    assert writer.stats()["queued"] == 4


def test_a_newer_move_replaces_a_queued_one(make):
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"first\n")
    assert pipe.entered.wait(WAIT_S)
    for i in range(1, 51):
        assert writer.send(b"move %d\n" % i, coalesce=COALESCE_MOVE) is True
    stats = writer.stats()
    assert stats["queued"] == 2  # the one being written and one move
    assert stats["coalesced"] == 49
    pipe.release()
    assert wait_until(lambda: writer.stats()["sent"] == 2)
    assert pipe.finished == [b"first\n", b"move 50\n"]


def test_a_replaced_move_takes_the_newest_slot_so_it_never_overtakes_a_later_command(make):
    """The rule: the surviving command sits where the NEWEST one was sent.

    What is delivered is always a subsequence of what was sent, in the order it was sent. (Replacing in place would
    deliver ``move 2`` before ``key``, though it was sent after it.)"""
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    writer.send(b"move 1\n", coalesce=COALESCE_MOVE)
    writer.send(b"key\n")
    writer.send(b"move 2\n", coalesce=COALESCE_MOVE)
    writer.send(b"key 2\n")
    writer.send(b"move 3\n", coalesce=COALESCE_MOVE)
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"held\n", b"key\n", b"key 2\n", b"move 3\n"]


def test_down_and_up_are_never_coalesced_away_because_the_caller_gives_them_no_key(make):
    """The intended use: ``coalesce`` is the caller's choice, and only a move gets the key."""
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"touch down 10 10\n")
    assert pipe.entered.wait(WAIT_S)
    for i in range(100):
        writer.send(b"touch move %d 10\n" % i, coalesce=COALESCE_MOVE)
    writer.send(b"touch up 99 10\n")
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"touch down 10 10\n", b"touch move 99 10\n", b"touch up 99 10\n"]


def test_a_key_per_gesture_keeps_the_last_move_of_each_gesture(make):
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    writer.send(b"touch down 1 1\n")
    writer.send(b"touch move 2 1\n", coalesce="move:1")
    writer.send(b"touch move 3 1\n", coalesce="move:1")
    writer.send(b"touch up 3 1\n")
    writer.send(b"touch down 5 5\n")
    writer.send(b"touch move 6 5\n", coalesce="move:2")
    writer.send(b"touch up 6 5\n")
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [
        b"held\n",
        b"touch down 1 1\n",
        b"touch move 3 1\n",
        b"touch up 3 1\n",
        b"touch down 5 5\n",
        b"touch move 6 5\n",
        b"touch up 6 5\n",
    ]


def test_one_key_for_every_move_also_replaces_the_last_move_of_an_earlier_gesture(make):
    """The cost of a shared key, as documented.

    The earlier gesture keeps its ``down`` and ``up`` (never coalesced) but loses the move that the newer one replaced.
    The order of what is delivered is still the order it was sent."""
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    writer.send(b"touch down 1 1\n")
    writer.send(b"touch move 3 1\n", coalesce=COALESCE_MOVE)
    writer.send(b"touch up 3 1\n")
    writer.send(b"touch down 5 5\n")
    writer.send(b"touch move 6 5\n", coalesce=COALESCE_MOVE)
    writer.send(b"touch up 6 5\n")
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [
        b"held\n",
        b"touch down 1 1\n",
        b"touch up 3 1\n",
        b"touch down 5 5\n",
        b"touch move 6 5\n",
        b"touch up 6 5\n",
    ]


def test_with_the_queue_full_a_move_is_dropped_and_an_up_is_refused(make):
    writer, _ = make(Pipe(stuck=True), depth=8)
    for i in range(8):
        writer.send(b"key %d\n" % i)
    assert writer.send(b"touch move 1 1\n", coalesce=COALESCE_MOVE) is False  # dropped, counted, no error
    with pytest.raises(InputError, match="busy"):
        writer.send(b"touch up 1 1\n")  # not droppable: the caller must answer with an error line
    stats = writer.stats()
    assert (stats["dropped"], stats["refused"], stats["queued"]) == (1, 1, 8)


def test_a_full_queue_still_takes_a_newer_command_for_a_key_it_holds(make):
    writer, pipe = make(Pipe(stuck=True), depth=4)
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    writer.send(b"move 1\n", coalesce=COALESCE_MOVE)
    writer.send(b"a\n")
    writer.send(b"b\n")  # 4 held: full
    assert writer.send(b"move 2\n", coalesce=COALESCE_MOVE) is True  # replaces, so the queue does not grow
    assert writer.send(b"kf\n", coalesce=COALESCE_KEYFRAME) is False  # a key that is not queued has no room
    assert writer.stats()["queued"] == 4
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"held\n", b"a\n", b"b\n", b"move 2\n"]


def test_key_picture_requests_are_merged(make):
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    for _ in range(10):
        writer.send(b"keyframe\n", coalesce=COALESCE_KEYFRAME)
    writer.send(b"move 1\n", coalesce=COALESCE_MOVE)
    writer.send(b"keyframe\n", coalesce=COALESCE_KEYFRAME)
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"held\n", b"move 1\n", b"keyframe\n"]
    assert writer.stats()["coalesced"] == 10


def test_a_key_is_independent_of_other_keys(make):
    writer, pipe = make(Pipe(stuck=True))
    writer.send(b"held\n")
    assert pipe.entered.wait(WAIT_S)
    writer.send(b"move\n", coalesce=COALESCE_MOVE)
    writer.send(b"keyframe\n", coalesce=COALESCE_KEYFRAME)
    assert writer.stats()["coalesced"] == 0
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"held\n", b"move\n", b"keyframe\n"]


def test_a_command_is_a_snapshot_of_its_bytes(make):
    writer, pipe = make(Pipe(stuck=True))
    data = bytearray(b"touch down 1 2\n")
    writer.send(b"held\n")
    writer.send(data)
    data[:] = b"something else"
    pipe.release()
    assert writer.close(WAIT_S) is True
    assert pipe.finished == [b"held\n", b"touch down 1 2\n"]


def test_a_command_must_be_bytes_and_the_depth_positive(make):
    writer, _ = make()
    with pytest.raises(TypeError):
        writer.send("not bytes")
    with pytest.raises(TypeError):
        writer.send(5)
    with pytest.raises(ValueError, match="depth"):
        CommandWriter(lambda data: None, depth=0)


def test_a_failed_write_ends_the_writer_reports_once_and_refuses_what_follows(make):
    failures: list[BaseException] = []
    boom = BrokenPipeError("the child is gone")
    pipe = Pipe(stuck=True, fail_with=boom)
    pipe.fail_on = 2
    writer, _ = make(pipe, on_failure=failures.append)
    writer.send(b"1\n")
    assert pipe.entered.wait(WAIT_S)
    for command in (b"2\n", b"3\n", b"4\n"):
        writer.send(command)
    pipe.release()
    assert wait_until(lambda: not writer.alive)
    assert failures == [boom]
    assert writer.error is boom
    with pytest.raises(InputError, match="the device stopped taking input"):
        writer.send(b"5\n")
    with pytest.raises(InputError, match="the device stopped taking input"):
        writer.send(b"move\n", coalesce=COALESCE_MOVE)
    stats = writer.stats()
    assert (stats["sent"], stats["failed"], stats["discarded"], stats["queued"]) == (1, 1, 2, 0)
    assert pipe.started == [b"1\n", b"2\n"]  # the writer wrote nothing after the failure
    assert writer.close(1.0) is True  # a failed writer has nothing to wait for
    assert failures == [boom]  # still once


def test_the_failure_callback_runs_outside_the_lock_and_may_use_the_writer(make):
    seen: dict[str, object] = {}
    done = threading.Event()

    def on_failure(error: BaseException) -> None:
        seen["thread"] = threading.current_thread().name
        seen["stats"] = writer.stats()
        try:
            writer.send(b"x\n")
        except InputError as e:
            seen["refused"] = str(e)
        seen["closed"] = writer.close(1.0)  # the callback runs on the writer thread itself: no waiting for itself
        done.set()

    writer, _ = make(Pipe(fail_with=OSError("gone")), on_failure=on_failure)
    writer.send(b"1\n")
    assert done.wait(WAIT_S)
    assert seen["thread"] == "test-writer"
    assert seen["refused"] == "the device stopped taking input"
    assert seen["closed"] is True
    assert isinstance(seen["stats"], dict)
    assert wait_until(lambda: not writer.alive)


def test_a_failure_callback_that_raises_does_not_matter(make):
    def on_failure(error: BaseException) -> None:
        raise RuntimeError("the callback is broken")

    writer, _ = make(Pipe(fail_with=OSError("gone")), on_failure=on_failure)
    writer.send(b"1\n")
    assert wait_until(lambda: not writer.alive)
    with pytest.raises(InputError):
        writer.send(b"2\n")


def test_close_lets_the_thread_drain_what_is_queued(make):
    writer, pipe = make(Pipe(stuck=True))
    for i in range(5):
        writer.send(b"%d\n" % i)
    assert pipe.entered.wait(WAIT_S)
    release = threading.Timer(0.1, pipe.release)
    release.start()
    assert writer.close(WAIT_S) is True  # waited for the release, then drained all five
    release.join()
    assert pipe.finished == [b"%d\n" % i for i in range(5)]
    assert not writer.alive


def test_after_close_sends_are_refused(make):
    writer, _ = make()
    assert writer.close(WAIT_S) is True
    with pytest.raises(InputError, match="closed"):
        writer.send(b"x\n")
    assert writer.close(WAIT_S) is True  # idempotent


def test_close_returns_within_its_deadline_when_the_write_is_stuck_and_abandons_the_thread(make):
    writer, pipe = make(Pipe(stuck=True))
    for i in range(4):
        writer.send(b"%d\n" % i)
    assert pipe.entered.wait(WAIT_S)
    started = time.monotonic()
    assert writer.close(0.3) is False
    elapsed = time.monotonic() - started
    assert 0.25 <= elapsed < 3.0
    stats = writer.stats()
    assert stats["discarded"] == 3  # what was queued behind the stuck one
    assert stats["queued"] == 1  # the stuck one: the thread still holds it
    with pytest.raises(InputError):
        writer.send(b"late\n")
    # the child is killed, the write returns: the abandoned thread ends without writing anything more
    pipe.release()
    assert wait_until(lambda: not writer.alive)
    assert pipe.started == [b"0\n"]


def test_an_abandoned_writer_does_not_report_the_failure_of_its_stuck_write(make):
    failures: list[BaseException] = []
    writer, pipe = make(Pipe(stuck=True, fail_with=BrokenPipeError("killed")), on_failure=failures.append)
    writer.send(b"1\n")
    assert pipe.entered.wait(WAIT_S)
    assert writer.close(0.1) is False
    pipe.release()  # the write fails now, as it does when the child is killed
    assert wait_until(lambda: not writer.alive)
    assert failures == []
    assert writer.stats()["failed"] == 1


def test_close_waits_no_longer_than_its_deadline_whatever_is_queued(make):
    writer, _ = make(Pipe(stuck=True))
    for _ in range(256):
        writer.send(b"x\n")
    started = time.monotonic()
    assert writer.close(0.0) is False
    assert time.monotonic() - started < 3.0


def test_several_threads_may_send_and_each_keeps_its_own_order(make):
    writer, pipe = make(depth=100_000)
    threads_n, per_thread = 4, 300

    def produce(thread: int) -> None:
        for i in range(per_thread):
            writer.send(b"%d:%d\n" % (thread, i))
            writer.send(b"%d:move\n" % thread, coalesce=f"move{thread}")

    threads = [threading.Thread(target=produce, args=(t,), daemon=True) for t in range(threads_n)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_S)
        assert not thread.is_alive()
    assert writer.close(WAIT_S) is True
    for t in range(threads_n):
        mine = [c for c in pipe.finished if c.startswith(b"%d:" % t) and not c.endswith(b"move\n")]
        assert mine == [b"%d:%d\n" % (t, i) for i in range(per_thread)]
    stats = writer.stats()
    assert stats["sent"] + stats["coalesced"] == threads_n * per_thread * 2
