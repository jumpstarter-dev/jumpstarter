"""A bounded command queue in front of a pipe that may stop being read.

A child process that stops reading its stdin makes every ``write`` to it block. If the thread that wants to send a
command is the one that reads the child's output, a touch handler, or the one that ends the child, the whole stream
freezes behind that one write. :class:`CommandWriter` takes the write out of those threads: callers only **enqueue**
(:meth:`CommandWriter.send` never blocks and never does I/O), and one writer thread drains the queue into the pipe.

**Queue rules**

* The queue holds at most ``depth`` commands, counting the one being written. Order is preserved.
* A command may carry a *coalesce key*. At most one command per key is queued: a newer command with the same key
  **replaces** the queued one and takes **the newest slot** (the end of the queue), so the commands that were sent in
  between are never reordered behind it: what is delivered is always a subsequence of what was sent, in the order it
  was sent. (Replacing in place would deliver a newer move ahead of a key sent before it.)
* The key is the caller's promise that the newest command makes the queued ones with the same key redundant. A command
  that must not be lost (a touch ``down`` or ``up``, a key press) gets no key. A touch move is redundant only within its
  gesture: with one key for all moves, a move queued before an ``up`` and ``down`` is replaced by one queued after
  them, which loses that last move of the earlier gesture (its ``up`` and ``down`` stay); a caller that cares keys the
  moves by gesture (``move:1``, ``move:2``).
* When the queue is full, a command with a key is **dropped** (``send`` returns ``False``) and any other is
  **refused** (``send`` raises :class:`~jumpstarter_driver_hmi.common.InputError`, which the caller answers with an
  error line).
* A failed write ends the writer: what is still queued is discarded, later sends are refused, and ``on_failure`` is
  called once.

The writer thread is a daemon and may be stuck inside ``write`` for as long as the child does not read: that is
expected. Whoever owns the child ends it (which makes the write fail) after :meth:`CommandWriter.close` gave up
waiting.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

from ..common import InputError

logger = logging.getLogger(__name__)

#: How many commands a writer holds by default (the one being written included).
DEFAULT_DEPTH = 256
#: Conventional coalesce keys: touch moves (only the newest position matters) and key-picture requests (one is enough).
COALESCE_MOVE = "move"
COALESCE_KEYFRAME = "keyframe"


class _Entry:
    __slots__ = ("data", "key")

    def __init__(self, data: bytes, key: str | None) -> None:
        self.data = data
        self.key = key


class CommandWriter:
    """One writer thread draining a bounded queue of commands into ``write``. See the module documentation.

    ``write`` takes the bytes of one command and returns when all of them are written; it runs on the writer thread
    only and may block for as long as it likes. It raises (any ``Exception``) when the pipe is gone.
    """

    def __init__(
        self,
        write: Callable[[bytes], None],
        *,
        depth: int = DEFAULT_DEPTH,
        name: str = "hmi-writer",
        on_failure: Callable[[BaseException], None] | None = None,
    ) -> None:
        """Start the writer thread.

        Args:
            write: Writes one command (all its bytes) to the pipe; blocks while the child does not read.
            depth: Most commands held at once, the one being written included.
            name: The thread's name.
            on_failure: Called once, on the writer thread and outside every lock, with the exception when a write
                fails. It is not called for a failure of a writer that :meth:`close` has already given up on.

        Raises:
            ValueError: If ``depth`` is less than 1.
        """
        if depth < 1:
            raise ValueError("depth must be at least 1")
        self._write = write
        self._depth = depth
        self._on_failure = on_failure
        self._cond = threading.Condition()
        self._queue: deque[_Entry] = deque()
        #: coalesce key -> the queued entry that has it (an entry being written is no longer in here)
        self._pending: dict[str, _Entry] = {}
        self._inflight = False
        self._closing = False
        self._abandoned = False
        self._failure: BaseException | None = None
        self._sent = self._coalesced = self._dropped = self._refused = self._failed = self._discarded = 0
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    # -- the callers' side: never blocks -----------------------------------------------------------------------------

    def send(self, data: bytes, *, coalesce: str | None = None) -> bool:
        """Queue one command. Never blocks and does no I/O.

        Args:
            data: The command's bytes, exactly as they are to be written.
            coalesce: A key: a queued command with the same key is replaced by this one, which takes the newest slot.

        Returns:
            ``True`` if the command is queued (or replaced a queued one); ``False`` if it was dropped because the
            queue is full (only a command with a ``coalesce`` key is ever dropped).

        Raises:
            InputError: If the writer is closed or failed, or the queue is full and the command has no ``coalesce``
                key (it is refused: answer the sender with an error).
            TypeError: If ``data`` is not bytes.
        """
        if not isinstance(data, bytes | bytearray | memoryview):
            raise TypeError(f"a command is bytes, not {type(data).__name__}")
        data = bytes(data)
        with self._cond:
            if self._failure is not None:
                self._refused += 1
                raise InputError("the device stopped taking input")
            if self._closing:
                self._refused += 1
                raise InputError("the input is closed")
            if coalesce is not None and (old := self._pending.get(coalesce)) is not None:
                self._queue.remove(old)
                entry = _Entry(data, coalesce)
                self._queue.append(entry)  # the newest slot: nothing sent after the old one is overtaken
                self._pending[coalesce] = entry
                self._coalesced += 1
                return True
            if len(self._queue) + self._inflight >= self._depth:
                if coalesce is not None:
                    self._dropped += 1
                    return False
                self._refused += 1
                raise InputError("the device is busy: its input queue is full")
            entry = _Entry(data, coalesce)
            self._queue.append(entry)
            if coalesce is not None:
                self._pending[coalesce] = entry
            self._cond.notify()
            return True

    def stats(self) -> dict[str, int]:
        """What happened so far.

        ``queued`` is what is waiting to be written (the command being written included); ``sent`` what was written;
        ``coalesced`` what a newer command replaced; ``dropped`` what was lost to a full queue (commands with a key
        only); ``refused`` what ``send`` raised for; ``failed`` the writes that failed (0 or 1); ``discarded`` what
        was still queued when the writer failed or was given up on.
        """
        with self._cond:
            return {
                "queued": len(self._queue) + self._inflight,
                "sent": self._sent,
                "coalesced": self._coalesced,
                "dropped": self._dropped,
                "refused": self._refused,
                "failed": self._failed,
                "discarded": self._discarded,
            }

    @property
    def error(self) -> BaseException | None:
        """The exception that ended the writer, once a write has failed."""
        with self._cond:
            return self._failure

    @property
    def alive(self) -> bool:
        """Whether the writer thread is still running (it ends when it failed, or has drained a closed queue)."""
        return not self._done.is_set()

    def close(self, deadline_s: float) -> bool:
        """Stop accepting commands and give the thread ``deadline_s`` seconds to write what is queued.

        It never blocks longer than that.

        If the thread is not done by then (it is stuck in ``write``), it is **abandoned**: the queue is discarded,
        nothing more is written when ``write`` returns, and a failure is no longer reported to ``on_failure``. The
        caller then ends the child, which makes the stuck write fail. Idempotent.

        Returns:
            ``True`` if the thread has ended (drained, or failed earlier); ``False`` if it had to be abandoned.
        """
        with self._cond:
            self._closing = True
            self._cond.notify_all()
        if threading.current_thread() is self._thread:
            return True  # called from on_failure: the thread ends as soon as the callback returns
        if self._done.wait(max(0.0, deadline_s)):
            return True
        with self._cond:
            self._abandoned = True
            self._discarded += len(self._queue)
            self._queue.clear()
            self._pending.clear()
            self._cond.notify_all()
        return self._done.is_set()

    # -- the writer thread -------------------------------------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._drain()
        finally:
            with self._cond:
                if self._failure is None and not self._closing:
                    # not a failed write that ended it: refuse everything all the same
                    self._failure = RuntimeError("the writer thread ended")
            self._done.set()

    def _drain(self) -> None:
        while True:
            with self._cond:
                while not self._queue and not self._closing:
                    self._cond.wait()
                if self._abandoned or not self._queue:
                    return
                entry = self._queue.popleft()
                if entry.key is not None and self._pending.get(entry.key) is entry:
                    del self._pending[entry.key]
                self._inflight = True
            try:
                self._write(entry.data)
            except Exception as error:  # noqa: BLE001 - the pipe is gone (or ``write`` is broken): the writer ends
                self._fail(error)
                return
            with self._cond:
                self._inflight = False
                self._sent += 1

    def _fail(self, error: BaseException) -> None:
        with self._cond:
            self._failure = error
            self._failed += 1
            self._inflight = False
            self._discarded += len(self._queue)
            self._queue.clear()
            self._pending.clear()
            report = not self._abandoned
        if report and self._on_failure is not None:
            try:
                self._on_failure(error)
            except Exception:
                logger.exception("the writer's failure callback raised")
