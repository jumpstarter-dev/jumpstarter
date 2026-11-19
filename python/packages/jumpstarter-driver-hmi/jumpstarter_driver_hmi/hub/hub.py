"""One encode, any number of viewers, latency over completeness.

Pure code (no Jumpstarter, no sockets, no processes): the reader thread of the driver calls :meth:`Hub.publish_config`
and :meth:`Hub.publish_media`; each viewer drains its own :class:`Viewer` from its own thread. Everything here is
thread-safe because those are different threads. What a viewer is promised:

* **It starts at once.** The hub keeps the current group of pictures (the last IDR and every picture since, up to
  ``join_replay_max_bytes``); a joining viewer is queued with the codec line and that GOP, as a burst, **without asking
  the source for anything**. A GOP too large to replay makes the viewer wait for the next IDR instead, but only for
  ``join_wait_s``: an encoder counts its IDR interval in frames and sends few on a still screen, so that IDR can be many
  seconds away; after the wait the viewer is given the large GOP anyway (up to ``max_gop_bytes``).
* **It never queues behind a slow link.** Each viewer has its own queue. When its backlog grows past ``max_lag_s``
  (seconds the oldest picture has waited) or ``max_queue_bytes``, the pictures nothing depends on are dropped first
  (non-reference pictures: the encoder makes them when it is asked for temporal layers). If it is still far behind (2.5
  times the limit) with only reference pictures left, its queue is discarded and it is held until the next IDR (a
  "resync": a frozen picture, then a clean one), and the hub's ``on_congestion`` is told, which is what adaptive quality
  listens to. An IDR drops everything queued before it. No other viewer, and not the encode, is affected, and the hub
  holds no queue of its own beyond the GOP.
* **A viewer can ask for a clean start** (``resync``): it is served from the cached GOP, again without the source. Only
  a source with no GOP to give (none yet, or too large) is asked for a key picture (``on_need_idr``).
* **A viewer that has stopped reading is let go** (:data:`STALL_OVERFLOWS`): see "A stuck viewer" below.

**What a key picture costs decides how the hub gets one** (``keyframe_cost``, from the source's capabilities):

* ``"cheap"``: the source forces a key picture without restarting anything. The hub asks for one whenever a viewer needs
  a start it cannot give from the cache, through a :class:`~.keyframes.KeyframeGate` (coalesced, rate limited, forwarded
  at once for another viewer, retried by :meth:`Hub.poll`), and a joiner is replayed the cached GOP only while its IDR
  is at most ``join_replay_max_age_s`` old (an old one would make the joiner decode seconds of history first, when a
  fresh key picture is a request away).
* ``"restart"``: asking restarts the whole capture, for every viewer. Nothing asks except ``on_need_idr`` for a viewer
  whose start the cache cannot give; the hub relies on the source's periodic IDR, the byte gate and ``join_wait_s``, as
  above.
* ``"none"``: the source cannot be asked. The same as ``"restart"``, and ``on_need_idr`` is the driver's to leave unset.

**A replay is not a lag.** The pictures of the cached GOP carry the timestamps they were made with, so a consumer that
is handed them reads the age of the key picture as a delay. A consumer measures that out itself (a picture older than
one it has seen is a replay), and the hub does not count a report made within ``LAG_GRACE_S`` of a replay either
(:meth:`Viewer.prime`).

**The codec line.** The hub sends a viewer the line whenever it changes. Only a change of the codec string or of the
coded size (:meth:`~jumpstarter_driver_hmi.common.CodecLine.restarts_decoder`) means a new SPS: that drops the cached
GOP, forgets the parameter sets and holds every viewer until the key picture that carries the new ones. A change of
anything else (the rotation, the frame rate, the point scale, where input goes) is only a new line: the pictures go on,
the GOP stays, and no key picture is asked for.

**One codec family at a time** (``h264``, ``hevc`` or ``mjpeg``: the line's
:attr:`~jumpstarter_driver_hmi.common.CodecLine.family`). The hub learns it from the codec line and reads every unit as
that family: what a key picture is (an IDR slice for H.264, an IRAP picture for HEVC), which parameter sets it needs
(SPS and PPS; VPS too for HEVC) and which pictures nothing depends on (``nal_ref_idc`` 0 for H.264; the sub-layer
non-reference types for HEVC). A line of another family has another codec string, so it restarts the decoder like any
new SPS: the cached GOP and the parameter sets are dropped (the new family starts with none) and every viewer is held
until the key picture of the new stream, after it has received its line. Before the first line the family is H.264, the
interface's default.

**Motion JPEG** (``mjpeg``) is the stream where *every* unit is a key picture: one complete JPEG, no parameter sets, no
NAL units. What the rest of this text assumes of a key picture (that it is rare) turns into its opposite, and the hub
follows from that rather than special-casing much:

* The cached "GOP" is the latest picture. A joiner is given it at once, however large it is (``join_replay_max_bytes``
  is about a burst of many pictures, and this is one), as long as it is recent enough for a ``cheap`` source (the age
  gate applies as it does to any key picture); a viewer that resyncs is served the same way. Only a joiner that finds no
  picture yet waits, for the first one.
* A key picture drops what is queued before it, so an unread picture is replaced by the next and a viewer's queue never
  holds more than one: a slow viewer sees fewer pictures, each of them whole and the latest, and the hub holds no more
  for it. There is no resync and no non-reference picture to shed. As the queue cannot show how far behind the viewer
  is, the hub measures how long it has gone without reading since the oldest picture it has not read was queued: past
  ``max_lag_s`` that counts as an overflow for the adaptation, as a queue growing past it does.
* Such a source says ``keyframe_cost`` ``none``, so the hub is never to ask it for anything: a lost picture is no hole
  (:meth:`Hub.discontinuity` keeps the cache and holds nobody), and ``idrs`` and ``gop()`` count and show pictures.
* The hub reads a unit for what it is: it ignores the ``idr`` and ``reference`` a source says about a JPEG (it is both),
  and drops a unit that is not a JPEG, counting it (``invalid_units``).

**Parameter sets.** A key unit that came without its parameter sets (SPS and PPS; VPS, SPS and PPS for HEVC) is given
the last ones the hub has seen (:class:`~.codecs.ParameterSets`), so that every start a viewer is handed can be decoded.
A key unit nobody can decode (no parameter sets seen yet) is no start: it is counted (``keys_without_params``), not
cached, and another is asked for if the source can.

**A stuck viewer.** A viewer that reads nothing overflows its queue again and again, each time the next IDR gives it
something to fall behind on. The hub asks the source for a key picture on a viewer's behalf (``cheap`` sources) only if
the viewer has read something since its last overflow; a viewer that has not is owed one, which is asked for when it
reads again (:meth:`Hub.poll`). After :data:`STALL_OVERFLOWS` overflows in a row with nothing read in between the viewer
is closed as ``stalled`` (:attr:`Viewer.close_reason`) and removed, so that it costs the source no more requests.

**Time.** Timestamps are whatever the source says (monotonic microseconds: the driver adds one offset for the epoch, the
hub never does) and are only carried. The hub's own clock is injected (``clock=``, monotonic by default) and nothing in
it reads the wall clock.

A source's H.264 or HEVC usually has no B-frames, and without temporal layers every delta picture is a reference for the
next one: then the first stage has nothing to shed and a viewer that falls behind skips to the next IDR.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..common import DEFAULT_CODEC, KIND_CONFIG, KIND_MEDIA, CodecLine, pack_media
from .codecs import ParameterSets, UnitScan, scan_unit
from .keyframes import KeyframeGate
from .quality import PathSample

#: A viewer this many times past ``max_lag_s`` (or ``max_queue_bytes``) with only reference pictures left skips to the
#: next IDR.
HARD_FACTOR = 2.5
#: A viewer that has had to skip to the next IDR this many times within ``KEYFRAMES_WITHIN_S`` gets **key pictures
#: only** (its path cannot take the stream): the shared encode is not touched for it. It is tried on the whole stream
#: again after ``KEYFRAMES_RETRY_S``, which doubles (up to ``KEYFRAMES_RETRY_MAX_S``) each time the whole stream does
#: not hold.
KEYFRAMES_AFTER = 3
KEYFRAMES_WITHIN_S = 10.0
KEYFRAMES_RETRY_S = 8.0
KEYFRAMES_RETRY_MAX_S = 64.0
#: What a viewer remembers of its path, for the adaptation: its overflow events, and the peak of its queue each second,
#: this long.
PATH_MEMORY_S = 300.0
#: A consumer that reports it is this far behind real time (seconds) is not keeping up; this far, it skips to the next
#: key picture.
LAG_SOFT_S = 1.2
LAG_HARD_S = 3.0
#: A lag reported within this long (seconds) of a replay (:meth:`Viewer.prime`) is not counted: the replayed key picture
#: is old by definition, and a consumer that read it as a delay would say a lag the path does not have. (A consumer that
#: measures replays out itself never says it.)
LAG_GRACE_S = 3.5
#: How many of a viewer's last lag reports its stats keep.
RECENT_LAGS = 5
#: A viewer that skipped to the next key picture this many times in a row without reading anything in between is stuck:
#: it is closed as ``stalled``.
STALL_OVERFLOWS = 3

#: What a key picture costs the source, as the capabilities say.
KEYFRAME_COSTS = ("cheap", "restart", "none")


@dataclass(frozen=True, slots=True)
class Frame:
    """One transport frame, packed once for every viewer."""

    wire: bytes
    kind: int
    #: The timestamp the source gave the picture, in microseconds, untouched.
    ts_us: int = 0
    idr: bool = False
    #: A later picture may predict from it: dropping it breaks the decode until the next IDR.
    reference: bool = True

    @property
    def size(self) -> int:
        """Bytes it takes on the wire."""
        return len(self.wire)


class Viewer:
    """One consumer's queue. The hub pushes from the reader's thread; the consumer pops from its own."""

    def __init__(
        self,
        vid: int,
        *,
        max_lag_s: float,
        max_bytes: int,
        notify: Callable[[], None] | None = None,
        on_congestion: Callable[[Viewer], None] | None = None,
        on_skip: Callable[[Viewer, str, bool], None] | None = None,
        on_stalled: Callable[[Viewer], None] | None = None,
        internal: bool = False,
        all_key: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """A viewer with its own limits; ``notify`` is called (from any thread) when there is something to pop.

        ``all_key``: every picture of the stream is a key picture (Motion JPEG); see :attr:`all_key`.

        ``on_congestion(viewer)`` is told when its queue made it skip to the next IDR. ``on_skip(viewer, reason, ask)``
        is told of every skip (``reason``: ``overflow`` or ``lag``) and whether a key picture is to be asked for it (it
        has read something since its last skip). ``on_stalled(viewer)`` is told when it was closed as stuck. All three
        are called outside the viewer's lock.
        """
        self.id = vid
        #: a consumer inside the exporter (the recorder), not a path to a client: the adaptation leaves it out
        self.internal = internal
        #: every picture of the stream is a key picture (Motion JPEG): a new one replaces what the viewer has not read,
        #: so its queue never holds more than one picture and its lag is measured from when it last caught up
        self.all_key = all_key
        self._max_lag_s = max_lag_s
        self._max_bytes = max_bytes
        self._notify = notify
        self._on_congestion = on_congestion
        self._on_skip = on_skip
        self._on_stalled = on_stalled
        self._clock = clock
        self._lock = threading.Lock()
        # (frame, when it was queued; None: a replay, exempt from the lag limit)
        self._queue: deque[tuple[Frame, float | None]] = deque()
        self._bytes = 0
        self._closed = False
        #: why the viewer was closed (``stalled``, ``removed``, ``ended``), once it has been; the first reason stays
        self.close_reason: str | None = None
        #: nothing but config frames is queued until an IDR arrives
        self.waiting_idr = True
        #: when a joiner began to wait for an IDR because the cached GOP was too large (or too old) to replay (``None``:
        #: not waiting for that)
        self.join_wait_since: float | None = None
        #: the path's own account of itself (see :meth:`path`): when the queue overflowed, and the highest it got each
        #: second
        self._events: deque[float] = deque()
        self._peaks: deque[tuple[int, float]] = deque()
        self._resyncs: deque[float] = deque()
        #: key pictures only (the path cannot take the stream), until ``_keyframes_until``; ``_retry_s`` is the next
        #: wait
        self.keyframes_only = False
        self._keyframes_until = 0.0
        self._retry_s = KEYFRAMES_RETRY_S
        self._left_keyframes_at: float | None = None
        self._last_lag_skip: float | None = None
        #: when the viewer was last handed a replay of the cached GOP (``None``: never): its reports for ``LAG_GRACE_S``
        #: after are not counted
        self._replayed_at: float | None = None
        #: the last few reports (seconds, when, whether it was counted), newest last: what a viewer said about itself,
        #: for ``stats()``
        self._recent_lags: deque[tuple[float, float, bool]] = deque(maxlen=RECENT_LAGS)
        #: the consumer has popped something since the viewer last skipped to an IDR (progress), and how many skips in a
        #: row had none
        self._popped = False
        self._idle_skips = 0
        #: ``all_key`` only: when the oldest picture the consumer has not read was queued (``None``: it has caught up)
        self._behind_since: float | None = None
        #: a key picture was not asked for when the viewer skipped (it was not reading): it is owed one, once it reads
        #: again
        self.owes_key = False
        self.joined_at = clock()
        # counters, for status()
        self.sent = 0
        self.sent_bytes = 0
        self.dropped_non_reference = 0
        self.dropped_for_idr = 0
        self.dropped_in_resync = 0
        self.dropped_keyframes_only = 0
        self.resyncs = 0
        self.lag_ignored = 0

    # -- producer side ------------------------------------------------------------------------------------------

    def push(self, frame: Frame) -> None:
        """Queue a frame, applying the latency policy. Never blocks."""
        if frame.kind == KIND_CONFIG:
            self.push_config(frame)
            return
        skipped: tuple[bool, bool] | None = None
        with self._lock:
            if self._closed:
                return
            if self.keyframes_only:
                if not frame.idr:
                    self.dropped_keyframes_only += 1
                    return
                if self._clock() >= self._keyframes_until:  # time to try the whole stream again, from this key picture
                    self.keyframes_only = False
                    self._left_keyframes_at = self._clock()
            if frame.idr:
                self._replace_with_key()
            elif self.waiting_idr:
                self.dropped_in_resync += 1
                return
            self._append(frame, self._clock())
            if self._enforce():
                skipped = self._account_skip()
        self._wake()
        if skipped is not None:
            self._announce("overflow", *skipped)

    def _replace_with_key(self) -> None:
        """A key picture is queued next (lock held): a full picture, so whatever is queued before it is history."""
        if self.all_key:
            self._note_replaced()
        self.dropped_for_idr += self._drop_media()
        self.waiting_idr = False
        self.owes_key = False
        self.join_wait_since = None

    def push_config(self, frame: Frame, *, require_idr: bool = False) -> None:
        """Queue a codec line (tiny, and a decoder needs it before the picture it describes).

        ``require_idr``: the line starts a new decode (a new SPS), so nothing is sent from now on until the IDR that
        carries it.
        """
        with self._lock:
            if self._closed:
                return
            self._append(frame, None)
            if require_idr:
                self.waiting_idr = True
        self._wake()

    def prime(self, frames: Sequence[Frame]) -> None:
        """Queue a replay (a GOP, starting at its IDR) ahead of live frames. Exempt from the lag limit, and from the
        viewer's own reports of it."""
        with self._lock:
            if self._closed:
                return
            self._drop_media()
            self._behind_since = None
            for frame in frames:
                self._append(frame, None)
            self.waiting_idr = False
            self.owes_key = False
            self.join_wait_since = None
            self._replayed_at = self._clock()
        self._wake()

    def wait_for_idr(self, *, join: bool = False) -> None:
        """Hold this viewer until the next IDR: what it has queued would not decode on its own.

        ``join``: it is a joiner whose GOP was too large (or too old) to replay; the hub gives up waiting for it after
        ``join_wait_s``.
        """
        with self._lock:
            self.dropped_in_resync += self._drop_media()
            self._behind_since = None
            self.waiting_idr = True
            self.join_wait_since = self._clock() if join else None

    def close(self, reason: str | None = None) -> None:
        """End the viewer: what is queued can still be popped; nothing more is pushed. ``reason`` is kept in
        :attr:`close_reason` (the first stays)."""
        with self._lock:
            self._closed = True
            if self.close_reason is None:
                self.close_reason = reason
        self._wake()

    def settle_owed(self) -> bool | None:
        """For the hub's :meth:`Hub.poll`: ``None`` if the viewer is owed no key picture; ``False`` if it is, and still
        not reading; ``True`` if it is and has read since (the debt is cleared: the hub asks now)."""
        with self._lock:
            if not self.owes_key:
                return None
            if self._closed or not self.waiting_idr:
                self.owes_key = False  # an IDR came meanwhile, or it is gone
                return None
            if not self._popped:
                return False
            self.owes_key = False
            return True

    # -- consumer side ------------------------------------------------------------------------------------------

    @property
    def closed(self) -> bool:
        """Whether the viewer was closed (its queue may still hold frames to drain)."""
        return self._closed

    @property
    def wants_key(self) -> bool:
        """Whether the viewer is held for an IDR that nobody has to hold back asking for (it is not one that stopped
        reading)."""
        return self.waiting_idr and not self.owes_key and not self._closed

    def pop(self) -> Frame | None:
        """The next frame to send, or ``None`` if there is none now. A pop is the viewer's sign of life."""
        with self._lock:
            if not self._queue:
                return None
            frame, _ = self._queue.popleft()
            self._bytes -= frame.size
            self._popped = True
            if frame.kind == KIND_MEDIA:
                self.sent += 1
                self.sent_bytes += frame.size
                self._behind_since = None  # it has what it was behind for
            return frame

    def stats(self) -> dict:
        """What this viewer has been sent, and what it lost."""
        with self._lock:
            now = self._clock()
            return {
                "id": self.id,
                "age_s": round(now - self.joined_at, 1),
                "queued": len(self._queue),
                "queued_bytes": self._bytes,
                "waiting_for_idr": self.waiting_idr,
                "sent": self.sent,
                "sent_bytes": self.sent_bytes,
                "dropped_non_reference": self.dropped_non_reference,
                "dropped_for_idr": self.dropped_for_idr,
                "dropped_in_resync": self.dropped_in_resync,
                "dropped_keyframes_only": self.dropped_keyframes_only,
                "keyframes_only": self.keyframes_only,
                "resyncs": self.resyncs,
                "idle_skips": self._idle_skips,
                "owes_key": self.owes_key,
                "close_reason": self.close_reason,
                "lag_ignored": self.lag_ignored,
                "recent_lags": [
                    {"s": round(lag, 2), "age_s": round(now - at, 1), "counted": counted}
                    for lag, at, counted in self._recent_lags
                ],
            }

    # -- internals (lock held) --------------------------------------------------------------------------------

    def _wake(self) -> None:
        if self._notify is not None:
            self._notify()

    def _append(self, frame: Frame, queued_at: float | None) -> None:
        self._queue.append((frame, queued_at))
        self._bytes += frame.size

    def _drop_media(self, only_non_reference: bool = False) -> int:
        """Remove queued pictures (never config frames); how many."""
        kept: deque[tuple[Frame, float | None]] = deque()
        dropped = 0
        for item in self._queue:
            frame = item[0]
            if frame.kind == KIND_MEDIA and not (only_non_reference and frame.reference):
                self._bytes -= frame.size
                dropped += 1
            else:
                kept.append(item)
        self._queue = kept
        return dropped

    def _note_replaced(self) -> None:
        """A key picture is about to replace pictures nobody has read (a stream where every picture is one).

        The queue is never longer than one picture then, so it cannot show how far behind the viewer is: this does. The
        viewer is as far behind as it has gone without reading since the oldest picture it has not read was queued, and
        past ``max_lag_s`` that is an overflow, like the queue of any other viewer growing past it. A replay is exempt.
        """
        if self._behind_since is None:
            self._behind_since = next((at for f, at in self._queue if f.kind == KIND_MEDIA and at is not None), None)
        if self._behind_since is None:
            return
        waited = self._clock() - self._behind_since
        self._note_peak(waited / self._max_lag_s if self._max_lag_s else 0.0)
        if waited > self._max_lag_s:
            self._note_event()

    def _pressure(self) -> int:
        """0: keeping up. 1: behind (shed what nothing depends on). 2: far behind (skip to the next key picture)."""
        media = [(f, t) for f, t in self._queue if f.kind == KIND_MEDIA]
        if len(media) <= 1:
            return 0  # one picture is the latest one; there is nothing to skip to
        head, queued_at = media[0]
        backlog = sum(f.size for f, _ in media) - head.size
        waited = self._clock() - queued_at if queued_at is not None else 0.0
        self._note_peak(
            max(
                waited / self._max_lag_s if self._max_lag_s else 0.0,
                backlog / self._max_bytes if self._max_bytes else 0.0,
            )
        )
        if backlog > HARD_FACTOR * self._max_bytes or waited > HARD_FACTOR * self._max_lag_s:
            return 2
        if backlog > self._max_bytes or waited > self._max_lag_s:
            return 1
        return 0

    def _enforce(self) -> bool:
        """Apply the policy after a push; True when this viewer had to be resynchronised."""
        if self._pressure() == 0:
            return False
        self._note_event()
        self.dropped_non_reference += self._drop_media(only_non_reference=True)
        if self._pressure() < 2:
            return False
        # Far behind, and what is left is all reference pictures: skipping one breaks every picture after it, so skip
        # them all and hold the viewer until a key picture that it can start from.
        self.dropped_in_resync += self._drop_media()
        self._behind_since = None
        self.waiting_idr = True
        self.resyncs += 1
        self._note_resync()
        return True

    def _account_skip(self) -> tuple[bool, bool]:
        """The viewer skipped to the next key picture: ``(ask for one on its behalf, it is stuck and now closed)``.

        A key picture is asked for only when the viewer has popped something since it last skipped: one that has not is
        not reading, and a key picture would only give it something else to fall behind on. It is then owed one
        (:meth:`settle_owed`), and after ``STALL_OVERFLOWS`` skips in a row with no reading in between it is closed as
        ``stalled``.
        """
        progressed = self._popped
        self._popped = False
        self._idle_skips = 0 if progressed else self._idle_skips + 1
        if self._idle_skips >= STALL_OVERFLOWS:
            self._closed = True
            self.close_reason = self.close_reason or "stalled"
            self.owes_key = False
            return False, True
        self.owes_key = not progressed
        return progressed, False

    # -- what a skip tells the hub (lock not held) --------------------------------------------------------------

    def _announce(self, reason: str, ask: bool, stalled: bool) -> None:
        if stalled:
            self._wake()
            if self._on_stalled is not None:
                self._on_stalled(self)
            return
        if reason == "overflow" and self._on_congestion is not None:
            self._on_congestion(self)
        if self._on_skip is not None:
            self._on_skip(self, reason, ask)

    # -- the path's account of itself (lock held) --------------------------------------------------------------------

    def _note_event(self) -> None:
        """The queue overflowed (non-reference pictures were dropped, or a skip followed). At most one a half second: a
        burst is one event."""
        now = self._clock()
        if not self._events or now - self._events[-1] >= 0.5:
            self._events.append(now)
        while self._events and now - self._events[0] > PATH_MEMORY_S:
            self._events.popleft()

    def _note_peak(self, ratio: float) -> None:
        second = int(self._clock())
        if self._peaks and self._peaks[-1][0] == second:
            if ratio > self._peaks[-1][1]:
                self._peaks[-1] = (second, ratio)
        else:
            self._peaks.append((second, ratio))
        while self._peaks and second - self._peaks[0][0] > PATH_MEMORY_S:
            self._peaks.popleft()

    def _note_resync(self) -> None:
        """A skip to the next key picture; enough of them in a short time and the viewer is on key pictures only."""
        now = self._clock()
        self._resyncs.append(now)
        while self._resyncs and now - self._resyncs[0] > KEYFRAMES_WITHIN_S:
            self._resyncs.popleft()
        if len(self._resyncs) >= KEYFRAMES_AFTER:
            # if the whole stream was tried again not long ago and did not hold, wait longer next time
            if self._left_keyframes_at is not None and now - self._left_keyframes_at < 2 * self._retry_s:
                self._retry_s = min(self._retry_s * 2, KEYFRAMES_RETRY_MAX_S)
            self.keyframes_only = True
            self._keyframes_until = now + self._retry_s
            self._resyncs.clear()
            self.waiting_idr = True

    def report_lag(self, lag_s: float) -> bool:
        """The consumer's own account of how far behind real time it is, measured at the far end (what buffers between
        the hub and it hides from the queue here: a path that cannot take the stream shows as the consumer's lag growing
        long before this queue fills).

        Past ``LAG_SOFT_S`` it counts as an overflow (the viewer's path is not keeping up); past ``LAG_HARD_S`` the
        viewer also skips to the next key picture, as one whose queue overflowed does, at most once in ``LAG_HARD_S``
        (what is in flight cannot be taken back, and the lag of what arrives goes on reporting it for a while). Returns
        whether it skipped.

        A report within ``LAG_GRACE_S`` of a replay (:meth:`prime`) is not counted (``lag_ignored``): the key picture it
        replayed is old, and reads as a delay to a consumer that does not tell a replay from a lag.

        A report belongs to the viewer it is made on: the driver calls this from that viewer's own stream, so one
        viewer's lag never lands on another.
        """
        skipped: tuple[bool, bool] | None = None
        with self._lock:
            if self._closed:
                return False
            now = self._clock()
            if self._replayed_at is not None and now - self._replayed_at < LAG_GRACE_S:
                self.lag_ignored += 1
                self._recent_lags.append((lag_s, now, False))
                return False
            self._recent_lags.append((lag_s, now, True))
            self._note_peak(lag_s / LAG_SOFT_S)
            if lag_s > LAG_SOFT_S:
                self._note_event()
            # A viewer already held for a key picture has nothing to skip: the pictures still in flight read as a lag,
            # and
            # counting them as skips would close a viewer that is reading everything it is given.
            if (
                lag_s > LAG_HARD_S
                and not self.waiting_idr
                and (self._last_lag_skip is None or now - self._last_lag_skip >= LAG_HARD_S)
            ):
                self._last_lag_skip = now
                self.dropped_in_resync += self._drop_media()
                self._behind_since = None
                self.waiting_idr = True
                self.resyncs += 1
                self._note_resync()
                skipped = self._account_skip()
        if skipped is None:
            return False
        self._announce("lag", *skipped)
        return True

    def path(self, window_s: float) -> PathSample:
        """What the viewer's path did in the last ``window_s`` seconds: its overflow events, how full its queue got,
        whether it is on key pictures."""
        with self._lock:
            now = self._clock()
            events = sum(1 for t in self._events if now - t <= window_s)
            peak = max((p for sec, p in self._peaks if now - sec <= window_s), default=0.0)
            return PathSample(self.id, events, peak, self.keyframes_only)


#: How far back the measured rate remembers (``measure`` windows are shorter).
MEASURE_MEMORY_S = 60.0


class StreamEnded(RuntimeError):
    """The hub was closed (the stream ended): nothing attaches to it any more."""


class Hub:
    """The publishing side: the current codec line, the current GOP, and the viewers."""

    def __init__(
        self,
        *,
        max_lag_s: float = 0.6,
        max_queue_bytes: int = 4_000_000,
        max_gop_bytes: int = 16 * 1024 * 1024,
        join_replay_max_bytes: int = 3_000_000,
        join_replay_max_age_s: float = 1.5,
        join_wait_s: float = 1.0,
        keyframe_cost: str = "none",
        keyframe_min_interval_s: float = 2.0,
        on_need_idr: Callable[[], None] | None = None,
        on_congestion: Callable[[Viewer], None] | None = None,
        on_stalled: Callable[[Viewer], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """``on_need_idr`` asks the source for a key picture; it must not block.

        ``keyframe_cost`` is what asking costs (see the module documentation): ``join_replay_max_age_s`` and
        ``keyframe_min_interval_s`` apply only to ``"cheap"``. ``on_congestion`` is told (from the reader's thread)
        which viewer had to skip to the next IDR: what adaptive quality listens to. ``on_stalled`` is told (from the
        thread that found out) which viewer was closed as stuck and removed: the driver ends its stream.

        Raises:
            ValueError: If ``keyframe_cost`` is not one of ``cheap``, ``restart`` or ``none``.
        """
        if keyframe_cost not in KEYFRAME_COSTS:
            raise ValueError(f"keyframe_cost is one of {', '.join(KEYFRAME_COSTS)}: {keyframe_cost!r}")
        self._max_lag_s = max_lag_s
        self._max_bytes = max_queue_bytes
        self.max_gop_bytes = max_gop_bytes
        self.join_replay_max_bytes = join_replay_max_bytes
        self.join_replay_max_age_s = join_replay_max_age_s
        self.join_wait_s = join_wait_s
        self.keyframe_cost = keyframe_cost
        self._joiners_waiting = False
        self._on_need_idr = on_need_idr
        self.on_congestion = on_congestion
        self.on_stalled = on_stalled
        self._clock = clock
        self._lock = threading.Lock()
        self._viewers: dict[int, Viewer] = {}
        self._closed = False
        self._next_id = 0
        self._line: CodecLine | None = None
        self._family = DEFAULT_CODEC
        self._params = ParameterSets(self._family)
        self._gop: list[Frame] | None = None
        self._gop_bytes = 0
        self._await_idr = True
        self._last_idr_at: float | None = None
        #: when the last access unit came (the hub's clock): what a watchdog measures silence from
        self.last_unit_at: float | None = None
        self._recent: deque[tuple[float, int]] = deque()
        self._gate = KeyframeGate(keyframe_min_interval_s)
        #: some viewer was not asked for a key picture because it was not reading, and is owed one
        self._owing = False
        self.frames = 0
        self.bytes = 0
        self.idrs = 0
        self.joins_replayed = 0
        self.joins_waited = 0
        self.joins_forced = 0
        self.keyframe_requests = 0
        self.keyframes_withheld = 0
        self.last_keyframe_reason: str | None = None
        self.keys_without_params = 0
        self.keys_repaired = 0
        self.invalid_units = 0
        self.discontinuities = 0
        self.resets = 0
        self.stalled = 0

    # -- source side --------------------------------------------------------------------------------------------

    def publish_config(self, line: CodecLine) -> bool:
        """Say what the stream is now; returns whether the viewers were sent a new line.

        An equal line is nothing: it is not sent again. Any difference is sent to every viewer (each gets its own id in
        it, ``viewer``, by which a client reports how far behind it is: :meth:`Viewer.report_lag`). What the decoder
        needs is another matter: only a new codec string or coded size means a new SPS
        (:meth:`~jumpstarter_driver_hmi.common.CodecLine.restarts_decoder`). Then the cached GOP and the parameter
        sets are dropped and every viewer waits for the key picture that carries the new ones. A change of anything else
        about showing the picture (the rotation, the frame rate, the point scale, where input goes) leaves the decode
        alone: the pictures go on and nothing is asked of the source. The source says the line before the first access
        unit that belongs to it.

        The line also says the codec family (``h264``, ``hevc`` or ``mjpeg``) that every unit after it is read as. A
        line of another family has another codec string, so it is a new decode like any other; and what the hub kept of
        the old family (its parameter sets are another kind of unit) is replaced by an empty cache of the new one.
        """
        with self._lock:
            if self._closed or line == self._line:
                return False
            decoding = line.restarts_decoder(self._line)
            self._line = line
            self._use_family(line.family)
            if decoding:
                self._gop = None  # a picture of another size is not a start for a viewer of this one
                self._gop_bytes = 0
                self._await_idr = True
                self._params.reset()
            viewers = list(self._viewers.values())
        for viewer in viewers:
            viewer.push_config(self._frame_for(line, viewer), require_idr=decoding)
        return True

    @staticmethod
    def _frame_for(line: CodecLine, viewer: Viewer) -> Frame:
        return Frame(wire=line.frame(viewer.id), kind=KIND_CONFIG)

    def _use_family(self, family: str) -> None:
        """Read the units from now on as ``family`` (lock held): its parameter sets, and whether every unit is a key
        picture. The same family changes nothing."""
        if family == self._family:
            return
        self._family = family
        self._params = ParameterSets(family)
        for viewer in self._viewers.values():
            viewer.all_key = family == "mjpeg"

    def publish_media(
        self, ts_us: int, access_unit: bytes, idr: bool | None = None, reference: bool | None = None
    ) -> None:
        """One access unit for every viewer, stamped ``ts_us`` (the source's own monotonic microseconds, carried as they
        are).

        ``idr`` and ``reference`` are found from the NAL units (of the codec family the line named) when left out; a
        source that knows says so. A key unit that lacks any of its parameter sets (SPS and PPS; VPS too for HEVC) is
        given the last ones seen. The sets any unit carries are remembered. A Motion JPEG unit is both a key picture and
        a reference by what it is, whatever is said; one that is not a JPEG is dropped and counted (``invalid_units``).
        """
        family = self._family  # the one reader thread says the line before the first unit that belongs to it
        scan = scan_unit(access_unit, family)
        if family == "mjpeg":
            idr = reference = True
            if not scan.idr:
                with self._lock:
                    self.invalid_units += 1
                return
        else:
            idr = scan.idr if idr is None else idr
            reference = scan.reference if reference is None else reference
        wire = pack_media(ts_us, access_unit)
        with self._lock:
            if self._closed:
                return
            key, unit = self._start_of(access_unit, scan, idr)
            if unit is not access_unit:
                wire = pack_media(ts_us, unit)
            frame = Frame(wire=wire, kind=KIND_MEDIA, ts_us=ts_us, idr=key, reference=reference or idr)
            viewers = self._accept(frame, len(unit), key)
        if idr and not key:
            self._ask_if_cheap(
                "params"
            )  # a key picture nobody could decode: another, with its parameter sets, is wanted
        for viewer in viewers:
            viewer.push(frame)
        if self._joiners_waiting:
            self.release_waiting_joins()
        if self._poll_due():
            self.poll()

    def _start_of(self, access_unit: bytes, scan: UnitScan, idr: bool) -> tuple[bool, bytes]:
        """``(whether it is a start a decoder can use, the unit to send)`` (lock held). Remembers the parameter sets it
        carries."""
        self._params.observe(scan)
        if not idr:
            return False, access_unit
        repaired = self._params.repair(access_unit, scan)
        if repaired is None:
            self.keys_without_params += 1
            return False, access_unit
        if repaired is not access_unit:
            self.keys_repaired += 1
        return True, repaired

    def _accept(self, frame: Frame, size: int, key: bool) -> list[Viewer]:
        """Cache and count a unit; the viewers to push it to (none while the stream waits for its first IDR).

        Lock held.
        """
        if self._await_idr:
            if not key:
                return []  # the stream began (or changed) mid-GOP: nothing a decoder could use
            self._await_idr = False
        now = self._clock()
        self.frames += 1
        self.bytes += size
        self.last_unit_at = now
        self._recent.append((now, size))
        while self._recent and now - self._recent[0][0] > MEASURE_MEMORY_S:
            self._recent.popleft()
        if key:
            self.idrs += 1
            self._gate.key_came()
            self._last_idr_at = now
            self._gop, self._gop_bytes = [frame], frame.size
        elif self._gop is not None:
            self._gop.append(frame)
            self._gop_bytes += frame.size
            if self._gop_bytes > self.max_gop_bytes:
                self._gop = None  # too long a GOP to keep: joiners wait for the next IDR instead
        return list(self._viewers.values())

    def release_waiting_joins(self) -> int:
        """Serve the joiners that have waited ``join_wait_s`` for an IDR from the cached GOP after all, however large or
        old (up to ``max_gop_bytes``).

        An encoder sends few pictures on a still screen and its next IDR can be many seconds away (the interval counts
        frames, not seconds), so a joiner would otherwise wait for as long: a burst bigger than
        ``join_replay_max_bytes`` is a lesser cost than that. Called with every picture and from the driver's
        once-a-second watchdog (a screen that sends nothing at all). Returns how many.
        """
        with self._lock:
            if not self._joiners_waiting:
                return 0
            now = self._clock()
            waiting = [v for v in self._viewers.values() if v.join_wait_since is not None]
            self._joiners_waiting = bool(waiting)
            gop = self._gop
            if not gop:
                return 0
            due = [v for v in waiting if v.join_wait_since is not None and now - v.join_wait_since >= self.join_wait_s]
            for viewer in due:
                viewer.prime(list(gop))
                self.joins_forced += 1
            self._joiners_waiting = len(due) < len(waiting)
            return len(due)

    def discontinuity(self) -> None:
        """Pictures were lost: no viewer may be given another until an IDR, and the cached GOP, which has a hole in it,
        is dropped.

        A ``cheap`` source is asked for the key picture they wait for. Pictures lost from a stream of key pictures
        (Motion JPEG) leave no hole in what follows, so it is only counted: nothing is dropped and nobody waits.
        """
        with self._lock:
            self.discontinuities += 1
            if self._family == "mjpeg":
                return
            self._gop = None
            self._gop_bytes = 0
            self._await_idr = True
            viewers = list(self._viewers.values())
        for viewer in viewers:
            viewer.wait_for_idr()
        if viewers:
            self._ask_if_cheap("discontinuity")

    def set_source(self, on_need_idr: Callable[[], None] | None, keyframe_cost: str | None = None) -> None:
        """Say where requests for a key picture go (a new source, after :meth:`reset_source`), and what they cost if
        that changed."""
        if keyframe_cost is not None and keyframe_cost not in KEYFRAME_COSTS:
            raise ValueError(f"keyframe_cost is one of {', '.join(KEYFRAME_COSTS)}: {keyframe_cost!r}")
        self._on_need_idr = on_need_idr
        if keyframe_cost is not None:
            self.keyframe_cost = keyframe_cost

    def reset_source(self) -> None:
        """The source restarted (a new session feeds the same viewers): what it said, what was cached of it and what had
        been asked of it are not known again until the new one says so. Every viewer stays, waits for the start of the
        new stream and is sent its codec line when it comes."""
        with self._lock:
            self._line = None
            self._family = DEFAULT_CODEC
            self._params = ParameterSets(self._family)
            for viewer in self._viewers.values():
                viewer.all_key = False
            self._gop = None
            self._gop_bytes = 0
            self._await_idr = True
            self._last_idr_at = self.last_unit_at = None
            self._gate.reset()
            self._owing = False
            self._joiners_waiting = False
            self.resets += 1
            viewers = list(self._viewers.values())
        for viewer in viewers:
            viewer.wait_for_idr()

    def close(self) -> None:
        """The source has ended: every viewer is closed (its queue can still be drained) and no new one is taken."""
        with self._lock:
            self._closed = True
            viewers = list(self._viewers.values())
            self._viewers.clear()
            self._gate.reset()
        for viewer in viewers:
            viewer.close("ended")

    # -- viewer side --------------------------------------------------------------------------------------------

    @property
    def line(self) -> CodecLine | None:
        """The current codec line, once the source has said it."""
        return self._line

    @property
    def closed(self) -> bool:
        """Whether the stream ended (see :meth:`close`)."""
        return self._closed

    @property
    def viewer_count(self) -> int:
        """Viewers that are attached."""
        with self._lock:
            return len(self._viewers)

    def viewers(self) -> list[Viewer]:
        """The attached viewers."""
        with self._lock:
            return list(self._viewers.values())

    def add_viewer(self, notify: Callable[[], None] | None = None, *, internal: bool = False) -> Viewer:
        """Attach a viewer: it gets the codec line (if known) and then the current GOP, or waits for the next IDR.

        Raises:
            StreamEnded: If the hub was closed: the caller finds the stream's current hub and tries again.
        """
        with self._lock:
            if self._closed:
                raise StreamEnded("the stream ended")
            self._next_id += 1
            viewer = Viewer(
                self._next_id,
                max_lag_s=self._max_lag_s,
                max_bytes=self._max_bytes,
                notify=notify,
                on_congestion=self._congested,
                on_skip=self._skipped,
                on_stalled=self._stalled,
                internal=internal,
                all_key=self._family == "mjpeg",
                clock=self._clock,
            )
            self._viewers[viewer.id] = viewer
            if self._line is not None:
                viewer.push(
                    self._frame_for(self._line, viewer)
                )  # inside the lock: a codec line published at the same moment must come after it
            ask = self._seed(viewer)
        if ask:
            self.request_keyframe("join", viewer.id)
        return viewer

    def remove_viewer(self, viewer: Viewer) -> None:
        """Detach a viewer (idempotent)."""
        with self._lock:
            self._viewers.pop(viewer.id, None)
        viewer.close("removed")

    def resync(self, viewer: Viewer) -> None:
        """A viewer's decoder lost the thread: give it a clean start again, from the GOP if there is one.

        A ``cheap`` source is asked for a fresh key picture as well (the cached start is what the decoder just choked
        on, perhaps); any other is asked only when the cache has no start to give.
        """
        with self._lock:
            ask = self._seed(viewer) if viewer.id in self._viewers else None
        if ask or (ask is not None and self.keyframe_cost == "cheap"):
            self.request_keyframe("resync", viewer.id)

    def report_lag(self, viewer_id: int, lag_s: float) -> bool:
        """A client's report of its own lag (see :meth:`Viewer.report_lag`); False for a viewer that is no longer here.

        The report is made on the viewer it names, so one viewer's lag never lands on another. A driver that has the
        viewer in hand (its own stream) calls :meth:`Viewer.report_lag` directly, which does the same.
        """
        with self._lock:
            viewer = self._viewers.get(int(viewer_id))
        if viewer is None:
            return False
        viewer.report_lag(float(lag_s))
        return True

    def path_samples(self, window_s: float) -> list[PathSample]:
        """Every viewer's path over the last ``window_s`` seconds (what the adaptation reads)."""
        return [viewer.path(window_s) for viewer in self.viewers() if not viewer.internal]

    # -- asking the source for a key picture --------------------------------------------------------------------

    def request_keyframe(self, reason: str = "", viewer: int | None = None) -> bool:
        """Ask the source for a key picture for ``viewer`` (``None``: for several, or nobody in particular). Returns
        whether a request was made now.

        A ``cheap`` source is asked through the :class:`~.keyframes.KeyframeGate`: not repeated inside
        ``keyframe_min_interval_s``, shared with a key picture already on its way, and forwarded at once for a viewer
        other than the last asked for. For any other source the caller has decided it is worth what it costs, and
        ``on_need_idr`` is called. ``reason`` (``join``, ``resync``, ``overflow``, ``lag``, ``params``,
        ``discontinuity``, ``stall``...) is kept for ``stats()``.
        """
        with self._lock:
            if self._closed:
                return False
            send = self._request_locked(reason, viewer)
        if send:
            self._emit()
        return send

    def _request_locked(self, reason: str, viewer: int | None) -> bool:
        self.keyframe_requests += 1
        self.last_keyframe_reason = reason
        now = self._clock()
        if self.keyframe_cost == "cheap":
            return self._gate.request(now, viewer)
        self._gate.send(now, viewer)
        return True

    def _ask_if_cheap(self, reason: str, viewer: int | None = None) -> None:
        if self.keyframe_cost == "cheap":
            self.request_keyframe(reason, viewer)

    def poll(self) -> bool:
        """Make the request that was held for its window to end, if the window is over and a viewer still needs a key
        picture; ask again when a request has gone unanswered through a whole window while a viewer waits for it; and
        ask for the viewer that was not reading when it skipped, now that it reads again. Only a ``cheap`` source is
        asked.

        Called with every unit while there is something to do, and from the driver's housekeeping (a stream that has
        gone quiet). Returns whether a request was made.
        """
        if self.keyframe_cost != "cheap":
            return False
        with self._lock:
            if self._closed:
                return False
            now = self._clock()
            viewers = list(self._viewers.values())
            send = self._settle_debts(viewers)
            waiting = any(viewer.wants_key for viewer in viewers)
            send = self._gate.poll(now, waiting) or send
        if send:
            self._emit()
        return send

    def _settle_debts(self, viewers: list[Viewer]) -> bool:
        """Ask for the viewers that were owed a key picture and have read since (lock held); whether a request is to be
        sent."""
        if not self._owing:
            return False
        send = False
        owing = False
        for viewer in viewers:
            state = viewer.settle_owed()
            if state is None:
                continue
            if state:
                send = self._request_locked("resumed", viewer.id) or send
            else:
                owing = True
        self._owing = owing
        return send

    def _poll_due(self) -> bool:
        return self.keyframe_cost == "cheap" and (self._gate.pending or self._gate.unanswered or self._owing)

    def _emit(self) -> None:
        if self._on_need_idr is not None:
            self._on_need_idr()

    # -- what is cached ---------------------------------------------------------------------------------------------

    def seconds_since_idr(self) -> float | None:
        """How long ago the last key picture went out, or ``None`` if none has."""
        with self._lock:
            return None if self._last_idr_at is None else self._clock() - self._last_idr_at

    def measure(self, window_s: float = 3.0) -> dict:
        """The rate over the last ``window_s`` seconds: ``{"fps", "kbit_s"}`` (0 when nothing was published)."""
        with self._lock:
            now = self._clock()
            while self._recent and now - self._recent[0][0] > window_s:
                self._recent.popleft()
            count = len(self._recent)
            total = sum(size for _, size in self._recent)
        return {"fps": round(count / window_s, 1), "kbit_s": round(total * 8 / window_s / 1000, 1)}

    def gop(self) -> dict:
        """What is cached for a joining viewer."""
        with self._lock:
            return {
                "frames": len(self._gop) if self._gop else 0,
                "bytes": self._gop_bytes if self._gop else 0,
                "age_s": round(self._clock() - self._last_idr_at, 2)
                if self._gop and self._last_idr_at is not None
                else None,
                "parameter_set_bytes": self._params.size,
            }

    def stats(self) -> dict:
        """The hub's counters and each viewer's."""
        age = self.seconds_since_idr()
        return {
            "family": self._family,
            "frames": self.frames,
            "bytes": self.bytes,
            "idrs": self.idrs,
            "last_idr_age_s": None if age is None else round(age, 2),
            **self.measure(),
            "gop": self.gop(),
            "joins": {"replayed": self.joins_replayed, "waited": self.joins_waited, "forced": self.joins_forced},
            "keyframe_cost": self.keyframe_cost,
            "keyframe_requests": {
                "requested": self.keyframe_requests,
                "withheld": self.keyframes_withheld,
                **self._gate.stats(),
            },
            "last_keyframe_reason": self.last_keyframe_reason,
            "discontinuities": self.discontinuities,
            "resets": self.resets,
            "keys_without_params": self.keys_without_params,
            "keys_repaired": self.keys_repaired,
            "invalid_units": self.invalid_units,
            "stalled": self.stalled,
            "viewers": [viewer.stats() for viewer in self.viewers()],
        }

    # -- internals ------------------------------------------------------------------------------------------------

    def _replayable(self, gop: list[Frame] | None) -> bool:
        """Whether the cached GOP is a start to hand a joiner now (lock held): small enough, and, for a ``cheap``
        source, recent enough.

        A burst bigger than ``join_replay_max_bytes`` costs more than waiting for a key picture; for a source that can
        be asked cheaply, so does a key picture so old that the joiner would decode seconds of history before it is up
        to date. The one picture of a Motion JPEG stream is no burst however large it is: waiting for the next one
        costs more.
        """
        if not gop or (self._gop_bytes > self.join_replay_max_bytes and self._family != "mjpeg"):
            return False
        if self.keyframe_cost != "cheap" or self._last_idr_at is None:
            return True
        return self._clock() - self._last_idr_at <= self.join_replay_max_age_s

    def _seed(self, viewer: Viewer) -> bool:
        """Give ``viewer`` a picture it can decode: the GOP so far, or a wait for the next IDR. Lock held.

        Returns whether the source should be asked for a key picture.
        """
        gop = self._gop
        if gop and self._replayable(gop):
            viewer.prime(list(gop))
            self.joins_replayed += 1
            return False
        viewer.wait_for_idr(join=bool(gop))
        self.joins_waited += 1
        if gop:
            self._joiners_waiting = True
        # at the very start there is no GOP yet and the first IDR is on its way; later, a GOP too large (or too old) to
        # replay is a reason to ask
        return self._last_idr_at is not None

    def _congested(self, viewer: Viewer) -> None:
        callback = self.on_congestion
        if callback is not None:
            callback(viewer)

    def _skipped(self, viewer: Viewer, reason: str, ask: bool) -> None:
        """A viewer skipped to the next IDR (its queue overflowed, or it said it was far behind). A ``cheap`` source is
        asked for a key picture if the viewer has read since it last skipped; otherwise the viewer is owed one."""
        if self.keyframe_cost != "cheap":
            return
        if ask:
            self.request_keyframe(reason, viewer.id)
            return
        with self._lock:
            self._owing = True
            self.keyframes_withheld += 1

    def _stalled(self, viewer: Viewer) -> None:
        """A viewer stopped reading and was closed: take it out, so it costs nothing more, and say so."""
        with self._lock:
            self._viewers.pop(viewer.id, None)
            self.stalled += 1
        callback = self.on_stalled
        if callback is not None:
            callback(viewer)


__all__ = ["Frame", "Hub", "StreamEnded", "Viewer"]
