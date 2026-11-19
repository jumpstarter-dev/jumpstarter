"""Client of every HMI: blocking helpers for the stream, the input and the calls, and the ``j <hmi> ...`` commands.

::

    with client.video() as video:
        print(video.header)               # {"codec": "avc1.64001F", "width": 540, "height": 1200, "fps": 30, ...}
        for frame in video:               # blocks for the next picture; ends when the stream does
            if isinstance(frame, str):    # JSON text: the codec line changed (the first one is video.header)
                continue
            timestamp_us = int.from_bytes(frame[:8], "big")
            access_unit = frame[8:]       # one picture: Annex-B (H.264, HEVC) or a JPEG (MJPEG)

    with client.input() as hmi:
        hmi.tap(270, 600)                 # a touch HMI: a finger; a mouse HMI: a click of the primary button
        hmi.click(270, 600, "secondary")  # a mouse
        hmi.scroll(0, 120)                # a mouse's wheel
        hmi.press("KeyA", modifiers=("ShiftLeft",))   # a keyboard key, by its W3C KeyboardEvent.code
        hmi.key("HOME")                   # a hard key, by name
        hmi.rotate("volume", -2)          # a rotary knob, two detents counter-clockwise
        hmi.push("volume")                # the knob's push button
        hmi.text("hello")

The frames are what a relay passes to a WebSocket, one to one: ``str`` is a text frame (the JSON codec line), ``bytes``
is a binary frame (an 8-byte big-endian microsecond timestamp and one access unit). What a Jumpstarter stream carries
underneath (length-prefixed frames, because a byte stream has no message boundaries) is
:mod:`jumpstarter_driver_hmi.common`'s concern.

The helpers of the input stream check what they are asked against what the HMI declares in ``hmi_info()["input"]`` and
raise :class:`InputNotDeclaredError`, sending nothing, when it does not take it (a ``scroll`` on a touch screen, a key
that is not on the keyboard, a rotary it does not have); :meth:`InputStream.send`, the raw ``touch``, ``pointer`` and
``keyboard`` methods and ``key`` send whatever they are given and leave the answer to the exporter.

The class carries ``HMI_STREAM_VERSION``, and every HMI driver's client is a :class:`HmiClient`: a UI server finds an
HMI in a driver tree with ``isinstance``. The client is blocking and runs its calls on the Jumpstarter portal's event
loop, so use it from threads, not from a coroutine of your own loop.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import click
import grpc
from anyio import BrokenResourceError, ClosedResourceError, EndOfStream

from .common import (
    BUTTON_AUXILIARY,
    BUTTON_BACK,
    BUTTON_FORWARD,
    BUTTON_PRIMARY,
    BUTTON_SECONDARY,
    CODECS,
    KIND_CONFIG,
    KIND_MEDIA,
    MAX_ROTARY_DELTA,
    FrameParser,
    LineBuffer,
    LineTooLongError,
)
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
                    "the HMI stream ended before it said what it carries", self.end_error
                ) from self.end_error
            kind, body = self._pending[0]
            if kind != KIND_CONFIG:
                raise HmiStreamClosedError("the HMI stream did not start with its codec line", reason=ERROR)
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
        picture is not silent for long: the encoder repeats the last one).
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


#: The mouse buttons by name (and the names a browser and a desktop use): their bits of ``PointerEvent.buttons``.
BUTTONS = {
    "primary": BUTTON_PRIMARY,
    "left": BUTTON_PRIMARY,
    "secondary": BUTTON_SECONDARY,
    "right": BUTTON_SECONDARY,
    "auxiliary": BUTTON_AUXILIARY,
    "middle": BUTTON_AUXILIARY,
    "back": BUTTON_BACK,
    "forward": BUTTON_FORWARD,
}


class InputNotDeclaredError(ValueError):
    """A call needs input the HMI does not declare in ``hmi_info()["input"]``: nothing was sent."""


def declares_input(info: dict) -> bool:
    """Whether ``hmi_info()`` declares any input at all: a pointer, a keyboard (keys or text), a hard key or a rotary. A
    view-only HMI declares none.
    """
    declared = info.get("input") or {}
    pointer, keyboard = declared.get("pointer") or {}, declared.get("keyboard") or {}
    return bool(
        pointer.get("kinds")
        or keyboard.get("codes")
        or keyboard.get("text")
        or declared.get("keys")
        or declared.get("rotaries")
    )


class InputStream(_Receiving):
    """The ``input`` stream: pointer, keyboard, key, rotary and text events. Obtained from :meth:`HmiClient.input`.

    The exporter answers a line it could not take with ``{"error": "..."}``: :meth:`read_error` (blocking) and
    :meth:`poll_errors` give them, or pass ``on_error`` to :meth:`HmiClient.input`. Use one of the three, not several:
    they read the same stream.

    **Raw events** (``send``, ``touch``, ``pointer``, ``keyboard``) and ``key`` (a hard key by name) go out as they are
    given, and the exporter answers one the HMI does not take. **Helpers** (``tap``, ``swipe``, ``long_press``,
    ``click``, ``double_click``, ``move``, ``move_by``, ``drag``, ``scroll``, ``key_down``, ``key_up``, ``press``,
    ``rotate``, ``push``, ``text``) are made of them and check, before sending anything, that the HMI declares what
    they need: :class:`InputNotDeclaredError` otherwise. A stream made without ``hmi_info`` input (``input_info``
    ``None``) checks nothing.
    """

    def __init__(
        self,
        raw: Any,
        input_info: dict | None = None,
        size: tuple[int, int] | Callable[[], tuple[int, int] | None] | None = None,
    ) -> None:
        """Wrap the raw blocking stream. ``input_info`` is ``hmi_info()["input"]``; ``size`` is the shown picture's
        ``(width, height)``, or a function that finds it when it is needed (a ``scroll`` with no point): the picture
        may not be there yet when the stream is opened.
        """
        self._raw = raw
        self._lines = LineBuffer()
        self._errors: deque[str] = deque()
        self._eof = False
        self._input = input_info
        self._size = size
        #: Every error the exporter has answered with, in order.
        self.errors: list[str] = []
        self.end_reason: str | None = None
        self.end_error: BaseException | None = None
        #: Where the pointer was last put by this stream (``None`` until it has been): where a ``scroll`` without a
        #: position turns the wheel.
        self.position: tuple[float, float] | None = None

    # -- raw events ----------------------------------------------------------------------------------------------------

    def send(self, event: dict) -> None:
        """One raw event object: ``{"pointer": {...}}``, ``{"touch": {...}}``, ``{"keyboard": {...}}``, ``{"key":
        {...}}``, ``{"rotary": {...}}`` or ``{"text": "..."}``.

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
        picture can change under the viewer, and a touch aimed before a rotation is refused instead of landing
        somewhere else.
        """
        event: dict = {"type": kind, "x": x, "y": y, "id": id}
        if size is not None:
            event["w"], event["h"] = size
        self.position = (x, y)
        self.send({"touch": event})

    def pointer(
        self,
        type: str,
        x: float | None = None,
        y: float | None = None,
        *,
        kind: str = "touch",
        id: int = 0,
        buttons: int | None = None,
        dx: float = 0.0,
        dy: float = 0.0,
        size: tuple[float, float] | None = None,
    ) -> None:
        """A pointer event, as the wire has it: ``type`` is ``down``, ``move``, ``up`` or ``wheel``, ``kind`` is
        ``touch``, ``mouse`` or ``pen``, ``buttons`` is the state of the buttons after the event
        (``PointerEvent.buttons``; left out: the primary button for a ``down``, what is held for a ``move``), ``dx`` and
        ``dy`` are the wheel's deltas, or the relative motion of a ``move`` that has no ``x`` and ``y``.
        """
        event: dict = {"type": type, "kind": kind, "id": id}
        if x is not None and y is not None:
            event["x"], event["y"] = x, y
            self.position = (x, y)
        if buttons is not None:
            event["buttons"] = buttons
        if dx:
            event["dx"] = dx
        if dy:
            event["dy"] = dy
        if size is not None:
            event["w"], event["h"] = size
        self.send({"pointer": event})

    def keyboard(self, code: str, down: bool) -> None:
        """A key going down or up, by its W3C ``KeyboardEvent.code`` (``KeyA``, ``Digit1``, ``ArrowLeft``...)."""
        self.send({"keyboard": {"code": code, "type": "down" if down else "up"}})

    # -- what the HMI declares -----------------------------------------------------------------------------------------

    def _pointer_kind(self, *wanted: str, need: str = "a pointer") -> str:
        """The first of the pointer ``wanted`` kinds the HMI declares (the first of them when nothing is known).

        Raises:
            InputNotDeclaredError: If the HMI declares none of them.
        """
        if self._input is None:
            return wanted[0]
        kinds = self._input["pointer"]["kinds"]
        for kind in wanted:
            if kind in kinds:
                return kind
        if not kinds:
            raise InputNotDeclaredError(f"this HMI declares no pointer, so there is nothing to {need}")
        raise InputNotDeclaredError(
            f"this HMI's pointer is {' and '.join(kinds)}, not {' or '.join(wanted)}: it cannot {need}"
        )

    def _pointer_has(self, feature: str, what: str) -> None:
        if self._input is not None and not self._input["pointer"][feature]:
            raise InputNotDeclaredError(f"this HMI's pointer takes no {what}")

    @staticmethod
    def _button(button: str | int) -> int:
        if isinstance(button, int) and not isinstance(button, bool) and 0 < button <= 63:
            return button
        bit = BUTTONS.get(button.lower()) if isinstance(button, str) else None
        if bit is None:
            raise ValueError(
                f"a button is one of {', '.join(BUTTONS)} (or the bits of PointerEvent.buttons): {button!r}"
            )
        return bit

    def _checked_button(self, button: str | int) -> int:
        bit = self._button(button)
        if bit != BUTTON_PRIMARY:
            self._pointer_has("buttons", "button but the primary one")
        return bit

    # -- the pointer ---------------------------------------------------------------------------------------------------

    def _down(self, kind: str, x: float, y: float, bit: int, size: tuple[float, float] | None) -> None:
        if kind == "touch":
            self.touch("down", x, y, size=size)
        else:
            self.pointer("down", x, y, kind=kind, buttons=bit, size=size)

    def _move(self, kind: str, x: float, y: float, bit: int, size: tuple[float, float] | None) -> None:
        if kind == "touch":
            self.touch("move", x, y, size=size)
        else:
            self.pointer("move", x, y, kind=kind, buttons=bit, size=size)

    def _up(self, kind: str, x: float, y: float, size: tuple[float, float] | None) -> None:
        if kind == "touch":
            self.touch("up", x, y, size=size)
        else:
            self.pointer("up", x, y, kind=kind, buttons=0, size=size)

    def _drag(
        self,
        kind: str,
        start: tuple[float, float],
        end: tuple[float, float],
        bit: int,
        seconds: float,
        steps: int,
        size: tuple[float, float] | None,
    ) -> None:
        (x1, y1), (x2, y2) = start, end
        self._down(kind, x1, y1, bit, size)
        for i in range(1, steps + 1):
            time.sleep(seconds / steps)
            self._move(kind, x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps, bit, size)
        self._up(kind, x2, y2, size)

    def tap(self, x: float, y: float, hold: float = 0.05, size: tuple[float, float] | None = None) -> None:
        """Down and up at one point: a finger on a touch HMI, a click of the primary button on a mouse HMI (a pen's tip
        on a pen's).

        Raises:
            InputNotDeclaredError: If the HMI declares no pointer.
        """
        kind = self._pointer_kind("touch", "mouse", "pen", need="tap")
        self._down(kind, x, y, BUTTON_PRIMARY, size)
        time.sleep(hold)
        self._up(kind, x, y, size)

    def long_press(self, x: float, y: float, seconds: float = 1.0, size: tuple[float, float] | None = None) -> None:
        """Hold down at one point for ``seconds`` (as :meth:`tap`, with the finger or button kept down)."""
        self.tap(x, y, hold=seconds, size=size)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        seconds: float = 0.3,
        steps: int = 12,
        size: tuple[float, float] | None = None,
    ) -> None:
        """Drag from one point to another: a finger on a touch HMI, the primary button on a mouse HMI.

        Raises:
            InputNotDeclaredError: If the HMI declares no pointer.
        """
        kind = self._pointer_kind("touch", "mouse", "pen", need="swipe")
        self._drag(kind, (x1, y1), (x2, y2), BUTTON_PRIMARY, seconds, steps, size)

    def click(
        self,
        x: float,
        y: float,
        button: str | int = "primary",
        hold: float = 0.05,
        size: tuple[float, float] | None = None,
    ) -> None:
        """Press and release a mouse button at one point. ``button`` is ``primary`` (``left``), ``secondary``
        (``right``), ``auxiliary`` (``middle``), ``back`` or ``forward``.

        Raises:
            InputNotDeclaredError: If the HMI declares no mouse, or no buttons beyond the primary one.
        """
        kind = self._pointer_kind("mouse", need="click (use tap on a touch screen)")
        bit = self._checked_button(button)
        self._down(kind, x, y, bit, size)
        time.sleep(hold)
        self._up(kind, x, y, size)

    def double_click(
        self,
        x: float,
        y: float,
        button: str | int = "primary",
        interval: float = 0.08,
        size: tuple[float, float] | None = None,
    ) -> None:
        """Two clicks, ``interval`` seconds apart (the same as :meth:`click`)."""
        self.click(x, y, button, hold=0.03, size=size)
        time.sleep(interval)
        self.click(x, y, button, hold=0.03, size=size)

    def move(self, x: float, y: float, size: tuple[float, float] | None = None) -> None:
        """Put the mouse at a point with no button down: a hover.

        Raises:
            InputNotDeclaredError: If the HMI declares no mouse (or pen), or it takes only relative motion.
        """
        kind = self._pointer_kind("mouse", "pen", need="hover (a touch screen has no hover)")
        self._pointer_has("absolute", "position (only relative motion: use move_by)")
        self.pointer("move", x, y, kind=kind, size=size)

    def move_by(self, dx: float, dy: float) -> None:
        """Move the mouse by ``dx``, ``dy`` pixels from where it is, with no position said (relative motion).

        Raises:
            InputNotDeclaredError: If the HMI declares no mouse, or no relative motion.
        """
        kind = self._pointer_kind("mouse", need="move by a distance")
        self._pointer_has("relative", "relative motion")
        self.pointer("move", kind=kind, dx=dx, dy=dy)

    def drag(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        button: str | int = "primary",
        seconds: float = 0.3,
        steps: int = 12,
        size: tuple[float, float] | None = None,
    ) -> None:
        """Press a mouse button at one point, move to another with it held, and let go (use :meth:`swipe` on a touch
        screen).

        Raises:
            InputNotDeclaredError: If the HMI declares no mouse (or pen), or no buttons beyond the primary one.
        """
        kind = self._pointer_kind("mouse", "pen", need="drag with a button (use swipe on a touch screen)")
        self._drag(kind, (x1, y1), (x2, y2), self._checked_button(button), seconds, steps, size)

    def scroll(
        self,
        dx: float,
        dy: float,
        x: float | None = None,
        y: float | None = None,
        size: tuple[float, float] | None = None,
    ) -> None:
        """Turn the wheel by ``dx``, ``dy`` pixels at a point, with the sign of ``WheelEvent.deltaX`` and ``deltaY``
        (positive ``dy`` scrolls down). The point is where the pointer last was, or the center of the picture.

        Raises:
            InputNotDeclaredError: If the HMI declares no pointer with a wheel.
            ValueError: If there is no point to scroll at.
        """
        kind = self._pointer_kind("mouse", "pen", "touch", need="scroll")
        self._pointer_has("wheel", "wheel")
        if x is None or y is None:
            if self.position is not None:
                x, y = self.position
            elif (shown := self._shown_size()) is not None:
                x, y = shown[0] / 2, shown[1] / 2
            else:
                raise ValueError("scroll needs x and y: the picture's size is not known yet and no pointer was put")
        self.pointer("wheel", x, y, kind=kind, dx=dx, dy=dy, size=size)

    def _shown_size(self) -> tuple[int, int] | None:
        """The shown picture's size, if it is known."""
        size = self._size
        return size if size is None or isinstance(size, tuple) else size()

    # -- the keyboard, hard keys and text ------------------------------------------------------------------------------

    def _code(self, code: str) -> str:
        if self._input is not None:
            keyboard = self._input["keyboard"]
            codes = keyboard["codes"]
            if not codes:
                raise InputNotDeclaredError(
                    "this HMI takes no key codes" + (" (it types text: use text)" if keyboard["text"] else "")
                )
            if codes != "all" and code not in codes:
                raise InputNotDeclaredError(f"this HMI does not take the key {code}")
        return code

    def key_down(self, code: str) -> None:
        """Press a keyboard key and keep it down, by its W3C ``KeyboardEvent.code`` (``KeyA``, ``ShiftLeft``...).

        Raises:
            InputNotDeclaredError: If the HMI does not take the key.
        """
        self.keyboard(self._code(code), True)

    def key_up(self, code: str) -> None:
        """Let go of a keyboard key (the exporter drops it if the key was not down).

        Raises:
            InputNotDeclaredError: If the HMI does not take the key.
        """
        self.keyboard(self._code(code), False)

    def press(self, code: str, modifiers: Sequence[str] = (), hold: float = 0.02) -> None:
        """Press and release a keyboard key, with ``modifiers`` (``("ShiftLeft",)``, ``("ControlLeft", "AltLeft")``...)
        held while it is: they go down in order, then the key, and up in the reverse order. Nothing is sent if the HMI
        does not take every one of the keys.

        Raises:
            InputNotDeclaredError: If the HMI does not take one of the keys.
        """
        keys = [self._code(modifier) for modifier in modifiers]
        key = self._code(code)
        for modifier in keys:
            self.keyboard(modifier, True)
        self.keyboard(key, True)
        time.sleep(hold)
        self.keyboard(key, False)
        for modifier in reversed(keys):
            self.keyboard(modifier, False)

    def key(self, key: str, press: str = "short") -> None:
        """A hard key by name (``HOME``, ``BACK``, ``APP_SWITCH``, ``VOLUME_UP``...; ``hmi_info()`` lists them):
        ``press`` is ``short``, ``long``, ``down`` or ``up``.

        It is sent as it is: a key the HMI does not have is answered by the exporter with an error line (see
        :meth:`read_error`, :meth:`poll_errors`).
        """
        self.send({"key": {"key": key, "press": press}})

    def _rotary(self, rotary_id: str) -> dict:
        """What the HMI declares of the rotary ``rotary_id`` (``{"id", "label", "push"}``).

        Raises:
            InputNotDeclaredError: If the HMI has no such rotary.
        """
        if self._input is None:
            return {"id": rotary_id, "push": None}  # nothing is known: the exporter is the one to say
        rotaries = self._input.get("rotaries") or []
        for rotary in rotaries:
            if rotary["id"] == rotary_id:
                return rotary
        if not rotaries:
            raise InputNotDeclaredError("this HMI declares no rotary")
        raise InputNotDeclaredError(
            f"this HMI has no rotary {rotary_id} (it has {', '.join(rotary['id'] for rotary in rotaries)})"
        )

    def rotate(self, rotary_id: str, delta: int) -> None:
        """Turn a rotary knob by ``delta`` detents, positive clockwise (``hmi_info()`` lists the rotaries). A turn of
        more than ``MAX_ROTARY_DELTA`` detents is sent as several lines.

        Raises:
            InputNotDeclaredError: If the HMI has no such rotary.
            ValueError: If ``delta`` is not a whole number of detents other than 0.
        """
        self._rotary(rotary_id)
        if (
            isinstance(delta, bool)
            or not isinstance(delta, int | float)
            or not math.isfinite(delta)
            or delta != int(delta)
            or delta == 0
        ):
            raise ValueError(f"a rotary turns by a whole number of detents, not 0: {delta!r}")
        remaining, sign = abs(int(delta)), 1 if delta > 0 else -1
        while remaining:
            step = min(remaining, MAX_ROTARY_DELTA)
            self.send({"rotary": {"id": rotary_id, "delta": sign * step}})
            remaining -= step

    def push(self, rotary_id: str, press: str = "short") -> None:
        """Push a rotary knob: its push button is the hard key the HMI names for it, pressed as ``press`` says
        (``short``, ``long``, ``down`` or ``up``, as far as the key takes them).

        Raises:
            InputNotDeclaredError: If the HMI has no such rotary, or it has no push button.
        """
        if self._input is None:
            raise InputNotDeclaredError("the push button of a rotary is named by hmi_info(): this stream has none")
        key = self._rotary(rotary_id)["push"]
        if key is None:
            raise InputNotDeclaredError(f"the rotary {rotary_id} has no push button")
        self.key(key, press)

    def text(self, text: str) -> None:
        """Type text into the focused field, in the layout the HMI declares.

        Raises:
            InputNotDeclaredError: If the HMI does not type text.
        """
        if self._input is not None and not self._input["keyboard"]["text"]:
            raise InputNotDeclaredError("this HMI does not take text")
        self.send({"text": text})

    # -- what the exporter answers -------------------------------------------------------------------------------------

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

    #: Marks the class as an HMI of the HMI interface (``video()``, ``input()``): a UI server that finds it anywhere in
    #: a lease's driver tree can relay it, whatever the child is called.
    HMI_STREAM_VERSION: ClassVar[int] = 1

    def start(
        self,
        quality: str | int | None = None,
        max_size: int | None = None,
        bit_rate: int | None = None,
        max_fps: int | None = None,
        codec: str | None = None,
    ) -> dict:
        """Start the stream if it is not running (joining ``video`` or ``input`` does it too); returns the status.

        ``codec`` (``h264``, ``hevc`` or ``mjpeg``: ``hmi_info()["codecs"]`` lists what the HMI produces) chooses the
        stream's codec for this lease: a running stream is restarted in it, and its viewers are sent the new codec line.

        Raises:
            ValueError: If the HMI does not produce ``codec``, or its quality cannot be adjusted.
        """
        return _integers(self.call("start", quality, max_size, bit_rate, max_fps, codec))

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
            ValueError: If the HMI's quality cannot be adjusted.
        """
        return _integers(self.call("set_quality", level))

    def hmi_info(self) -> dict:
        """What the HMI is and what it takes: the ``codecs`` it produces and the ``codec`` in use, whether it is
        ``recording``-capable, the picture's size, and ``input``: its ``pointer``, ``keyboard``, ``keys``, ``hard_keys``
        and ``clipboard``, all of them empty for a view-only HMI.
        """
        return _integers(self.call("hmi_info"))

    def start_recording(self, audio: bool = False, mic_audio: bool = False, max_seconds: float | None = None) -> dict:
        """Record the stream to an MP4 on the exporter (``audio`` is accepted and ignored: there is none); returns the
        status. H.264 and HEVC are recorded; a stream that goes on in another codec ends the recording
        (``reason`` ``codec_changed``).

        Raises:
            DriverError: If the stream is MJPEG (``hmi_info()["recording"]`` is false), which is not recorded.
        """
        return _integers(self.call("start_recording", audio, mic_audio, max_seconds))

    def recording_status(self) -> dict:
        """What the recording is doing: ``{"active", "seconds", "frames", "bytes", "reason", "error"}``. ``reason`` is
        ``None`` while it runs, then ``stop``, ``max_seconds``, ``max_bytes``, ``stream_ended``, ``codec_changed`` (the
        stream went on in another codec) or ``error``.
        """
        return _integers(self.call("recording_status"))

    def stop_recording(self) -> dict:
        """End the recording and fetch it: ``{"data": <the MP4's bytes>, "name", "size", "delivered", "seconds",
        "frames", "width", "height", "reason", ...}`` (``reason`` as in :meth:`recording_status`).
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
                raise HmiStreamClosedError.of("the HMI stream could not be opened", e) from e
            yield video

    @contextmanager
    def input(self, on_error: Callable[[str], None] | None = None) -> Generator[InputStream]:
        """Open the input stream: pointer, keyboard, key and text events to the device.

        ``on_error`` is called (on a thread of its own) with the message of every event the exporter could not take, as
        they come,
        so that a relay can pass them on to its UI.

        Raises:
            HmiStreamClosedError: If the stream could not be opened, or the HMI takes no input from here (it declares
                none: a view-only HMI; ``reason`` as for :meth:`video`).
        """
        info = self.hmi_info()
        if not declares_input(info):
            raise HmiStreamClosedError("this HMI takes no input from here", reason=ERROR)

        def shown() -> tuple[int, int] | None:
            now = self.hmi_info()
            return (now["width"], now["height"]) if now.get("width") and now.get("height") else None

        with ExitStack() as stack:
            try:
                stream = InputStream(stack.enter_context(self.stream("input")), info["input"], shown)
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
        """Write ``seconds`` of the stream to ``path`` as a raw elementary stream; returns what was written.

        H.264 and HEVC are written as Annex-B (``.h264``, ``.h265``) starting at the first key picture (parameter sets
        included), so the file plays from its first picture; MJPEG as the JPEGs one after the other (``.mjpeg``). Raw
        video has no timing: ``ffmpeg -framerate <fps> -i file.h264 -c copy file.mp4`` makes an MP4 of it (the ``fps``
        of the result is nominal). The name is the caller's: the ``codec`` of the result says what was written.
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
        """The command group of the HMI (``j <hmi> ...``)."""
        client = self

        def inputs(action: Callable[[InputStream], None]) -> None:
            """Open the input stream, run ``action`` on it and report what the exporter answered with."""
            try:
                with client.input() as hmi_input:
                    action(hmi_input)
                    for error in hmi_input.poll_errors(0.5):
                        raise click.ClickException(error)
            except (InputNotDeclaredError, HmiStreamClosedError, ValueError) as e:
                raise click.ClickException(str(e)) from None

        codec_option = click.option(
            "--codec", type=click.Choice(CODECS), default=None, help="the stream's codec for this lease (see `info`)"
        )

        @driver_click_group(self)
        def hmi():
            """The device's HMI: its picture, and the pointer, keyboard, keys and knobs it takes, from the exporter."""

        @hmi.command("status")
        def status_command():
            """What the stream is doing: state, picture, measured fps and bit rate, viewers and what each lost."""
            click.echo(json.dumps(client.status(), indent=2))

        @hmi.command("info")
        @click.option("--json", "as_json", is_flag=True, help="the raw hmi_info() as JSON")
        def info_command(as_json):
            """What the HMI is and takes: its codecs, pointer, keyboard, hard keys, rotaries and quality."""
            info = client.hmi_info()
            click.echo(json.dumps(info, indent=2) if as_json else describe(info))

        @hmi.command("start")
        @click.option("--quality", default=None, help="auto, minimal, low, medium or high (or 0-3)")
        @click.option("--max-size", type=int, default=None, help="longest side of the picture, pixels")
        @click.option("--bit-rate", type=int, default=None, help="video bit rate, bit/s")
        @click.option("--max-fps", type=int, default=None)
        @codec_option
        def start_command(quality, max_size, bit_rate, max_fps, codec):
            """Start the stream now (joining it does that too), in a codec of choice."""
            try:
                click.echo(json.dumps(client.start(quality, max_size, bit_rate, max_fps, codec), indent=2))
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
            """Record the stream on the exporter to an MP4 (muxed there, no transcode) and fetch it (H.264 and HEVC)."""
            info = client.hmi_info()
            if not info["recording"]:
                raise click.ClickException(
                    f"a {info['codec']} stream is not recorded: start it as h264 or hevc (`start --codec`) first"
                )
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
            """Record the stream to a raw file (Annex-B for h264 and hevc, JPEGs for mjpeg), from a key picture."""
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
            """Press a hard key of the HMI: HOME, BACK, APP_SWITCH, VOLUME_UP, ... (see `info` for what it has)."""
            inputs(lambda hmi_input: hmi_input.key(key, press))

        @hmi.command("turn", context_settings={"ignore_unknown_options": True})  # a negative DELTA is not an option
        @click.argument("rotary_id", metavar="ID")
        @click.argument("delta", type=int)
        def turn_command(rotary_id, delta):
            """Turn the rotary knob ID by DELTA detents (positive is clockwise; see `info` for the rotaries)."""
            inputs(lambda hmi_input: hmi_input.rotate(rotary_id, delta))

        @hmi.command("push")
        @click.argument("rotary_id", metavar="ID")
        @click.option("--press", type=click.Choice(["short", "long", "down", "up"]), default="short")
        def push_command(rotary_id, press):
            """Push the rotary knob ID (its push button is a hard key; see `info` for the rotaries)."""
            inputs(lambda hmi_input: hmi_input.push(rotary_id, press))

        @hmi.command("press")
        @click.argument("code")
        @click.option(
            "--modifier", "modifiers", multiple=True, help="a key held meanwhile, e.g. ShiftLeft (repeatable)"
        )
        def press_command(code, modifiers):
            """Press a keyboard key by its W3C code (KeyA, Digit1, Enter, ArrowLeft, ...), with modifiers held."""
            inputs(lambda hmi_input: hmi_input.press(code, modifiers=modifiers))

        @hmi.command("text")
        @click.argument("text")
        def text_command(text):
            """Type text into the focused field."""
            inputs(lambda hmi_input: hmi_input.text(text))

        @hmi.command("tap")
        @click.argument("x", type=float)
        @click.argument("y", type=float)
        def tap_command(x, y):
            """Tap at a pixel of the picture (a click of the primary button on an HMI with a mouse)."""
            inputs(lambda hmi_input: hmi_input.tap(x, y))

        @hmi.command("swipe")
        @click.argument("x1", type=float)
        @click.argument("y1", type=float)
        @click.argument("x2", type=float)
        @click.argument("y2", type=float)
        @click.option("--seconds", type=click.FloatRange(min=0.0), default=0.3, show_default=True)
        def swipe_command(x1, y1, x2, y2, seconds):
            """Drag from one pixel of the picture to another."""
            inputs(lambda hmi_input: hmi_input.swipe(x1, y1, x2, y2, seconds=seconds))

        @hmi.command("click")
        @click.argument("x", type=float)
        @click.argument("y", type=float)
        @click.option("--button", type=click.Choice(list(BUTTONS)), default="primary", show_default=True)
        @click.option("--double", is_flag=True, help="click twice")
        def click_command(x, y, button, double):
            """Click a mouse button at a pixel of the picture."""
            if double:
                inputs(lambda hmi_input: hmi_input.double_click(x, y, button))
            else:
                inputs(lambda hmi_input: hmi_input.click(x, y, button))

        @hmi.command(
            "scroll", context_settings={"ignore_unknown_options": True}
        )  # a negative DX or DY is not an option
        @click.argument("dx", type=float)
        @click.argument("dy", type=float)
        @click.option("--at", nargs=2, type=float, default=None, help="X Y: where to turn the wheel (default: center)")
        def scroll_command(dx, dy, at):
            """Turn the mouse wheel by DX, DY pixels (positive DY scrolls down)."""
            x, y = at if at is not None else (None, None)
            inputs(lambda hmi_input: hmi_input.scroll(dx, dy, x, y))

        @hmi.command("view")
        @click.option("--port", type=click.IntRange(0, 65535), default=0, show_default=True, help="0: any free port")
        @click.option("--no-open", is_flag=True, help="do not open the browser")
        @codec_option
        def view_command(port, no_open, codec):
            """Watch and control the HMI in a browser page served from this machine (127.0.0.1 only).

            The address it prints carries a random token the page needs for its stream and its input. ``--codec``
            starts the stream in that codec first.
            """
            from .web import serve_view

            if codec is not None:
                try:
                    client.start(codec=codec)
                except ValueError as e:
                    raise click.ClickException(str(e)) from None
            serve_view(client, port=port, open_browser=not no_open)

        return hmi


def describe(info: dict) -> str:
    """``hmi_info()`` as a person reads it: the codecs, the picture, and what the HMI takes (and what it does not)."""
    lines = []
    codecs = ", ".join(c if c != info["codec"] else f"{c} (in use)" for c in info["codecs"])
    size = f"{info['width']}x{info['height']}" if info.get("width") else "not started"
    lines.append(f"picture: {size}, rotation {info.get('rotation', 0)}")
    lines.append(f"codecs: {codecs}; {'recorded to MP4' if info['recording'] else 'not recorded'}")
    declared = info["input"]
    pointer, keyboard = declared["pointer"], declared["keyboard"]
    if pointer["kinds"]:
        how = [name for name in ("absolute", "relative") if pointer[name]]
        extras = [name for name in ("buttons", "wheel") if pointer[name]]
        contacts = f"{pointer['max_contacts']} contact{'s' if pointer['max_contacts'] != 1 else ''}"
        lines.append(f"pointer: {', '.join(pointer['kinds'])}; {' and '.join(how)}; {contacts}")
        lines.append(f"  with {', '.join(extras) if extras else 'no buttons or wheel'}")
    else:
        lines.append("pointer: none")
    if keyboard["codes"] or keyboard["text"]:
        codes = "every key" if keyboard["codes"] == "all" else ", ".join(keyboard["codes"]) or "no key codes"
        lines.append(
            f"keyboard: {codes}; {'types text' if keyboard['text'] else 'no text'} ({keyboard['layout']} layout)"
        )
    else:
        lines.append("keyboard: none")
    if declared["hard_keys"]:
        lines.append("hard keys: " + ", ".join(f"{k['key']} ({'/'.join(k['presses'])})" for k in declared["hard_keys"]))
    else:
        lines.append("hard keys: none")
    rotaries = declared.get("rotaries") or []
    pushes = {rotary["push"] for rotary in rotaries if rotary["push"]}
    other = [key for key in declared["keys"] if key not in {k["key"] for k in declared["hard_keys"]} | pushes]
    if other:
        lines.append("  also takes: " + ", ".join(other))
    if rotaries:
        lines.append(
            "rotaries: "
            + ", ".join(
                f"{rotary['id']} ({rotary['label']}" + (f", push {rotary['push']})" if rotary["push"] else ")")
                for rotary in rotaries
            )
        )
    else:
        lines.append("rotaries: none")
    lines.append(f"clipboard: {'yes' if declared['clipboard'] else 'no'}")
    quality = info["quality"]
    lines.append(
        "quality: "
        + (f"{', '.join(quality['levels'])}{', auto' if quality['auto'] else ''}" if quality["adjustable"] else "fixed")
    )
    if not declares_input(info):
        lines.append("input: none (a view-only HMI)")
    return "\n".join(lines)
