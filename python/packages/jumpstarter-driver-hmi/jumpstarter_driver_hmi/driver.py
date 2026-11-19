"""``HmiDriver``: the HMI interface on a shared hub, for any
:class:`~jumpstarter_driver_hmi.source.HmiSource`.

A device driver builds a screen as a child (``hmi:`` option) or exports one on its own by subclassing
:class:`HmiDriver` and
returning its source from :meth:`HmiDriver.make_source`. Everything the viewers see is made here, on the exporter:

``video()``
    Frames in the wire format of :mod:`jumpstarter_driver_hmi.common`: the codec line, then timestamped Annex-B
    access units, SPS/PPS
    in front of every IDR. A joining viewer starts at once from the cached group of pictures, or at the next key
    picture. The client may
    write ``{"resync": true}`` (a clean start again) and ``{"lag": seconds}`` (how far behind it is) lines back.
``input()``
    A stream of JSON lines (touch, key, text) in, and ``{"error": ...}`` lines out. Events reach the source in the order
    they were
    sent; the source's input sink never blocks, and the exporter never waits on an error line.
``start`` / ``stop`` / ``status`` / ``hmi_info`` / ``set_quality`` and the recording calls
    Plain calls. ``stop`` is bounded in time.

The capture starts on first use (a viewer, an input stream, ``start``) and ends when the lease ends (``close``), on
``stop``, or after
``idle_stop_s`` seconds with nobody watching.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import tempfile
import threading
import time
from abc import abstractmethod
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import Any

import anyio
from anyio import to_thread
from anyio.streams.stapled import StapledObjectStream

from .common import (
    Capabilities,
    CodecLine,
    HardKey,
    InputError,
    KeyEvent,
    LineBuffer,
    LineTooLongError,
    QualityCaps,
    TextEvent,
    TouchEvent,
    canonical_key,
    error_line,
    parse_input,
)
from .hub import LEVELS, Adapter, Hub, Rung, StreamEnded, Viewer, ladder, level_rung, top_rung
from .hub.codecs import has_idr, is_reference
from .interface import HmiInterface
from .recording import Recording
from .source import InputSink, HmiSource
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver, export, exportstream

#: A line a viewer writes back on ``video()`` is small.
MAX_BACK_CHANNEL_BYTES = 4096
#: A viewer whose stream takes no picture for this long has stopped reading: its pump gives up and the stream is closed
#: (the hub has
#: already closed the viewer as stalled by then). Without it a pump blocked in a send would keep the stream open until
#: the lease ends.
SEND_TIMEOUT_S = 15.0


class _Waker:
    """Wakes a coroutine from another thread without ever blocking that thread: the source's reader pushes frames, a
    viewer's pump sleeps
    until one arrives.

    A self-pipe: ``notify`` (any thread) writes a byte, ``wait`` (the event loop) sleeps until the pipe is readable and
    drains it. A
    wake-up cannot be lost, and the reader never waits for the event loop, so a slow viewer cannot slow it.
    """

    def __init__(self) -> None:
        self._read, self._write = os.pipe()
        os.set_blocking(self._read, False)
        os.set_blocking(self._write, False)
        self._lock = threading.Lock()
        self._closed = False

    def notify(self) -> None:
        """Wake the waiting coroutine. Safe from any thread, and after :meth:`close` (then it does nothing)."""
        with self._lock:
            if not self._closed:
                with contextlib.suppress(OSError):  # a full pipe is a pipe that is readable already
                    os.write(self._write, b"\x00")

    async def wait(self) -> None:
        """Sleep until :meth:`notify` has been called since the last time this returned."""
        while True:
            try:
                if os.read(self._read, 4096):
                    return
            except BlockingIOError:
                pass
            await anyio.wait_readable(self._read)

    def close(self) -> None:
        """Release the pipe (from the event loop's thread)."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            anyio.notify_closing(self._read)
            os.close(self._read)
            os.close(self._write)


def _pipe(buffer: int = 2) -> tuple[StapledObjectStream[bytes], StapledObjectStream[bytes]]:
    """Two ends of an in-memory byte pipe with a small buffer: the first goes to the framework, the second is ours.

    ``jumpstarter.streams.common.create_memory_stream`` buffers 32 chunks; here each chunk is a picture, and a slow link
    has to show up
    as a full pipe (so the pump stops and the viewer's own queue, which has the latency policy, takes over) rather than
    as half a second
    of pictures hidden in a buffer.
    """
    a_tx, a_rx = anyio.create_memory_object_stream[bytes](buffer)
    b_tx, b_rx = anyio.create_memory_object_stream[bytes](buffer)
    return StapledObjectStream(a_tx, b_rx), StapledObjectStream(b_tx, a_rx)


@asynccontextmanager
async def _background(*jobs: Callable[[], Any]):
    """Run ``jobs`` (coroutine functions) beside the body of the ``with`` and cancel them when it ends.

    ``async with anyio.create_task_group()`` around a ``yield`` would wrap whatever the framework throws into the stream
    at the end of a
    lease in an ExceptionGroup, and the framework logs that as an unexpected servicer error. This enters the group but
    does not hand it
    the body's exception.
    """
    group = anyio.create_task_group()
    await group.__aenter__()
    try:
        for job in jobs:
            group.start_soon(job)
        yield
    finally:
        group.cancel_scope.cancel()
        await group.__aexit__(None, None, None)


@dataclass(eq=False)
class _InputState:
    """One ``input`` stream: the finger it has down (lifted when it ends), and whether it has ended."""

    down: tuple[int, int] | None = None
    size: tuple[int, int] | None = None
    #: the capture the finger went down on
    session: _Session | None = None
    ended: bool = False


class _Session:
    """One run of a source: the sink it publishes into and what the watchdog needs to know about it."""

    def __init__(self, source: HmiSource, hub: Hub) -> None:
        self.source = source
        self.hub = hub
        self.started_at = time.monotonic()
        self.last_media_at = self.started_at
        self.ended = threading.Event()
        self.error: str | None = None
        self.stall_attempt = 0
        self.probing = False
        #: set once the driver has let this session go: what its source publishes after that is dropped
        self.stopped = False
        self._offset_us: int | None = None
        self._last_ts = 0

    def config(self, line: CodecLine) -> None:
        """The source's codec line (published only if it changed)."""
        if not self.stopped:
            self.hub.publish_config(line)

    def media(self, ts_us: int, access_unit: bytes, *, idr: bool | None = None, reference: bool | None = None) -> None:
        """One access unit. The source's timestamps run on its own monotonic clock; one constant offset puts them near
        the exporter's wall clock.
        """
        if self.stopped:
            return
        if self._offset_us is None:
            self._offset_us = time.time_ns() // 1000 - ts_us
        ts = max(ts_us + self._offset_us, self._last_ts)  # never backwards
        self._last_ts = ts
        self.last_media_at = time.monotonic()
        self.stall_attempt = 0
        key = has_idr(access_unit) if idr is None else idr
        self.hub.publish_media(ts, access_unit, key, is_reference(access_unit) if reference is None else reference)

    def discontinuity(self) -> None:
        """Pictures were lost."""
        if not self.stopped:
            self.hub.discontinuity()

    def end(self, error: str | None = None) -> None:
        """The capture ended by itself."""
        if error and not self.error:
            self.error = error
        self.ended.set()


class _SinkAdapter:
    """The ``HubSink`` a source holds: it forwards to its own session only, so a stale session's source cannot publish
    into a new one.
    """

    def __init__(self, session: _Session) -> None:
        self._session = session

    def config(self, line: CodecLine) -> None:
        """See ``HubSink.config``."""
        self._session.config(line)

    def media(self, ts_us: int, access_unit: bytes, *, idr: bool | None = None, reference: bool | None = None) -> None:
        """See ``HubSink.media``."""
        self._session.media(ts_us, access_unit, idr=idr, reference=reference)

    def discontinuity(self) -> None:
        """See ``HubSink.discontinuity``."""
        self._session.discontinuity()

    def ended(self, error: str | None = None) -> None:
        """See ``HubSink.ended``."""
        self._session.end(error)


@dataclass(kw_only=True)
class HmiDriver(HmiInterface, Driver):
    """The HMI interface on a shared hub. Subclass it and return a :class:`HmiSource` from :meth:`make_source`."""

    # -- what the stream may be ----------------------------------------------------------------------------------------
    #: Optional **ceilings**, to cap a bench on purpose: set none of them and the stream starts at the device's own
    #: size, at the bit rate that
    #: suits that picture (8 Mbit/s at 1920x1080, in proportion to the pixels) and 30 pictures a second, and then
    #: follows the network between
    #: the client and the exporter: down when the viewers cannot keep up, back up when they have room. ``max_size``:
    #: longest side in pixels;
    #: ``bit_rate``: bit/s; ``max_fps``: pictures a second. Only for a source whose quality can be adjusted.
    max_size: int | None = None
    bit_rate: int | None = None
    max_fps: int | None = None
    #: ``false``: a picture and nothing else; ``input`` is refused.
    control: bool = True
    #: The buttons a UI draws, in order, replacing the source's own default: key names, or mappings with ``key``,
    #: ``label``, ``presses``.
    hard_keys: list[Any] | None = None

    # -- lifecycle and limits ------------------------------------------------------------------------------------------
    #: Stop the capture after this many seconds with no viewer and no input stream (0: only on ``stop`` or the end of
    #: the lease).
    idle_stop_s: float = 60.0
    #: How long the source is given to connect.
    start_timeout_s: float = 25.0
    #: The longest ``stop`` (and the end of a lease) waits for the source to let go of the device.
    stop_deadline_s: float = 6.0
    #: A viewer more than this far behind loses its queued pictures and waits for a key picture.
    max_lag_s: float = 0.6
    max_queue_bytes: int = 4_000_000
    #: The cached group of pictures a joining viewer is replayed from, at once, up to this many bytes (and, for a source
    #: whose key
    #: pictures are cheap to ask for, up to this age in seconds); else it waits for a key picture, for at most
    #: ``join_wait_s``.
    join_replay_max_bytes: int = 3_000_000
    join_replay_max_age_s: float = 1.5
    join_wait_s: float = 1.0
    #: At most one request for a key picture per this many seconds.
    keyframe_min_interval_s: float = 2.0
    #: Follow the network (the default; ``set_quality("auto")`` after a manual level goes back to it): step down one
    #: rung when **most** viewers
    #: cannot keep up, and one rung back up after ``adapt_quiet_s`` of room; steps are at least ``adapt_min_interval_s``
    #: apart.
    adaptive: bool = True
    adapt_min_interval_s: float = 30.0
    adapt_quiet_s: float = 60.0
    #: The longest a recording may run, and the largest file it may make, before it ends by itself (the file is held on
    #: the exporter
    #: until the client takes it or the lease ends).
    max_recording_s: float = 600.0
    max_recording_bytes: int = 256 * 1024 * 1024
    #: A stream with a viewer that has produced no picture for this many seconds is asked about by the source
    #: (``on_stall``) and, if that
    #: says so, restarted or ended. A still screen can be quiet for seconds. 0: no watchdog.
    stall_s: float = 15.0

    def __post_init__(self) -> None:
        """Check the configuration; touches no device."""
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._validate()
        self._hard_keys: tuple[HardKey, ...] | None = None
        if self.hard_keys is not None:
            if not isinstance(self.hard_keys, list):
                raise ConfigurationError("hard_keys must be a list")
            try:
                self._hard_keys = tuple(HardKey.parse(item) for item in self.hard_keys)
            except ValueError as e:
                raise ConfigurationError(str(e)) from e
        self._lock = threading.RLock()
        #: the source is built under this one, never under ``_lock``: ``_lock`` is held through a whole start
        self._source_lock = threading.Lock()
        #: counts the stops (``stop``, a lease's end): a start that finds it changed was overtaken, and must not go on
        self._generation = 0
        self._warned_alias = False
        self._source: HmiSource | None = None
        self._session: _Session | None = None
        self._hub = (
            self._new_hub()
        )  # replaced when the source exists (its key pictures' cost is part of the hub's policy)
        self._state = "stopped"
        self._error: str | None = None
        self._inputs = 0
        self._last_activity = time.monotonic()
        self._pinned: Rung | None = None
        self._down = 0
        self._native: tuple[int, int] | None = None
        self._rung: Rung | None = None
        self._adapter = Adapter(min_interval_s=self.adapt_min_interval_s, quiet_s=self.adapt_quiet_s)
        self._stepping = False
        self.steps: list[dict] = []
        self._recording: Recording | None = None
        self._recording_dir: str | None = None
        self._recording_result: dict | None = None

    def _validate(self) -> None:
        for name in ("max_size", "bit_rate", "max_fps"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
                raise ConfigurationError(f"{name} must be a positive whole number")
        for name in (
            "idle_stop_s",
            "max_lag_s",
            "join_wait_s",
            "join_replay_max_age_s",
            "keyframe_min_interval_s",
            "adapt_min_interval_s",
            "adapt_quiet_s",
            "stall_s",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
                raise ConfigurationError(f"{name} must be a number of seconds, not negative")
        for name in ("start_timeout_s", "stop_deadline_s", "max_recording_s"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
                raise ConfigurationError(f"{name} must be a positive number of seconds")
        if not isinstance(self.control, bool):
            raise ConfigurationError("control must be true or false")

    # -- what a subclass supplies --------------------------------------------------------------------------------------

    @abstractmethod
    def make_source(self) -> HmiSource:
        """The device's source. Called once, lazily (never in the constructor); it is started and stopped as often as
        the stream is.
        """

    def _the_source(self) -> HmiSource:
        with self._source_lock:
            if self._source is None:
                self._source = self.make_source()
                self._hub = self._new_hub()  # nothing can be attached to the first one: attaching needs the source
            return self._source

    def _caps(self) -> Capabilities:
        """What the source says, with the exporter's own choices applied: ``control: false`` and ``hard_keys``."""
        caps = self._the_source().capabilities()
        if not self.control:
            return replace(caps, max_fingers=0, keys=(), hard_keys=(), text=False)
        if self._hard_keys is not None:
            known = {canonical_key(k) for k in caps.keys}
            optional = {canonical_key(k) for k in caps.optional_keys}
            # a key the source can send but does not offer by default is accepted once the exporter lists it
            enabled = tuple(dict.fromkeys(k.key for k in self._hard_keys if k.key in optional and k.key not in known))
            unknown = [k.key for k in self._hard_keys if k.key not in known and k.key not in enabled]
            if unknown:
                self.logger.warning("hard_keys names keys the screen does not accept: %s", ", ".join(unknown))
            caps = replace(caps, keys=(*caps.keys, *enabled), hard_keys=self._hard_keys)
        return caps

    def _new_hub(self) -> Hub:
        source = self._source
        cost = source.capabilities().keyframe_cost if source is not None else "none"
        return Hub(
            max_lag_s=self.max_lag_s,
            max_queue_bytes=self.max_queue_bytes,
            join_replay_max_bytes=self.join_replay_max_bytes,
            join_replay_max_age_s=self.join_replay_max_age_s,
            join_wait_s=self.join_wait_s,
            keyframe_min_interval_s=self.keyframe_min_interval_s,
            keyframe_cost=cost,
            # only a source for which a key picture is cheap is ever asked for one: for the others (a request restarts
            # the capture for every viewer, or cannot be made) the hub relies on the periodic key picture and
            # ``join_wait_s``
            on_need_idr=self._need_idr if cost == "cheap" else None,
            on_stalled=self._viewer_stalled,
        )

    def _viewer_stalled(self, viewer: Viewer) -> None:
        """The hub gave up on a viewer that made no progress through repeated overflows; its pump ends the stream (see
        ``_pump``).
        """
        self.logger.warning("hmi: viewer %s stopped reading: closed (%s)", viewer.id, viewer.close_reason)

    def _need_idr(self) -> None:
        session = self._session
        if session is not None and session.source.alive:
            with contextlib.suppress(Exception):
                session.source.request_keyframe()

    # -- lease boundaries ----------------------------------------------------------------------------------------------

    def reset(self):
        """A new lease: whatever an earlier one left running is ended, the configured quality is back, and the stream
        starts on first use.
        """
        self._stop_session(why="a new lease")
        with self._lock:
            self._error = None
            self._pinned, self._down, self._native, self._rung = None, 0, None, None
            self._adapter.reset()
            self.steps = []
        self._drop_recording()
        super().reset()

    def close(self):
        """The lease ended (or the exporter is going down): the capture is stopped within ``stop_deadline_s`` and the
        device is let go.
        """
        self._stop_session(why="the lease ended")
        self._drop_recording()
        super().close()

    # -- starting and stopping -----------------------------------------------------------------------------------------

    def _ensure_started(self) -> _Session:
        """The running session, starting it if there is none."""
        with self._lock:
            session = self._session
            if session is not None and session.source.alive and not session.ended.is_set():
                self._last_activity = time.monotonic()
                return session
            if session is not None:  # it ended by itself: say so, then start again
                self._finish(session)
            return self._start_locked(self._start_rung())

    def _start_rung(self) -> Rung | None:
        if self._the_source().capabilities().quality is None:
            return None
        return self._pinned if self._pinned is not None else self._wanted()

    def _start_locked(self, rung: Rung | None) -> _Session:
        source = self._the_source()
        generation = self._generation
        self._state, self._error = "starting", None
        session = _Session(source, self._hub)
        try:
            if self._native is None:
                self._native = source.native_size()
                if self._native is not None and self._pinned is None and rung is not None:
                    rung = self._wanted()  # the top rung depends on the display's own size, which is now known
            source.start(_SinkAdapter(session), rung, self.start_timeout_s)
        except Exception as e:
            with contextlib.suppress(Exception):
                source.stop(self.stop_deadline_s)
            if self._generation != generation:
                raise RuntimeError("the stream was stopped while it was starting") from e
            self._state, self._error = "error", str(e) or type(e).__name__
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(self._error) from e
        if self._generation != generation:
            # a stop (or the end of the lease) overtook this start while it held the lock: what it started is not
            # wanted,
            # and its hub is the one that stop closed
            session.stopped = True
            source.stop(self.stop_deadline_s)
            raise RuntimeError("the stream was stopped while it was starting")
        self._rung = rung
        self._session = session
        self._state = "running"
        self._last_activity = time.monotonic()
        self._start_watchdog(session)
        return session

    def _finish(self, session: _Session) -> None:
        """A session that ended by itself: keep why, drop it, end the viewers (they come back and start a new one)."""
        error = session.error or session.source.error
        if error:
            self._error = error
        session.stopped = True
        session.source.stop(self.stop_deadline_s)
        self._hub.close()
        self._hub = self._new_hub()
        self._session = None
        self._state = "error" if error else "stopped"

    def _stop_session(self, *, why: str) -> None:
        """End the session and every viewer, within ``stop_deadline_s``: the lock is taken with a timeout, and the
        source's own stop is bounded.
        """
        began = time.monotonic()
        self._generation += 1  # before the lock: a start that holds it learns of this stop when it comes back
        locked = self._lock.acquire(timeout=self.stop_deadline_s / 2)

        def remaining() -> float:
            return max(self.stop_deadline_s - (time.monotonic() - began), 0.2)

        try:
            if not locked:
                self.logger.warning("hmi: the driver is busy; stopping the source without its lock (%s)", why)
            session, self._session = self._session, None
            hub = self._hub
            self._hub = self._new_hub()
            self._state = "stopped"
            if session is not None:
                session.stopped = True
                self.logger.info("hmi: stopping (%s)", why)
                session.source.stop(remaining())
            elif self._source is not None and not locked:
                self._source.stop(remaining())
            hub.close()
        finally:
            if locked:
                self._lock.release()

    def _start_watchdog(self, session: _Session) -> None:
        def watch() -> None:
            while not session.ended.wait(1.0):
                if self._session is not session:
                    return
                with self._lock:  # a viewer that is being handed this session holds the lock until it is attached
                    now = time.monotonic()
                    if self._session is not session:
                        return
                    idle = self.idle_stop_s and self._hub.viewer_count == 0 and self._inputs == 0
                    if idle and now - self._last_activity > self.idle_stop_s:
                        self._stop_session(why=f"nobody watching for {self.idle_stop_s:g} s")
                        return
                    self._check_stall(session, now)
                    self._adapt(now)
                    self._hub.release_waiting_joins()  # a still screen sends no picture to do it
                    self._hub.poll()
            with self._lock:
                if self._session is session:
                    self._finish(session)

        threading.Thread(target=watch, name="hmi-watchdog", daemon=True).start()

    def _check_stall(self, session: _Session, now: float) -> None:
        """A stream with viewers and no picture for ``stall_s`` seconds: ask the source what to do about it (on a thread
        of its own).
        """
        if not self.stall_s:
            return
        if self._hub.viewer_count == 0:
            session.last_media_at = (
                now  # silence nobody is watching is not a stall; it is measured from when a viewer comes
            )
            return
        if now - session.last_media_at <= self.stall_s or session.probing:
            return
        session.probing = True
        threading.Thread(target=self._probe, args=(session,), name="hmi-stall-probe", daemon=True).start()

    def _probe(self, session: _Session) -> None:
        try:
            silent = time.monotonic() - session.last_media_at
            session.stall_attempt += 1
            action = session.source.on_stall(silent, session.stall_attempt)
            if action == "restart" and self._session is session:
                self.logger.warning("hmi: no picture for %.0f s: restarting the capture", silent)
                with self._lock:
                    if self._session is session:
                        self._restart(self._rung)
            elif action == "end" and not session.ended.is_set():
                self.logger.warning("hmi: no picture for %.0f s and the source is gone: ending the stream", silent)
                session.end(f"the video stream stalled: no picture for {silent:.0f} s")
        except Exception:
            self.logger.warning("hmi: the stall probe failed", exc_info=True)
        finally:
            session.probing = False
            session.last_media_at = time.monotonic()  # the next look comes one stall period later

    def _restart(self, rung: Rung | None) -> None:
        """Stop the source and start it again with ``rung``, keeping the viewers (lock held)."""
        old, self._session = self._session, None
        if old is not None:
            old.stopped = True
            old.source.stop(self.stop_deadline_s)
            self._hub.reset_source()
        try:
            self._start_locked(rung)
        except Exception:
            # the viewers are on a stream that is not coming back: end them (they come back and start again)
            hub, self._hub = self._hub, self._new_hub()
            hub.close()
            raise

    # -- adaptive quality ----------------------------------------------------------------------------------------------
    # The network between the client and the exporter limits the stream, not the configuration: it starts at the top
    # rung and the hub measures each viewer's path; the watchdog reads it once a second.

    def _top(self) -> Rung:
        return top_rung(self._native, self.max_size, self.bit_rate, self.max_fps)

    def _ladder(self) -> list[Rung]:
        return ladder(self._top())

    def _level(self) -> int:
        """Where the stream is on the ladder: rungs below the top for the adaptation's own choice, the nearest rung by
        bit rate for a pinned one.
        """
        rungs = self._ladder()
        pinned = self._pinned
        if pinned is None:
            return max(0, len(rungs) - 1 - self._down)
        return min(range(len(rungs)), key=lambda i: abs(rungs[i][1] - pinned.bit_rate))

    def _wanted(self) -> Rung:
        """The rung the stream should be on now: the operator's pinned one, else the one the adaptation is on."""
        if self._pinned is not None:
            return self._pinned
        rungs = self._ladder()
        return rungs[max(0, len(rungs) - 1 - self._down)]

    def _adapt(self, now: float) -> None:
        """One look at the viewers' paths (the watchdog's tick, lock held): a step down when most of them cannot keep
        up, one back up when
        they have all had room for a while. Off while the operator has pinned a level."""
        quality = self._the_source().capabilities().quality
        if (
            not self.adaptive
            or quality is None
            or not quality.auto
            or self._pinned is not None
            or self._stepping
            or self._state != "running"
            or self._hub.viewer_count == 0
        ):
            return
        rungs = self._ladder()
        decision = self._adapter.decide(
            now,
            level=self._level(),
            top=len(rungs) - 1,
            recent=self._hub.path_samples(self._adapter.recent_window_s(now)),
            quiet=self._hub.path_samples(self._adapter.quiet_needed_s),
        )
        if decision is None:
            return
        self._adapter.stepped(now, decision.direction)  # claimed here, so that the next tick does not start another
        self._stepping = True
        threading.Thread(
            target=self._step,
            args=(1 if decision.direction == "down" else -1, decision.direction, decision.why),
            name="hmi-adapt",
            daemon=True,
        ).start()

    def _step(self, rungs_down: int, direction: str, why: str) -> None:
        """One automatic change of rung (``set_quality`` is the operator's)."""
        try:
            with self._lock:
                before, previous_down = self._rung or self._top(), self._down
                self._down = max(0, min(self._down + rungs_down, len(self._ladder()) - 1))
                wanted = self._wanted()
                self.logger.warning(
                    "hmi: %s: quality %s -> %s kbit/s, size %s, %s fps (%s)",
                    direction,
                    before.bit_rate // 1000,
                    wanted.bit_rate // 1000,
                    wanted.max_size or "native",
                    wanted.max_fps,
                    why,
                )
                self.steps.append(
                    {
                        "at": round(time.time()),
                        "direction": direction,
                        "why": why,
                        "bit_rate": wanted.bit_rate,
                        "max_size": wanted.max_size,
                        "max_fps": wanted.max_fps,
                        "from": before._asdict(),
                    }
                )
                del self.steps[:-20]
                try:
                    self._apply_rung(wanted)
                except Exception:
                    self._down = previous_down
                    raise
        except Exception:
            self.logger.warning("hmi: the step %s failed", direction, exc_info=True)
        finally:
            self._stepping = False

    def _apply_rung(self, rung: Rung) -> None:
        """Make ``rung`` the stream's quality, restarting a running capture with it unless the source can change it
        live.
        """
        with self._lock:
            session = self._session
            if session is not None and session.source.alive:
                if rung == self._rung:
                    return
                if session.source.apply_rung(rung):
                    self._rung = rung
                    return
                self._restart(rung)
            else:
                self._rung = rung  # nothing is running: the next start uses it

    def _quality_caps(self) -> QualityCaps:
        caps = self._the_source().capabilities().quality
        if caps is None:
            raise ValueError("this screen's quality cannot be adjusted")
        return caps

    # -- plain calls ---------------------------------------------------------------------------------------------------

    @export
    def start(
        self,
        quality: str | int | None = None,
        max_size: int | None = None,
        bit_rate: int | None = None,
        max_fps: int | None = None,
    ) -> dict:
        """Start the stream if it is not running (joining ``video`` or ``input`` does it too); returns the status.

        With ``quality`` (a level name or 0-3) or any of ``max_size`` / ``bit_rate`` / ``max_fps``, the quality changes
        first; a running
        stream is restarted with it (viewers stay attached and get the new codec line).
        """
        if quality is not None or max_size is not None or bit_rate is not None or max_fps is not None:
            self._apply(quality, max_size, bit_rate, max_fps)
        self._ensure_started()
        return self.status()

    @export
    def stop(self) -> dict:
        """Stop the stream, ending every viewer's; the device is let go within ``stop_deadline_s``. Returns status."""
        self._stop_session(why="stop")
        return self.status()

    @export
    def set_quality(self, level: str | int) -> dict:
        """Choose the picture quality: ``auto`` (the default: follow the network, from the top), or a level to pin:
        ``minimal``, ``low``,
        ``medium``, ``high`` (the top: the device's own size at the bit rate that suits it; or 0 to 3). A pinned level
        is a request for this
        stream only (a new lease starts on ``auto`` again) and turns the adaptation off. Restarts a running stream's
        capture, for every viewer.

        Raises:
            ValueError: If the screen's quality cannot be adjusted, or ``level`` is not one.
        """
        self._apply(level, None, None, None)
        return self.status()

    def _apply(
        self, quality: str | int | None, max_size: int | None, bit_rate: int | None, max_fps: int | None
    ) -> None:
        self._quality_caps()
        if (
            quality is not None
            and str(quality).lower() == "auto"
            and max_size is None
            and bit_rate is None
            and max_fps is None
        ):
            with self._lock:
                self._pinned, self._down = None, 0
                self._adapter.reset()
                self._adapter.changed_at = time.monotonic()  # no step in the first interval
            self._apply_rung(self._wanted())
            return
        base = self._rung or self._top()
        size, rate, fps = base.max_size, base.bit_rate, base.max_fps
        if quality is not None:
            if isinstance(quality, float) and quality.is_integer():  # a generic call carries every number as a float
                quality = int(quality)
            try:
                name = LEVELS[int(quality)] if str(quality).isdigit() else str(quality).lower()
                if name not in LEVELS:
                    raise KeyError(name)
            except (KeyError, IndexError):
                raise ValueError(
                    f"quality must be one of auto, {', '.join(LEVELS)} (or 0-{len(LEVELS) - 1}): {quality!r}"
                ) from None
            size, rate, fps = level_rung(name, self._top())
        size = size if max_size is None else int(max_size)
        rate = rate if bit_rate is None else int(bit_rate)
        fps = fps if max_fps is None else int(max_fps)
        if size < 0 or rate < 1 or fps < 1:
            raise ValueError("max_size must not be negative, bit_rate and max_fps must be positive")
        changed = Rung(size, rate, fps)
        with self._lock:
            self._pinned = changed
        self._apply_rung(changed)

    @export
    def report_lag(self, viewer: int, lag_s: float) -> bool:
        """Deprecated: a viewer's report of how far behind real time it is. A client writes ``{"lag": seconds}`` on its
        ``video`` stream
        instead; this call stays for one release for clients that cannot. Returns False for a viewer that is not
        attached any more."""
        if not self._warned_alias:
            self._warned_alias = True
            self.logger.warning(
                'hmi: report_lag/report_path are deprecated: write {"lag": seconds} on the video stream'
            )
        try:
            lag = float(lag_s)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(lag) or lag < 0:
            return False
        return self._hub.report_lag(int(viewer), lag)

    @export
    def report_path(self, viewer: int, lag_s: float) -> bool:
        """Deprecated alias of :meth:`report_lag`."""
        return self.report_lag(viewer, lag_s)

    @export
    def hmi_info(self) -> dict:
        """What this screen is and what it takes, for a UI to show only the input a device has (read once when a screen
        opens).
        """
        caps = self._caps()
        line = self._hub.line
        width, height = line.shown_size if line else (None, None)
        return caps.info(width=width, height=height, rotation=(line.rotation or 0) if line else 0)

    @export
    def status(self) -> dict:
        """What the stream is doing: ``state``, the picture, the measured rate, the viewers and what each lost, the
        quality, the recording and the device's own facts under ``device``. Takes no lock: it answers while a start is
        in progress (``state`` is then ``starting``).
        """
        source = self._the_source()
        session, hub = self._session, self._hub
        alive = session is not None and session.source.alive
        line = hub.line
        measure = hub.measure()
        return {
            "state": self._state if not (self._state == "running" and not alive) else "stopped",
            "error": self._error or (session.error if session is not None else None),
            "picture": {
                "codec": line.codec if line else None,
                "width": line.width if line else None,
                "height": line.height if line else None,
                "rotation": (line.rotation or 0) if line else 0,
                "fps": line.fps if line else None,
                "measured_fps": measure["fps"],
                "kbit_s": measure["kbit_s"],
            },
            "stream": {
                "frames": hub.frames,
                "idrs": hub.idrs,
                "gop": hub.gop(),
                "joins": {"replayed": hub.joins_replayed, "waited": hub.joins_waited, "forced": hub.joins_forced},
            },
            "viewers": [v.stats() for v in hub.viewers()],
            "quality": self._quality_status(source.capabilities()),
            "recording": self.recording_status(),
            "device": source.describe(),
            "uptime_s": round(time.monotonic() - session.started_at, 1) if alive and session is not None else 0,
        }

    def _quality_status(self, caps: Capabilities) -> dict:
        """What the UI's quality control shows: the mode, where on the ladder, the top and the ceilings, and why it last
        stepped.
        """
        if caps.quality is None:
            return {"adjustable": False, "mode": "fixed", "levels": []}
        rungs = self._ladder()
        top = self._top()
        pinned = None
        if self._pinned is not None:
            now = tuple(self._pinned)
            pinned = next((name for name in LEVELS if tuple(level_rung(name, top)) == now), "custom")
        return {
            "adjustable": True,
            "mode": "manual" if self._pinned is not None else "auto",
            "pinned": pinned,
            "adaptive": self.adaptive and caps.quality.auto,
            "level": self._level(),
            "levels": len(rungs),
            "named": {
                name: min(range(len(rungs)), key=lambda i, n=name: abs(rungs[i][1] - level_rung(n, top)[1]))
                for name in LEVELS
            },
            "top": top._asdict(),
            "ceiling": {"max_size": self.max_size, "bit_rate": self.bit_rate, "max_fps": self.max_fps},
            "native": {"width": self._native[0], "height": self._native[1]} if self._native else None,
            "last_step": self.steps[-1] if self.steps else None,
            "steps": list(self.steps[-5:]),
        }

    # -- recording -----------------------------------------------------------------------------------------------------

    @export
    def start_recording(self, audio: bool = False, mic_audio: bool = False, max_seconds: float | None = None) -> dict:
        """Record the stream to an MP4 on the exporter (muxed here from the stream's own H.264: no transcode, no
        ffmpeg), starting at the
        key picture the stream last had. ``audio`` and ``mic_audio`` are accepted and not honored: the stream has no
        audio.

        The file is held on the exporter until :meth:`stop_recording` has been called and the client has taken it (or
        the lease ends).
        """
        with self._lock:
            if self._recording is not None and self._recording.active:
                raise RuntimeError("a recording is already running")
            self._drop_recording()
            self._ensure_started()
            self._recording_dir = tempfile.mkdtemp(prefix="hmi-recording-")  # 0700
            limit = min(float(max_seconds), self.max_recording_s) if max_seconds else self.max_recording_s
            self._recording = Recording.start(
                self._hub,
                "recording.mp4",
                root=self._recording_dir,
                max_seconds=limit,
                max_bytes=self.max_recording_bytes,
            )
            return {
                "active": True,
                "audio": False,
                "mic_audio": False,
                "max_seconds": limit,
                **self._recording.status(),
            }

    @export
    def recording_status(self) -> dict:
        """What the recording is doing: ``{"active", "seconds", "frames", "bytes", "reason"}`` (``active`` false: none,
        or it ended).
        """
        recording = self._recording
        if recording is None:
            return {"active": False, "audio": False, "mic_audio": False, "seconds": 0, "frames": 0, "bytes": 0}
        return {"audio": False, "mic_audio": False, **recording.status()}

    @export
    def stop_recording(self) -> dict:
        """End the recording and finish its file; returns ``{"name", "size", "seconds", "frames", "width", "height",
        "reason"}``.

        The bytes are fetched with the ``recording_download`` stream (the client's ``stop_recording`` does both).
        """
        recording = self._recording
        if recording is None:
            raise RuntimeError("no recording was started")
        result = recording.stop()
        self._recording_result = {**result, "name": "recording.mp4"}
        return {k: v for k, v in self._recording_result.items() if k != "path"}

    @exportstream
    @asynccontextmanager
    async def recording_download(self):
        """The finished recording's bytes (an MP4), then the end of the stream."""
        result = self._recording_result
        if result is None:
            raise RuntimeError("there is no finished recording: stop_recording first")
        framework, mine = _pipe(buffer=4)

        async def send() -> None:
            try:
                async with await anyio.open_file(result["path"], "rb") as handle:
                    while chunk := await handle.read(256 * 1024):
                        await mine.send(chunk)
                await mine.send_eof()
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                return

        async with _background(send):
            yield framework

    def _drop_recording(self) -> None:
        """Forget the recording and delete its file (a new recording, a new lease, the end of the lease)."""
        recording, self._recording, self._recording_result = self._recording, None, None
        directory, self._recording_dir = self._recording_dir, None
        if recording is not None:
            with contextlib.suppress(Exception):
                recording.stop()
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)

    # -- video ---------------------------------------------------------------------------------------------------------

    @exportstream
    @asynccontextmanager
    async def video(self):
        """The screen as frames (:mod:`jumpstarter_driver_hmi.common`); joining starts the capture if needed.

        The client may send JSON lines back: ``{"resync": true}`` makes this viewer wait for the next key picture (what
        a decoder that
        errored needs), and ``{"lag": seconds}`` says how far behind real time it is (what adaptive quality listens to).
        """
        waker = _Waker()
        try:
            for _ in range(3):
                await to_thread.run_sync(self._ensure_started)
                hub = self._hub
                try:
                    viewer = hub.add_viewer(notify=waker.notify)
                except (
                    StreamEnded
                ):  # the stream was stopped (or its source died) between its start and this attach: start it again
                    continue
                if self._hub is hub:
                    break
                hub.remove_viewer(viewer)
            else:
                raise RuntimeError("the stream kept ending while this viewer was joining it")
            framework, mine = _pipe()
            try:
                async with _background(
                    lambda: self._pump(viewer, waker, mine), lambda: self._viewer_control(hub, viewer, mine)
                ):
                    yield framework
            finally:
                hub.remove_viewer(viewer)
                self._last_activity = time.monotonic()
        finally:
            waker.close()

    async def _pump(self, viewer: Viewer, waker: _Waker, mine: StapledObjectStream[bytes]) -> None:
        """The viewer's queue into the stream, a frame per send."""
        try:
            while True:
                frame = viewer.pop()
                if frame is not None:
                    with anyio.move_on_after(SEND_TIMEOUT_S) as sending:
                        await mine.send(frame.wire)
                    if sending.cancelled_caught:
                        self.logger.warning(
                            "hmi: viewer %s took no picture for %g s: ending its stream", viewer.id, SEND_TIMEOUT_S
                        )
                        with anyio.CancelScope(shield=True), contextlib.suppress(Exception):
                            await mine.aclose()
                        return
                elif viewer.closed:
                    break
                else:
                    await waker.wait()
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            return
        with contextlib.suppress(Exception):
            await mine.send_eof()

    async def _viewer_control(self, hub: Hub, viewer: Viewer, mine: StapledObjectStream[bytes]) -> None:
        """What the client sends back, and its end: a client that closes its side of the stream has left."""
        lines = LineBuffer(MAX_BACK_CHANNEL_BYTES)
        try:
            while True:
                try:
                    batch = lines.feed(await mine.receive())
                except LineTooLongError as e:
                    batch = e.lines
                for line in batch:
                    try:
                        request = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(request, dict):
                        continue
                    if request.get("resync"):
                        hub.resync(viewer)
                    lag = request.get("lag")
                    if isinstance(lag, int | float) and not isinstance(lag, bool) and 0 <= lag < 3600:
                        viewer.report_lag(float(lag))
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass
        finally:
            hub.remove_viewer(viewer)

    # -- input ---------------------------------------------------------------------------------------------------------

    @exportstream
    @asynccontextmanager
    async def input(self):
        """Touch, key and text events as JSON lines over this stream::

            {"touch": {"type": "down" | "move" | "up" | "cancel", "x": 540, "y": 1200, "id": 0, "w": 1080, "h": 2400}}
            {"key": {"key": "HOME", "press": "short"}}
            {"text": "hello"}

        ``x`` and ``y`` are pixels of the picture the viewer is shown (the codec line's size, turned by its rotation);
        ``w`` and ``h`` (optional) name that picture, so that a touch aimed before a rotation is not applied after it.
        One contact is tracked: any ``id`` other than 0 is ignored. A line that is wrong, or that the device could not
        take, is answered with ``{"error": "..."}``; the stream goes on, and the exporter never waits for the client
        to read the answers. A finger still down when the stream ends is lifted. Events reach the device in the order
        they were sent.
        """
        caps = await to_thread.run_sync(self._caps)  # the source may be built here: never on the event loop
        if caps.max_fingers == 0:
            raise RuntimeError("this screen takes no input from here")
        session = await to_thread.run_sync(self._ensure_started)
        if session.source.input_sink() is None:
            raise RuntimeError("this screen takes no input from here")
        framework, mine = _pipe(buffer=8)
        errors_tx, errors_rx = anyio.create_memory_object_stream[bytes](16)
        state = _InputState()
        self._inputs += 1
        try:
            async with _background(
                lambda: self._input_loop(mine, errors_tx, state, caps), lambda: self._error_loop(mine, errors_rx)
            ):
                yield framework
        finally:
            errors_tx.close()
            self._end_input(state)

    def _current_sink(self) -> tuple[_Session, InputSink]:
        """The input sink of the capture that is running now (a restart may have made a new one)."""
        session = self._session
        sink = session.source.input_sink() if session is not None and session.source.alive else None
        if session is None or sink is None:
            raise InputError("the screen is not running")
        return session, sink

    def _end_input(self, state: _InputState) -> None:
        """The stream ended: lift the finger that is down and let go of what the source holds. Never blocks (the sink
        does not).
        """
        if state.ended:
            return
        state.ended = True
        self._inputs -= 1
        self._last_activity = time.monotonic()
        try:
            session, sink = self._current_sink()
        except InputError:
            return  # nothing is running: nothing is held
        if state.down is not None and state.size is not None and state.session is session:
            with contextlib.suppress(Exception):
                sink.touch("up", state.down[0], state.down[1], state.size)
        state.down = None
        with contextlib.suppress(Exception):
            sink.release()

    async def _error_loop(self, mine: StapledObjectStream[bytes], errors) -> None:
        """Send the error lines, one at a time: the input loop only queues them, so a client that does not read stops
        nothing but this.
        """
        try:
            async for line in errors:
                await mine.send(line)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, anyio.EndOfStream):
            return

    @staticmethod
    def _answer(errors, message: str) -> None:
        with contextlib.suppress(anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
            errors.send_nowait(error_line(message))  # dropped when the client does not read them: they are a courtesy

    async def _input_loop(
        self, mine: StapledObjectStream[bytes], errors, state: _InputState, caps: Capabilities
    ) -> None:
        """Read the client's lines, check them, and hand them to the sink in order."""
        lines = LineBuffer()
        known = frozenset(canonical_key(k) for k in caps.keys)
        try:
            while True:
                try:
                    batch = lines.feed(await mine.receive())
                except LineTooLongError as e:
                    self._answer(errors, str(e))
                    batch = e.lines
                for line in batch:
                    try:
                        event = parse_input(line)
                        if event is not None:
                            self._handle(event, state, caps, known)
                    except (InputError, ValueError, RuntimeError, OSError) as e:
                        self._answer(errors, str(e))
        except (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass
        finally:
            # The client closing its side is the end of the input (the framework keeps the call open until the lease
            # ends).
            self._end_input(state)

    def _handle(self, event: Any, state: _InputState, caps: Capabilities, known: frozenset[str]) -> None:
        if isinstance(event, TouchEvent):
            self._touch(event, state)
        elif isinstance(event, KeyEvent):
            if event.key not in known:
                raise InputError(f"this screen has no key {event.key}")
            self._current_sink()[1].key(event.key, event.press)
        elif isinstance(event, TextEvent):
            if not caps.text:
                raise InputError("this screen does not take text")
            self._current_sink()[1].text(event.text)

    def _touch(self, event: TouchEvent, state: _InputState) -> None:
        """One touch into the source: checked against the picture the viewer is shown now."""
        session, sink = self._current_sink()
        if state.down is not None and state.session is not session:
            state.down = None  # the capture it went down on is gone (a restart): the new one never saw it
        line = self._hub.line
        if line is None:
            raise InputError("the screen stream is not running")
        width, height = line.shown_size
        if event.aimed is not None and event.aimed != (width, height):
            raise InputError(
                f"the picture is {width}x{height} now, not {event.aimed[0]}x{event.aimed[1]} "
                "(it was rotated or rescaled)"
            )
        x = min(max(round(event.x), 0), width - 1)
        y = min(max(round(event.y), 0), height - 1)
        kind = event.kind
        if state.down is None and kind in ("move", "up"):
            return  # a move or an up of a finger that is not down is nothing (a late event after it was lifted)
        if kind == "down" and state.down is not None:
            kind = "move"  # one contact: a second down is the same finger moving
        sink.touch(kind, x, y, (width, height))
        state.size = (width, height)
        state.session = session
        state.down = None if kind == "up" else (x, y)


@dataclass(kw_only=True)
class MockHmi(HmiDriver):
    """A screen of synthetic pictures, for tests and demos (no device)::

    export:
      hmi:
        type: jumpstarter_driver_hmi.driver.MockHmi
        config: {width: 480, height: 800}
    """

    width: int = 480
    height: int = 800
    fps: int = 30
    #: A key picture every this many pictures.
    gop: int = 30
    #: How big a delta and a key picture are, in bytes, and what asking for a key picture costs (``cheap``, ``restart``
    #: or ``none``): knobs for a demo of a slow viewer or of a source that restarts to give one.
    frame_bytes: int = 600
    key_bytes: int = 4000
    keyframe_cost: str = "cheap"

    def make_source(self) -> HmiSource:
        """The synthetic source."""
        from .testing.fake import FakeSource

        return FakeSource(
            width=self.width,
            height=self.height,
            fps=self.fps,
            gop=self.gop,
            control=self.control,
            frame_bytes=self.frame_bytes,
            key_bytes=self.key_bytes,
            keyframe_cost=self.keyframe_cost,
        )
