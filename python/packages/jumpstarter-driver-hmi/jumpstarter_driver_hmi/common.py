"""The HMI interface's wire format and vocabulary: pure code, no Jumpstarter, no I/O.

**The video stream** (what ``video()`` carries). A Jumpstarter byte stream has no message boundaries and an Annex-B
access unit has no
length, so the exporter frames what it sends::

    frame  = u32 big-endian N  +  u8 kind  +  body[N - 1]
    kind 0 = config: body is one JSON line (UTF-8, ends with a newline), the codec line
    kind 1 = media:  body is an 8-byte big-endian timestamp in microseconds + ONE H.264 Annex-B access unit
    kind 2-15        reserved: a reader skips a kind it does not know

Without the 5-byte transport header, a ``kind 0`` body is the codec line and a ``kind 1`` body is the "8-byte timestamp
+ access unit"
that a relay can pass to a WebSocket as a text and a binary frame, one to one. The first frame is always a config frame;
a SPS/PPS + IDR
access unit follows it; the line is sent again whenever anything in it changes.

**The input stream** (what ``input()`` takes): one JSON object per line, in the order sent::

    {"touch": {"type": "down" | "move" | "up" | "cancel", "x": 540, "y": 1200, "id": 0, "w": 1206, "h": 2622}}
    {"key": {"key": "HOME", "press": "short" | "long" | "down" | "up"}}
    {"text": "hello"}

Coordinates are pixels of the **shown** picture (the codec line's size, turned by its ``rotation``). One contact is
tracked: ``id`` is
always 0 and any other id is ignored. ``w`` and ``h`` name the picture the touch was aimed at; a touch aimed at another
size is refused.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any, cast

KIND_CONFIG = 0
KIND_MEDIA = 1
#: Largest frame a reader accepts (an IDR of a phone screen is a few hundred kB; this guards against a corrupt length).
MAX_FRAME_BYTES = 64 * 1024 * 1024
#: Version of the wire format, in every codec line.
WIRE_VERSION = 1
#: Touches and keys are small; a line longer than this is not one. (A pasted text is the long one: see
#: ``MAX_TEXT_CHARS``.)
MAX_LINE_BYTES = 128 * 1024
#: Most characters of one ``text`` event.
MAX_TEXT_CHARS = 16384

_HEADER = struct.Struct(">IB")
_TIMESTAMP = struct.Struct(">Q")


# -- framing -----------------------------------------------------------------------------------------------------------


def pack_frame(kind: int, body: bytes) -> bytes:
    """One transport frame: ``u32 length-of-(kind+body)``, ``u8 kind``, ``body``."""
    return _HEADER.pack(len(body) + 1, kind) + body


def pack_media(ts_us: int, access_unit: bytes) -> bytes:
    """A media frame: the 8-byte big-endian microsecond timestamp followed by the access unit."""
    return pack_frame(KIND_MEDIA, _TIMESTAMP.pack(ts_us) + access_unit)


def split_media(body: bytes) -> tuple[int, bytes]:
    """``(timestamp in µs, access unit)`` of a media frame's body."""
    if len(body) < _TIMESTAMP.size:
        raise ValueError("media frame shorter than its timestamp")
    return _TIMESTAMP.unpack_from(body)[0], body[_TIMESTAMP.size :]


class FrameParser:
    """Reads transport frames out of a byte stream, however it was chunked."""

    def __init__(self) -> None:
        """Start with nothing buffered."""
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        """``[(kind, body)]`` for every frame ``data`` completed. ``ValueError`` on a length that cannot be right."""
        self._buf += data
        frames: list[tuple[int, bytes]] = []
        while len(self._buf) >= _HEADER.size:
            length, kind = _HEADER.unpack_from(self._buf)
            if length < 1 or length > MAX_FRAME_BYTES:
                raise ValueError(f"screen stream frame of {length} bytes: the stream is out of step")
            end = 4 + length
            if len(self._buf) < end:
                break
            frames.append((kind, bytes(self._buf[_HEADER.size : end])))
            del self._buf[:end]
        return frames


class LineBuffer:
    """Splits a byte stream into lines, however it was chunked, refusing a line that is too long.

    The limit is on **whole lines**: a long line that arrives complete in one chunk is refused as one that never ends
    is, and what follows it is still read. The rest of a refused line, up to its newline, is thrown away, not read as a
    line of its own.
    """

    def __init__(self, limit: int = MAX_LINE_BYTES) -> None:
        """Refuse a line longer than ``limit`` bytes."""
        self._buf = bytearray()
        self._limit = limit
        self._discarding = False

    def feed(self, data: bytes) -> list[bytes]:
        """The complete, non-empty lines ``data`` finished. ``ValueError`` for a line over the limit (the lines before
        it are kept).
        """
        self._buf += data
        lines: list[bytes] = []
        too_long = False
        while (end := self._buf.find(b"\n")) >= 0:
            raw = bytes(self._buf[:end])
            del self._buf[: end + 1]
            if self._discarding:  # the tail of a line that was refused: nothing of it is a line
                self._discarding = False
                continue
            if len(raw) > self._limit:
                too_long = True
                continue
            line = raw.strip()
            if line:
                lines.append(line)
        if len(self._buf) > self._limit:
            self._buf.clear()
            self._discarding = True
            too_long = True
        if too_long:
            raise LineTooLongError(f"a line longer than {self._limit} bytes", lines)
        return lines


class LineTooLongError(ValueError):
    """A line was over the limit; ``lines`` are the good lines that were complete in the same chunk."""

    def __init__(self, message: str, lines: list[bytes]) -> None:
        """Say what is wrong and keep the good lines."""
        super().__init__(message)
        self.lines = lines


# -- the codec line ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CodecLine:
    """What a viewer needs to decode and show the stream: the codec string, the picture's visible (and coded) size and
    the nominal frame rate.

    ``rotation``, ``point_scale`` and ``input`` are optional (``None``: not said) and ignored by a client that does not
    know them.
    """

    codec: str
    width: int
    height: int
    fps: int = 0
    #: The coded size, when the encoder pads the picture (``None``: the same as the visible one).
    coded_width: int | None = None
    coded_height: int | None = None
    #: The clockwise angle (0, 90, 180 or 270) the decoded picture must be turned to be upright.
    rotation: int | None = None
    #: Pixels of the picture to a point of the device's screen.
    point_scale: int | float | None = None
    #: Where touch goes: ``stream`` (this driver's ``input()``) or another word the source says; ``None`` leaves the
    #: field out.
    input: str | None = None

    def __post_init__(self) -> None:
        """Refuse a line that cannot be right."""
        if (
            not isinstance(self.codec, str)
            or not 1 <= len(self.codec) <= 64
            or not self.codec.isascii()
            or not self.codec.isprintable()
        ):
            raise ValueError(f"the codec must be a codec string: {self.codec!r}")
        for name in ("width", "height"):
            _whole(name, getattr(self, name), 1, 16384)
        for name in ("coded_width", "coded_height"):
            if getattr(self, name) is not None:
                _whole(name, getattr(self, name), 1, 16384)
        if isinstance(self.fps, bool) or not isinstance(self.fps, int | float) or not 0 <= self.fps <= 1000:
            raise ValueError(f"fps must be a number from 0 to 1000: {self.fps!r}")
        if self.rotation is not None and self.rotation not in (0, 90, 180, 270):
            raise ValueError(f"rotation must be 0, 90, 180 or 270: {self.rotation!r}")
        if self.point_scale is not None and (
            isinstance(self.point_scale, bool)
            or not isinstance(self.point_scale, int | float)
            or not 0 < self.point_scale <= 64
        ):
            raise ValueError(f"point_scale must be a number above 0 and at most 64: {self.point_scale!r}")

    @property
    def shown_size(self) -> tuple[int, int]:
        """``(width, height)`` of the picture a viewer shows: the visible size turned by ``rotation`` (90 and 270 swap
        the two).

        It is the picture a touch is aimed at.
        """
        if self.rotation in (90, 270):
            return self.height, self.width
        return self.width, self.height

    def restarts_decoder(self, previous: CodecLine | None) -> bool:
        """Whether a viewer must start from a key picture after this line: the codec string or the coded size changed
        (or there was no line).
        """
        if previous is None:
            return True
        return (self.codec, self.coded_width or self.width, self.coded_height or self.height) != (
            previous.codec,
            previous.coded_width or previous.width,
            previous.coded_height or previous.height,
        )

    def to_json(self, viewer: int | None = None) -> bytes:
        """The codec line as JSON text (with its newline), the body of a config frame.

        ``viewer`` is the id this viewer has at the exporter.
        """
        line: dict[str, Any] = {"codec": self.codec, "width": self.width, "height": self.height, "fps": round(self.fps)}
        if self.coded_width is not None:
            line["coded_width"] = self.coded_width
        if self.coded_height is not None:
            line["coded_height"] = self.coded_height
        if self.rotation is not None:
            line["rotation"] = self.rotation
        if self.point_scale is not None:
            line["point_scale"] = self.point_scale
        if self.input is not None:
            line["input"] = self.input
        line.update({"format": "annexb", "timestamp": "us", "v": WIRE_VERSION})
        if viewer is not None:
            line["viewer"] = viewer
        return (json.dumps(line, separators=(",", ":")) + "\n").encode()

    def frame(self, viewer: int | None = None) -> bytes:
        """The config frame."""
        return pack_frame(KIND_CONFIG, self.to_json(viewer))

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> CodecLine:
        """The line a source's own ``config`` object or a received line says (extra fields are ignored).

        Raises:
            ValueError: If a field is missing or cannot be right.
        """
        try:
            return cls(
                codec=data["codec"],
                width=data["width"],
                height=data["height"],
                fps=data.get("fps", 0) or 0,
                coded_width=data.get("coded_width"),
                coded_height=data.get("coded_height"),
                rotation=data.get("rotation"),
                point_scale=data.get("point_scale"),
                input=data.get("input"),
            )
        except KeyError as e:
            raise ValueError(f"the codec line has no {e.args[0]}") from None


def _whole(name: str, value: object, low: int, high: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or isinstance(value, float)
        and not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be a whole number from {low} to {high}: {value!r}")
    if value != int(value) or not low <= int(value) <= high:
        raise ValueError(f"{name} must be a whole number from {low} to {high}: {value!r}")


# -- keys and capabilities ---------------------------------------------------------------------------------------------

#: The common vocabulary (Android's names without ``KEYCODE_``). A source may add names of its own.
COMMON_KEYS = ("HOME", "BACK", "APP_SWITCH", "POWER", "LOCK", "VOLUME_UP", "VOLUME_DOWN", "MUTE", "ASSIST")
#: Other spellings of a common name.
KEY_ALIASES = {
    "APP_SWITCHER": "APP_SWITCH",
    "RECENTS": "APP_SWITCH",
    "VOLUME_MUTE": "MUTE",
    "SIRI": "ASSIST",
}
PRESSES = ("short", "long", "down", "up")
TOUCH_TYPES = ("down", "move", "up", "cancel")


def canonical_key(name: object) -> str:
    """A key name as the interface compares it: upper case, ``-`` read as ``_``, no ``KEYCODE_``, aliases resolved.

    Raises:
        ValueError: If ``name`` is not a string naming a key.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("key.key must name a key")
    upper = name.strip().upper().replace("-", "_")
    upper = upper.removeprefix("KEYCODE_")
    return KEY_ALIASES.get(upper, upper)


@dataclass(frozen=True)
class HardKey:
    """A button a UI draws: its common name, a label, and the presses it may be given."""

    key: str
    label: str
    presses: tuple[str, ...] = ("short", "long")

    def info(self) -> dict[str, Any]:
        """The ``hmi_info()`` form."""
        return {"key": self.key, "label": self.label, "presses": list(self.presses)}

    @classmethod
    def parse(cls, value: object) -> HardKey:
        """A hard key from an exporter's config: a name, or a mapping with ``key`` (and optionally ``label`` and
        ``presses``).

        Raises:
            ValueError: If it is neither.
        """
        if isinstance(value, str):
            key = canonical_key(value)
            return cls(key, key.replace("_", " ").title())
        if isinstance(value, dict) and "key" in value and set(value) <= {"key", "label", "presses"}:
            key = canonical_key(value["key"])
            presses = value.get("presses", ["short", "long"])
            if not isinstance(presses, list | tuple) or not presses or any(p not in PRESSES for p in presses):
                raise ValueError(f"hard key presses must be a list of {', '.join(PRESSES)}: {presses!r}")
            label = value.get("label", key.replace("_", " ").title())
            if not isinstance(label, str) or not label:
                raise ValueError("hard key label must be a non-empty string")
            return cls(key, label, tuple(presses))
        raise ValueError(f"a hard key is a key name or a mapping with key, label and presses: {value!r}")


@dataclass(frozen=True)
class QualityCaps:
    """What quality control a source has: ``levels`` it can be pinned to (the names of the ladder), and whether it
    follows the network.
    """

    levels: tuple[str, ...] = ("minimal", "low", "medium", "high")
    auto: bool = True


@dataclass(frozen=True)
class Capabilities:
    """What a HMI source is and takes: the interface's honest answer to ``hmi_info()``.

    ``keyframe_cost`` says what a key-picture request costs the source: ``cheap`` (the hub may ask at once, for another
    viewer),
    ``restart`` (it restarts the capture for every viewer: the hub relies on the periodic key picture) or ``none`` (it
    cannot be asked).
    """

    codecs: tuple[str, ...] = ("h264",)
    #: Input from here: ``max_fingers`` is 0 for a view-only source, 1 otherwise (v1).
    max_fingers: int = 1
    #: Every key name the source accepts (the common vocabulary and its own), canonical.
    keys: tuple[str, ...] = ()
    #: The buttons a UI draws, in order.
    hard_keys: tuple[HardKey, ...] = ()
    text: bool = False
    quality: QualityCaps | None = None
    keyframe_cost: str = "none"
    #: Whether the source reports a rotation in its codec line.
    rotation: bool = False
    #: Whether the source says its picture's size is its own screen's (so ``native_size`` is meaningful).
    extras: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    def __post_init__(self) -> None:
        """Refuse what the interface does not have."""
        if self.max_fingers not in (0, 1):
            raise ValueError("max_fingers is 0 or 1 in version 1 of the interface")
        if self.keyframe_cost not in ("cheap", "restart", "none"):
            raise ValueError("keyframe_cost is cheap, restart or none")

    def info(self, *, width: int | None, height: int | None, rotation: int = 0) -> dict[str, Any]:
        """``hmi_info()``: what a UI shows."""
        quality = self.quality
        return {
            "interface": "hmi",
            "v": WIRE_VERSION,
            "codecs": list(self.codecs),
            "width": width,
            "height": height,
            "rotation": rotation,
            "input": {
                "touch": self.max_fingers > 0,
                "max_fingers": self.max_fingers,
                "text": self.text and self.max_fingers > 0,
                "keys": list(self.keys) if self.max_fingers > 0 else [],
                "hard_keys": [k.info() for k in self.hard_keys] if self.max_fingers > 0 else [],
            },
            "quality": {
                "adjustable": quality is not None,
                "auto": bool(quality and quality.auto),
                "levels": list(quality.levels) if quality else [],
            },
            "recording": True,
            "screenshot": False,
            **self.extras,
        }


# -- input events ------------------------------------------------------------------------------------------------------


class InputError(ValueError):
    """An input line is wrong or cannot be taken: it is answered with an ``{"error": ...}`` line; the stream goes on."""


@dataclass(frozen=True)
class TouchEvent:
    """One touch: ``kind`` is ``down``, ``move`` or ``up`` (``cancel`` is read as ``up``)."""

    kind: str
    x: float
    y: float
    #: The size of the picture it was aimed at, when the sender said.
    aimed: tuple[int, int] | None = None


@dataclass(frozen=True)
class KeyEvent:
    """One key press by its canonical name."""

    key: str
    press: str = "short"


@dataclass(frozen=True)
class TextEvent:
    """Characters to type."""

    text: str


#: What an ignored event (a second contact) parses to.
IGNORED = None
InputEvent = TouchEvent | KeyEvent | TextEvent


def finite(value: object) -> bool:
    """Whether ``value`` is a real number (not a bool, not NaN or infinite; a huge integer counts as infinite)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def error_line(message: str) -> bytes:
    """An ``{"error": ...}`` line for the input stream."""
    return (json.dumps({"error": message}) + "\n").encode()


def parse_input(line: bytes | str) -> InputEvent | None:
    """One input line as an event; ``None`` for one that is to be ignored (a contact that is not finger 0).

    Raises:
        InputError: If the line is not valid JSON, not one of the three events, or malformed.
    """
    try:
        event = json.loads(line)
    except ValueError:
        raise InputError("an input line is one JSON object") from None
    if not isinstance(event, dict) or len(event) != 1:
        raise InputError("an input line is one JSON object with touch, key or text")
    kind, body = next(iter(event.items()))
    if kind == "text":
        return _text(body)
    if kind == "touch" and isinstance(body, dict):
        return _touch(body)
    if kind == "key" and isinstance(body, dict):
        return _key(body)
    raise InputError('send {"touch": {...}}, {"key": {...}} or {"text": "..."}')


def _text(text: object) -> TextEvent:
    if not isinstance(text, str):
        raise InputError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise InputError(f"text is longer than {MAX_TEXT_CHARS} characters")
    return TextEvent(text)


def _touch(body: dict[str, Any]) -> TouchEvent | None:
    kind = body.get("type")
    if kind not in TOUCH_TYPES:
        raise InputError('touch.type must be "down", "move", "up" or "cancel"')
    x, y = body.get("x"), body.get("y")
    if not finite(x) or not finite(y):
        raise InputError("touch.x and touch.y must be numbers")
    finger = body.get("id", 0)
    if isinstance(finger, bool) or not isinstance(finger, int):
        raise InputError("touch.id must be a whole number")
    if finger != 0:
        return IGNORED  # one contact in version 1: another finger is ignored, not an error
    aimed_w, aimed_h = body.get("w"), body.get("h")
    aimed = None
    if aimed_w is not None or aimed_h is not None:
        if not finite(aimed_w) or not finite(aimed_h):
            raise InputError("touch.w and touch.h must be numbers")
        aimed = (int(cast(float, aimed_w)), int(cast(float, aimed_h)))
    return TouchEvent("up" if kind == "cancel" else kind, float(cast(float, x)), float(cast(float, y)), aimed)


def _key(body: dict[str, Any]) -> KeyEvent | TextEvent:
    if body.get("text") is not None:  # the older form, accepted for one release
        return _text(body["text"])
    if "code" in body:
        raise InputError("a browser key event is not part of the interface: send text or a named key")
    press = body.get("press", "short")
    if press not in PRESSES:
        raise InputError("key.press must be short, long, down or up")
    try:
        return KeyEvent(canonical_key(body.get("key")), press)
    except ValueError as e:
        raise InputError(str(e)) from None
