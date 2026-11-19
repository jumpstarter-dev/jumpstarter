"""Client of every hmi: blocking helpers for the stream, the input and the calls, and the ``j <hmi> ...``
commands.

::

    with client.hmi.video() as video:
        print(video.header)               # {"codec": "avc1.64001F", "width": 540, "height": 1200, "fps": 30, ...}
        for frame in video:               # blocks for the next picture; ends when the stream does
            if isinstance(frame, str):    # JSON text: the codec line changed (the first one is video.header)
                continue
            timestamp_us = int.from_bytes(frame[:8], "big")
            access_unit = frame[8:]       # Annex-B, starts with SPS/PPS + IDR for the first picture

    with client.hmi.input() as hmi:
        hmi.tap(270, 600)
        hmi.key("HOME")
        hmi.text("hello")

The frames are what a relay passes to a WebSocket, one to one: ``str`` is a text frame (the JSON codec line), ``bytes``
is a binary frame
(an 8-byte big-endian microsecond timestamp and one Annex-B access unit). What a Jumpstarter stream carries underneath
(length-prefixed
frames, because a byte stream has no message boundaries) is :mod:`jumpstarter_driver_hmi.common`'s concern.

The class carries ``HMI_STREAM_VERSION``, and every HMI driver's client is a :class:`HmiClient`: a UI server
finds a screen in a
driver tree with ``isinstance``. The client is blocking and runs its calls on the Jumpstarter portal's event loop, so
use it from threads,
not from a coroutine of your own loop.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import click
import grpc
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream

from .common import KIND_CONFIG, KIND_MEDIA, FrameParser, LineBuffer, LineTooLongError
from jumpstarter.client import DriverClient
from jumpstarter.client.decorators import driver_click_group

REFUSED = "refused"
ENDED = "ended"
ERROR = "error"
_REFUSALS = (grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.UNAUTHENTICATED)
#: What a stream that the exporter ended on purpose looks like: the framework aborts the call with ABORTED when a stream
#: is closed, and a
#: hang-up is CANCELLED.
_ENDS = (grpc.StatusCode.OK, grpc.StatusCode.ABORTED, grpc.StatusCode.CANCELLED)
#: What a read of a stream raises when it has ended: the end itself, a broken or closed pipe, or a task of the portal
#: that was cancelled
#: (``concurrent.futures.CancelledError`` is not an ``Exception`` of the anyio family and must not escape a reader).
_STREAM_ENDS = (EndOfStream, BrokenResourceError, ClosedResourceError, concurrent.futures.CancelledError)


def _integers(value: Any) -> Any:
    """Generic RPC values carry every number as a float; restore the integers."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {key: _integers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_integers(item) for item in value]
    return value


def _status_of(exc: BaseException | None) -> grpc.StatusCode | None:
    seen: set[int] = set()
    node = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        code = getattr(node, "code", None)
        if callable(code):
            try:
                value = code()
            except Exception:  # noqa: BLE001 (an exception's own accessor failing says nothing)
                value = None
            if isinstance(value, grpc.StatusCode):
                return value
        node = node.__cause__ or node.__context__
    return None


def _detail_of(exc: BaseException | None) -> str:
    seen: set[int] = set()
    node = exc
    while node is not None and id(node) not in seen:
        seen.add(id(node))
        details = getattr(node, "details", None)
        if callable(details):
            try:
                text = details()
            except Exception:  # noqa: BLE001
                text = None
            if text:
                return str(text)
        node = node.__cause__ or node.__context__
    return str(exc) if exc is not None and str(exc) else type(exc).__name__ if exc is not None else ""


def classify(exc: BaseException | None) -> tuple[str, str | None]:
    """``(reason, status)`` for an error from a stream: ``refused`` (policy, permission), ``ended`` (ordinary end) or
    ``error``.
    """
    code = _status_of(exc)
    if code in _REFUSALS:
        return REFUSED, code.name
    if (
        exc is None
        or isinstance(exc, EndOfStream | concurrent.futures.CancelledError)
        or (code is not None and code in _ENDS)
    ):
        return ENDED, code.name if code is not None else None
    return ERROR, code.name if code is not None else None


class HmiStreamClosedError(RuntimeError):
    """A stream did not open, or ended before it began: ``reason`` says whether it was refused, ended or broke."""

    def __init__(self, message: str, reason: str = ERROR, status: str | None = None, detail: str = "") -> None:
        """A message, and why."""
        super().__init__(message)
        self.reason = reason
        self.status = status
        self.detail = detail

    @classmethod
    def of(cls, what: str, exc: BaseException | None) -> HmiStreamClosedError:
        """``what`` happened because of ``exc`` (None: the stream just ended)."""
        reason, status = classify(exc)
        detail = _detail_of(exc)
        return cls(f"{what}: {detail}" if detail else what, reason=reason, status=status, detail=detail)


class _Receiving:
    """Reads a Jumpstarter stream from a calling thread, with a timeout that loses nothing.

    ``anyio.fail_after`` inside the portal's task cancels the receive when it expires, and the caller gets a cancelled
    future, not a
    ``TimeoutError``. Here the receive is started once and waited for with ``Future.result(timeout)``: when the wait
    times out the
    receive stays in flight, and the next call waits for the same one.
    """

    _raw: Any
    _inflight: Any = None

    def _receive_blocking(self, timeout: float | None) -> bytes:
        """The next chunk. ``TimeoutError`` after ``timeout`` seconds (the chunk, when it comes, is the next call's)."""
        if self._inflight is None:
            self._inflight = self._raw.portal.start_task_soon(self._receive_chunk)
        try:
            chunk = self._inflight.result(timeout)
        except concurrent.futures.TimeoutError as e:
            if not self._inflight.done():
                raise TimeoutError("nothing arrived in time") from e
            chunk = self._inflight.result()  # it finished in the instant between
        except BaseException:
            self._inflight = None
            raise
        self._inflight = None
        return chunk

    async def _receive_chunk(self) -> bytes:
        return await self._raw.stream.receive()


class LagMeter:
    """How far behind real time a consumer of the stream is, from the pictures' own timestamps: the wall-clock delay of
    each picture
    (``now - pts``) less the least delay seen in the last minute (the stream's own latency, and the offset of two
    clocks, are in it and cancel).

    A consumer that keeps up shows a lag of about nothing; one whose path cannot take the stream shows it growing by the
    second, however deep
    the buffers between it and the exporter are.

    What is **not** a lag, and so is not measured: the pictures a joiner is replayed from the cached group of pictures
    arrive at once with
    old timestamps (nothing is measured for ``WARM_UP_S`` after the codec line); for a consumer that is going already, a
    picture **older than
    one it has seen** is a replay (a resync) and is left out; and a delay over ``QUIET_S`` only counts once it has
    **lasted** ``CONFIRM_S`` (a
    consumer that stalled for a moment sees its first picture very late and the burst behind it catch up; a path that
    cannot keep up stays
    behind). What is said is held to ``CLAMP_S``.
    """

    WARM_UP_S = 3.0
    WINDOW_S = 60.0
    QUIET_S = 0.25
    CONFIRM_S = 1.0
    CLAMP_S = 30.0

    def __init__(self) -> None:
        """Nothing measured yet."""
        self.lag_s = 0.0
        self._since: float | None = None
        #: the minimum of the delays in the window, kept as a monotonic deque (increasing delays, oldest first): O(1)
        #: per picture
        self._minima: deque[tuple[float, float]] = deque()
        self._newest = -math.inf
        self._behind_since: float | None = None

    def restart(self) -> None:
        """A new codec line (a new capture session: the timestamps start again)."""
        self._since = None
        self._minima.clear()
        self.lag_s = 0.0
        self._newest = -math.inf
        self._behind_since = None

    def take(self, now: float, pts_us: int) -> float | None:
        """Take a picture: the lag it shows, or None while warming up or for a replayed picture."""
        if pts_us < self._newest:
            return None  # older than a picture already seen: the cached group of pictures given again, not a late one
        self._newest = pts_us
        if self._since is None:
            self._since = now
        delay = now - pts_us / 1e6
        while self._minima and self._minima[-1][1] >= delay:
            self._minima.pop()
        self._minima.append((now, delay))
        while self._minima and now - self._minima[0][0] > self.WINDOW_S:
            self._minima.popleft()
        if now - self._since < self.WARM_UP_S:
            return None
        behind = max(0.0, delay - self._minima[0][1])
        if behind < self.QUIET_S:
            self._behind_since = None
            self.lag_s = behind
        else:
            if self._behind_since is None:
                self._behind_since = now
            self.lag_s = min(behind, self.CLAMP_S) if now - self._behind_since >= self.CONFIRM_S else 0.0
        return self.lag_s


class Reporter:
    """Tells the exporter how far behind this consumer is, at most once a second, without ever making the consumer wait
    for the call.

    Its thread starts with the first report, not with the stream: a stream that never measures anything (or that fails
    to open) costs no
    thread.
    """

    INTERVAL_S = 1.0

    def __init__(self, report: Callable[[float], None]) -> None:
        """Remember how to report; nothing runs yet."""
        self._report = report
        self._latest: float | None = None
        self._last = 0.0
        self._cv = threading.Condition()
        self._stopped = False
        self._thread: threading.Thread | None = None

    def offer(self, lag_s: float) -> None:
        """Say the lag now; it goes out with the next report (the worst of those said since the last one)."""
        with self._cv:
            if self._stopped:
                return
            self._latest = max(lag_s, self._latest if self._latest is not None else 0.0)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="hmi-report-lag", daemon=True)
                self._thread.start()
            self._cv.notify()

    def stop(self) -> None:
        """Say nothing more."""
        with self._cv:
            self._stopped = True
            self._cv.notify()

    def _run(self) -> None:
        while True:
            with self._cv:
                while not self._stopped and (self._latest is None or time.monotonic() - self._last < self.INTERVAL_S):
                    self._cv.wait(timeout=0.25 if self._latest is not None else None)
                if self._stopped:
                    return
                lag_s = self._latest
                self._latest = None
                self._last = time.monotonic()
            with suppress(Exception):  # a report is a courtesy: a refused or failed write is not the stream's trouble
                if lag_s is not None:
                    self._report(lag_s)


class VideoStream(_Receiving):
    """The ``video`` stream: :attr:`header`, then frames. Obtained from :meth:`HmiClient.video`.

    When the stream ends, ``read_frame`` returns None (and iteration stops), and :attr:`end_reason` says how.
    """

    def __init__(self, raw: Any, header_timeout: float | None) -> None:
        """Read ahead to the codec line (``header_timeout`` seconds at most); the exporter may be starting the device's
        side.

        Raises:
            HmiStreamClosedError: If the stream ended or broke before the codec line, or began with something else.
        """
        self._raw = raw
        self._meter = LagMeter()
        self._reporter = Reporter(self._send_lag)
        self._parser = FrameParser()
        self._pending: list[tuple[int, bytes]] = []
        self._eof = False
        self.header: dict = {}
        self.frames = 0
        self.bytes = 0
        #: Set when the stream has ended: ``ended``, ``refused`` or ``error``; None while it is open.
        self.end_reason: str | None = None
        self.end_error: BaseException | None = None
        try:
            if not self._fill(header_timeout):
                raise HmiStreamClosedError.of(
                    "the screen stream ended before it said what it carries", self.end_error
                ) from self.end_error
            kind, body = self._pending[0]
            if kind != KIND_CONFIG:
                raise HmiStreamClosedError("the screen stream did not start with its codec line", reason=ERROR)
            self.header = json.loads(body.decode())
        except BaseException:
            self.close()  # whatever went wrong (a timeout too), nothing is left running
            raise

    @property
    def lag_s(self) -> float:
        """How far behind real time the last picture this consumer took was (0 until it has measured; see
        :class:`LagMeter`).
        """
        return self._meter.lag_s

    def _send_lag(self, lag_s: float) -> None:
        self._raw.send(json.dumps({"lag": round(lag_s, 3)}).encode() + b"\n")

    def report_lag(self, lag_s: float) -> None:
        """Say how far behind real time the far end of this stream is, as measured there (a browser, through a relay):
        the lag of the
        pictures' arrival, which sees what a consumer that reads at its own pace cannot (the buffers between). It goes
        to the exporter as a
        ``{"lag": seconds}`` line on this stream, at most once a second."""
        self._reporter.offer(max(0.0, float(lag_s)))

    def close(self) -> None:
        """Stop reporting (the stream itself is closed by the context that opened it)."""
        self._reporter.stop()

    def _fill(self, timeout: float | None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._pending:
            if self._eof:
                return False
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            try:
                chunk = self._receive_blocking(remaining)
            except _STREAM_ENDS as e:
                self._eof = True
                self.end_error = None if isinstance(e, EndOfStream | concurrent.futures.CancelledError) else e
                self.end_reason = classify(self.end_error)[0]
                return False
            self._pending.extend(self._parser.feed(chunk))
        return True

    def read_frame(self, timeout: float | None = None) -> str | bytes | None:
        """The next frame: ``str`` (JSON text) or ``bytes`` (timestamp + access unit), or None at the end of the stream.

        The first one is always the codec line. Raises ``TimeoutError`` when nothing arrives within ``timeout`` seconds
        (a still
        screen is not silent for long: the encoder repeats the last picture).
        """
        while True:
            if not self._fill(timeout):
                return None
            kind, body = self._pending.pop(0)
            if kind == KIND_CONFIG:
                text = body.decode().strip()
                self.header = json.loads(text)
                self._meter.restart()
                return text
            if kind == KIND_MEDIA:
                self.frames += 1
                self.bytes += len(body)
                lag = self._meter.take(time.monotonic(), int.from_bytes(body[:8], "big"))
                if lag is not None:
                    self._reporter.offer(lag)
                return body
            # a kind from a newer exporter: not for us

    def __iter__(self) -> Iterator[str | bytes]:
        """The stream is its own iterator."""
        return self

    def __next__(self) -> str | bytes:
        """The next frame; ``StopIteration`` when the stream ends."""
        frame = self.read_frame()
        if frame is None:
            raise StopIteration
        return frame

    def request_resync(self) -> None:
        """Ask the exporter to restart this viewer at the next key picture (what a decoder that errored needs)."""
        self._raw.send(b'{"resync": true}\n')


class InputStream(_Receiving):
    """The ``input`` stream: touch, key and text events. Obtained from :meth:`HmiClient.input`.

    The exporter answers a line it could not take with ``{"error": "..."}``: :meth:`read_error` (blocking) and
    :meth:`poll_errors`
    give them, or pass ``on_error`` to :meth:`HmiClient.input`. Use one of the three, not several: they read the same
    stream.
    """

    def __init__(self, raw: Any) -> None:
        """Wrap the raw blocking stream."""
        self._raw = raw
        self._lines = LineBuffer()
        self._errors: deque[str] = deque()
        self._eof = False
        #: Every error the exporter has answered with, in order.
        self.errors: list[str] = []
        self.end_reason: str | None = None
        self.end_error: BaseException | None = None

    def send(self, event: dict) -> None:
        """One raw event object: ``{"touch": {...}}``, ``{"key": {...}}`` or ``{"text": "..."}``.

        Raises:
            HmiStreamClosedError: If the stream has ended.
        """
        try:
            self._raw.send(json.dumps(event, separators=(",", ":")).encode() + b"\n")
        except _STREAM_ENDS as e:
            raise HmiStreamClosedError.of("the input stream is closed", e) from e

    def touch(self, kind: str, x: float, y: float, id: int = 0, size: tuple[float, float] | None = None) -> None:
        """A touch event: ``kind`` is ``down``, ``move`` or ``up``; ``x``, ``y`` are pixels of the shown picture.

        ``size`` is that picture's ``(width, height)`` (the codec line's, turned by its rotation): give it when the
        picture can change
        under the viewer, and a touch aimed before a rotation is refused instead of landing somewhere else.
        """
        event: dict = {"type": kind, "x": x, "y": y, "id": id}
        if size is not None:
            event["w"], event["h"] = size
        self.send({"touch": event})

    def tap(self, x: float, y: float, hold: float = 0.05, size: tuple[float, float] | None = None) -> None:
        """Down and up at one point."""
        self.touch("down", x, y, size=size)
        time.sleep(hold)
        self.touch("up", x, y, size=size)

    def swipe(self, x1: float, y1: float, x2: float, y2: float, seconds: float = 0.3, steps: int = 12) -> None:
        """Drag from one point to another."""
        self.touch("down", x1, y1)
        for i in range(1, steps + 1):
            time.sleep(seconds / steps)
            self.touch("move", x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
        self.touch("up", x2, y2)

    def key(self, key: str, press: str = "short") -> None:
        """A key (``HOME``, ``BACK``, ``APP_SWITCH``, ``POWER``...): ``press`` is ``short``, ``long``, ``down`` or
        ``up``.
        """
        self.send({"key": {"key": key, "press": press}})

    def text(self, text: str) -> None:
        """Type text into the focused field."""
        self.send({"text": text})

    def read_error(self, timeout: float | None = None) -> str | None:
        """The next error the exporter answered with, or None when the stream has ended. Raises ``TimeoutError`` after
        ``timeout``.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self._errors:
            if self._eof:
                return None
            remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
            try:
                chunk = self._receive_blocking(remaining)
            except _STREAM_ENDS as e:
                self._eof = True
                self.end_error = None if isinstance(e, EndOfStream | concurrent.futures.CancelledError) else e
                self.end_reason = classify(self.end_error)[0]
                return None
            try:
                lines = self._lines.feed(chunk)
            except LineTooLongError as e:
                lines = e.lines
            for line in lines:
                try:
                    message = str(json.loads(line).get("error", line.decode()))
                except (ValueError, AttributeError):
                    message = line.decode(errors="replace")
                self._errors.append(message)
        message = self._errors.popleft()
        self.errors.append(message)
        return message

    def poll_errors(self, timeout: float = 0.2) -> list[str]:
        """What the exporter answered with within ``timeout`` seconds: ``[]`` if nothing, or the stream has ended."""
        found: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            try:
                message = self.read_error(max(deadline - time.monotonic(), 0.0))
            except TimeoutError:
                return found
            if message is None:
                return found
            found.append(message)
            if self._errors:
                continue
            return found


@dataclass(kw_only=True)
class HmiClient(DriverClient):
    """A hmi: the client of every driver that implements the HMI interface."""

    #: Marks the class as a screen of the HMI interface (``video()``, ``input()``): a UI server that finds it
    #: anywhere in a lease's driver
    #: tree can relay it, whatever the child is called.
    HMI_STREAM_VERSION: ClassVar[int] = 1

    def start(
        self,
        quality: str | int | None = None,
        max_size: int | None = None,
        bit_rate: int | None = None,
        max_fps: int | None = None,
    ) -> dict:
        """Start the stream if it is not running (joining ``video`` or ``input`` does it too); returns the status."""
        return _integers(self.call("start", quality, max_size, bit_rate, max_fps))

    def stop(self) -> dict:
        """Stop the stream, end every viewer's, and have the exporter let go of the device; returns the status."""
        return _integers(self.call("stop"))

    def status(self) -> dict:  # ty: ignore[invalid-property-type-override]
        """The exporter's view: ``state``, the picture, the measured rate, the viewers and their losses, the quality and
        the recording.
        """
        return _integers(self.call("status"))

    def set_quality(self, level: str | int) -> dict:
        """Change the picture quality (``auto``, ``minimal``, ``low``, ``medium``, ``high`` or 0-3); restarts a running
        stream.

        Raises:
            ValueError: If the screen's quality cannot be adjusted.
        """
        return _integers(self.call("set_quality", level))

    def hmi_info(self) -> dict:
        """What the screen is and which input it takes."""
        return _integers(self.call("hmi_info"))

    def start_recording(self, audio: bool = False, mic_audio: bool = False, max_seconds: float | None = None) -> dict:
        """Record the stream to an MP4 on the exporter (``audio`` is accepted and ignored: there is none); returns the
        status.
        """
        return _integers(self.call("start_recording", audio, mic_audio, max_seconds))

    def recording_status(self) -> dict:
        """What the recording is doing: ``{"active", "seconds", "frames", "bytes", "reason"}``."""
        return _integers(self.call("recording_status"))

    def stop_recording(self) -> dict:
        """End the recording and fetch it: ``{"data": <the MP4's bytes>, "name", "size", "delivered", "seconds",
        "frames", ...}``.
        """
        info = _integers(self.call("stop_recording"))
        chunks = []
        with self.stream("recording_download") as raw:
            while True:
                try:
                    chunks.append(raw.portal.call(raw.stream.receive))
                except _STREAM_ENDS:
                    break
        data = b"".join(chunks)
        return {**info, "data": data, "size": len(data), "delivered": len(data) == info.get("size", len(data))}

    @contextmanager
    def video(self, timeout: float | None = 120.0) -> Generator[VideoStream]:
        """Join the stream. ``timeout`` bounds the wait for the codec line (the exporter may be starting the device's
        side).

        Raises:
            HmiStreamClosedError: If the stream could not be opened or ended before the codec line; ``reason`` is
                ``refused`` (policy or permissions: a relay closes with 4403), ``error`` (1011) or ``ended``.
        """
        with ExitStack() as stack:
            try:
                video = VideoStream(stack.enter_context(self.stream("video")), timeout)
                stack.callback(video.close)
            except HmiStreamClosedError:
                raise
            except (TimeoutError, KeyboardInterrupt):
                raise
            except Exception as e:
                raise HmiStreamClosedError.of("the screen stream could not be opened", e) from e
            yield video

    @contextmanager
    def input(self, on_error: Callable[[str], None] | None = None) -> Generator[InputStream]:
        """Open the input stream: touch, key and text events to the device.

        ``on_error`` is called (on a thread of its own) with the message of every event the exporter could not take, as
        they come,
        so that a relay can pass them on to its UI.

        Raises:
            HmiStreamClosedError: If the stream could not be opened, or the screen takes no input from here
            (``reason`` as for
                :meth:`video`).
        """
        if not self.hmi_info().get("input", {}).get("touch", True):
            raise HmiStreamClosedError("this screen takes no input from here", reason=ERROR)
        with ExitStack() as stack:
            try:
                stream = InputStream(stack.enter_context(self.stream("input")))
            except Exception as e:
                raise HmiStreamClosedError.of("the input stream could not be opened", e) from e
            listener = None
            if on_error is not None:

                def listen() -> None:
                    while (message := stream.read_error()) is not None:
                        on_error(message)

                listener = threading.Thread(target=listen, name="hmi-input-errors", daemon=True)
                listener.start()
            try:
                yield stream
            finally:
                if listener is not None:
                    stack.close()  # the stream's end is what lets the listener's read return
                    listener.join(timeout=5)

    def record(
        self, path: str | Path, seconds: float, *, on_progress: Callable[[int, int], None] | None = None
    ) -> dict:
        """Write ``seconds`` of the stream to ``path`` as a raw Annex-B ``.h264`` file; returns what was written.

        Starts at the first key picture (SPS/PPS included), so the file plays from its first picture. Raw H.264 has no
        timing:
        ``ffmpeg -framerate <fps> -i file.h264 -c copy file.mp4`` makes an MP4 of it (the ``fps`` of the result is
        nominal).
        """
        path = Path(path)
        frames = size = 0
        with self.video() as video, path.open("wb") as out:
            deadline = time.monotonic() + seconds
            first_ts: int | None = None
            last_ts = 0
            while time.monotonic() < deadline:
                try:
                    frame = video.read_frame(max(deadline - time.monotonic(), 0.05) + 5.0)
                except TimeoutError:
                    break
                if frame is None:
                    break
                if isinstance(frame, str):
                    continue
                timestamp = int.from_bytes(frame[:8], "big")
                first_ts = timestamp if first_ts is None else first_ts
                last_ts = timestamp
                out.write(frame[8:])
                frames += 1
                size += len(frame) - 8
                if on_progress is not None:
                    on_progress(frames, size)
            header = dict(video.header)
        span = (last_ts - first_ts) / 1e6 if first_ts is not None else 0.0
        return {
            "path": str(path),
            "frames": frames,
            "bytes": size,
            "seconds": round(span, 2),
            "fps": header.get("fps"),
            "width": header.get("width"),
            "height": header.get("height"),
            "codec": header.get("codec"),
        }

    def cli(self):  # noqa: C901 (one command group: each command is a few lines)
        """The command group of the screen (``j <hmi> ...``)."""
        client = self

        @driver_click_group(self)
        def hmi():
            """The device's screen and input, streamed by the exporter."""

        @hmi.command("status")
        def status_command():
            """What the stream is doing: state, picture, measured fps and bit rate, viewers and what each lost."""
            click.echo(json.dumps(client.status(), indent=2))

        @hmi.command("info")
        def info_command():
            """What the screen is and which input it takes (keys, hard keys, text, quality)."""
            click.echo(json.dumps(client.hmi_info(), indent=2))

        @hmi.command("start")
        @click.option("--quality", default=None, help="auto, minimal, low, medium or high (or 0-3)")
        @click.option("--max-size", type=int, default=None, help="longest side of the picture, pixels")
        @click.option("--bit-rate", type=int, default=None, help="video bit rate, bit/s")
        @click.option("--max-fps", type=int, default=None)
        def start_command(quality, max_size, bit_rate, max_fps):
            """Start the stream now (joining it does that too)."""
            try:
                click.echo(json.dumps(client.start(quality, max_size, bit_rate, max_fps), indent=2))
            except ValueError as e:
                raise click.ClickException(str(e)) from None

        @hmi.command("stop")
        def stop_command():
            """Stop the stream and have the exporter let go of the device."""
            click.echo(f"state: {client.stop()['state']}")

        @hmi.command("quality")
        @click.argument("level")
        def quality_command(level):
            """Set the picture quality (auto, minimal, low, medium, high, or 0-3); restarts the stream."""
            try:
                result = client.set_quality(int(level) if level.isdigit() else level)
            except ValueError as e:
                raise click.ClickException(str(e)) from None
            click.echo(json.dumps(result["quality"], indent=2))

        @hmi.command("recording")
        @click.option("-o", "--output", "output", required=True, type=click.Path(dir_okay=False, path_type=Path))
        @click.option("--seconds", type=click.FloatRange(min=0.5), default=5.0, show_default=True)
        def recording_command(output, seconds):
            """Record the stream on the exporter to an MP4 (muxed there, no transcode) and fetch it."""
            client.start_recording(max_seconds=seconds + 60)
            time.sleep(seconds)
            result = client.stop_recording()
            output.write_bytes(result["data"])
            click.echo(
                f"{output}: {result['size']} bytes, {result['seconds']} s, {result['frames']} frames, "
                f"{result['width']}x{result['height']}"
            )

        @hmi.command("record")
        @click.option("-o", "--output", "output", required=True, type=click.Path(dir_okay=False, path_type=Path))
        @click.option("--seconds", type=click.FloatRange(min=0.1), default=5.0, show_default=True)
        def record_command(output, seconds):
            """Record the stream to a raw .h264 file (Annex-B), starting at a key picture."""
            result = client.record(output, seconds)
            if not result["frames"]:
                raise click.ClickException("the stream gave no pictures")
            click.echo(
                f"{result['path']}: {result['frames']} frames, {result['bytes']} bytes, {result['seconds']} s, "
                f"{result['width']}x{result['height']} {result['codec']}"
            )

        @hmi.command("key")
        @click.argument("key")
        @click.option("--press", type=click.Choice(["short", "long", "down", "up"]), default="short")
        def key_command(key, press):
            """Press a key of the hmi: HOME, BACK, APP_SWITCH, POWER, VOLUME_UP, ... (see `info` for what it has)."""
            with client.input() as hmi_input:
                hmi_input.key(key, press)
                for error in hmi_input.poll_errors(0.5):
                    raise click.ClickException(error)

        @hmi.command("text")
        @click.argument("text")
        def text_command(text):
            """Type text into the focused field."""
            with client.input() as hmi_input:
                hmi_input.text(text)
                for error in hmi_input.poll_errors(0.5):
                    raise click.ClickException(error)

        @hmi.command("tap")
        @click.argument("x", type=float)
        @click.argument("y", type=float)
        def tap_command(x, y):
            """Tap at a pixel of the picture."""
            with client.input() as hmi_input:
                hmi_input.tap(x, y)
                for error in hmi_input.poll_errors(0.5):
                    raise click.ClickException(error)

        @hmi.command("view")
        @click.option("--port", type=click.IntRange(0, 65535), default=0, show_default=True, help="0: any free port")
        @click.option("--no-open", is_flag=True, help="do not open the browser")
        def view_command(port, no_open):
            """Watch and control the screen in a browser page served from this machine (127.0.0.1 only).

            The address it prints carries a random token the page needs for its stream and its input.
            """
            from .web import serve_view

            serve_view(client, port=port, open_browser=not no_open)

        return hmi
