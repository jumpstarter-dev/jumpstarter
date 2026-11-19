"""The HMI interface's wire format and vocabulary: pure code, no Jumpstarter, no I/O.

An **HMI** (human-machine interface) here is a remote display with declared input: a picture, and whatever the device
takes back: a pointer (touch, mouse, pen), a keyboard, hard keys, rotary knobs and typed text. It covers mobile devices
and embedded touch screens, and later remote desktops (VNC) and KVM devices. The word means the device's interface, not
an HMI application that runs on it.

**The video stream** (what ``video()`` carries). A Jumpstarter byte stream has no message boundaries and an access unit
has no length, so the exporter frames what it sends::

    frame  = u32 big-endian N  +  u8 kind  +  body[N - 1]
    kind 0 = config: body is one JSON line (UTF-8, ends with a newline), the codec line
    kind 1 = media:  body is an 8-byte big-endian timestamp in microseconds + ONE access unit
    kind 2-15        reserved: a reader skips a kind it does not know

Without the 5-byte transport header, a ``kind 0`` body is the codec line and a ``kind 1`` body is the "8-byte timestamp
+ access unit" that a relay can pass to a WebSocket as a text and a binary frame, one to one. The first frame is always
a config frame, and a key picture follows it; the line is sent again whenever anything in it changes.

The **codec line** names the codec in use, as a WebCodecs codec string, and the picture::

    {"codec": "avc1.64001F", "width": 1080, "height": 2400, "fps": 30, "format": "annexb", "timestamp": "us", "v": 1}

``codec`` is ``avc1.*`` (H.264, the default), ``hev1.*`` or ``hvc1.*`` (HEVC) or ``mjpeg`` (Motion JPEG). H.264 and HEVC
units are Annex-B access units with their parameter sets (SPS/PPS, plus VPS for HEVC) in front of every key picture
(``format`` is ``annexb``). An ``mjpeg`` unit is one complete JPEG picture, and every picture is a key picture
(``format`` is ``jpeg``). Optional fields: ``coded_width``, ``coded_height``, ``rotation``, ``point_scale``, ``input``
and ``viewer``. A stream has **one** codec at a time (the hub fans out one encode); it changes with a restart, which
sends a new codec line.

**The input stream** (what ``input()`` takes): one JSON object per line, in the order sent. Errors come back as
``{"error": "..."}`` lines that the exporter never waits on::

    {"pointer": {"type": "down", "kind": "mouse", "x": 540, "y": 1200, "id": 0, "buttons": 1, "w": 1206, "h": 2622}}
    {"pointer": {"type": "wheel", "kind": "mouse", "x": 540, "y": 1200, "dx": 0, "dy": 120, "w": 1206, "h": 2622}}
    {"touch": {"type": "down", "x": 540, "y": 1200, "id": 0, "w": 1206, "h": 2622}}
    {"keyboard": {"code": "KeyA", "type": "down"}}
    {"key": {"key": "HOME", "press": "short" | "long" | "down" | "up"}}
    {"rotary": {"id": "volume", "delta": -2}}
    {"text": "hello"}

*pointer*. ``type`` is ``down``, ``move``, ``up``, ``cancel`` (read as ``up``) or ``wheel``; ``kind`` is ``touch``,
``mouse`` or ``pen``. ``x`` and ``y`` are **pixels of the shown picture** (the codec line's size turned by its
``rotation``) and ``w`` and ``h`` (optional, both or neither) name the picture the event was aimed at: one aimed at
another size is refused. ``id`` tells the client's contacts apart (a whole number such as ``PointerEvent.pointerId``,
default 0); the device is told small numbers, 0 to ``max_contacts`` - 1, in the order the contacts come down.
``buttons`` is the bitmask of ``PointerEvent.buttons`` (1 primary, 2 secondary, 4 auxiliary, 8 back, 16 forward, 32
eraser): the state **after** the event, as in a browser (``down`` when the first button goes down, ``move`` when others
change, ``up`` when the last is let go); when it is left out a ``down`` has the primary button and a ``move`` keeps the
buttons that are down. A ``move`` with no contact down is a hover, taken from a mouse or a pen, unless it says a
declared button is down: a browser reports a button that goes down while another is held as a ``move``, and that is a
press. ``dx`` and ``dy`` are pixels: the wheel deltas of a ``wheel`` (the sign of ``WheelEvent.deltaX`` and ``deltaY``:
positive is right and down; the wire has ``deltaMode`` pixels only, so a client that has line or page deltas converts
them to pixels first), and the relative motion of a ``move`` that has no ``x`` and ``y`` (only for a device that
declares ``relative``). ``{"touch": ...}`` is shorthand for a pointer of kind ``touch``. Contacts beyond
``max_contacts``, kinds and buttons the device does not declare are ignored, not errors; a ``wheel`` or a relative
motion for a device without one is refused. A contact still down when the stream ends is lifted.

*keyboard*. ``code`` is a W3C ``KeyboardEvent.code`` (``KeyA``, ``Digit1``, ``ArrowLeft``, ``ShiftLeft``: the physical
key, whatever the layout) and ``type`` is ``down`` or ``up``. Modifiers are keys like any other (``ShiftLeft`` down,
then ``KeyA`` down). A ``down`` of a key that is already down is a repeat and is dropped: the device repeats by itself.
Keys still down when the stream ends are released. Only the codes the device declares are taken.

*key*. A named hard key (``HOME``, ``BACK``, ``APP_SWITCH``, ``VOLUME_UP``...): ``press`` is ``short``, ``long``,
``down`` or ``up``. The names a device takes are the ones it lists, each with the presses it declares (``short`` and
``long`` unless it says); power and lock are only taken when the exporter lists them.

*rotary*. A rotary knob or encoder the device declares in ``rotaries``: ``id`` names it and ``delta`` is the number of
detents it is turned, positive clockwise (a whole number, not 0, at most 100 either way: a fast spin is several lines).
Pushing it is a hard key that the device lists, named by the rotary's ``push`` (``{"key": {"key": "VOLUME_PUSH"}}``).

*text*. Characters to type, in the layout the device declares (``us`` by default); a device that can only type text
takes this and no ``keyboard`` events.

What a device takes is declared in ``hmi_info()["input"]`` (see :class:`Capabilities`): a UI shows only what is there,
and a device that declares nothing is a valid, view-only HMI."""

from __future__ import annotations

import json
import math
import re
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
                raise ValueError(f"HMI stream frame of {length} bytes: the stream is out of step")
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


#: The codecs the interface knows, by the names a source declares and an exporter configures: ``h264`` (the default),
#: ``hevc`` and ``mjpeg``. Browser and hardware support is narrower for HEVC and MJPEG, so they are always explicit.
CODECS = ("h264", "hevc", "mjpeg")
DEFAULT_CODEC = "h264"


def codec_family(codec: str) -> str:
    """The family (``h264``, ``hevc`` or ``mjpeg``) of a codec line's WebCodecs string (``avc1.64001F``,
    ``hev1.1.6.L93.B0``, ``mjpeg``).

    Raises:
        ValueError: If the interface does not know the codec.
    """
    lowered = codec.lower() if isinstance(codec, str) else ""
    if lowered.startswith(("avc1.", "avc3.")):
        return "h264"
    if lowered.startswith(("hev1.", "hvc1.")):
        return "hevc"
    if lowered == "mjpeg":
        return "mjpeg"
    raise ValueError(f"the codec is not one the interface knows (avc1.*, hev1.*, hvc1.* or mjpeg): {codec!r}")


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
    #: Where input goes: ``stream`` (this driver's ``input()``) or another word the source says; ``None`` leaves the
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
        codec_family(self.codec)
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
    def family(self) -> str:
        """``h264``, ``hevc`` or ``mjpeg``."""
        return codec_family(self.codec)

    @property
    def format(self) -> str:
        """How the units are written: ``annexb`` (H.264 and HEVC access units) or ``jpeg`` (one JPEG each)."""
        return "jpeg" if self.family == "mjpeg" else "annexb"

    @property
    def shown_size(self) -> tuple[int, int]:
        """``(width, height)`` of the picture a viewer shows: the visible size turned by ``rotation`` (90 and 270 swap
        the two).

        It is the picture a pointer is aimed at.
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
        line.update({"format": self.format, "timestamp": "us", "v": WIRE_VERSION})
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
#: What a hard key takes when nothing else is said about it.
DEFAULT_PRESSES = ("short", "long")


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
    presses: tuple[str, ...] = DEFAULT_PRESSES

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
            presses = value.get("presses", list(DEFAULT_PRESSES))
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


#: The kinds of pointer, and the types of pointer event.
POINTER_KINDS = ("touch", "mouse", "pen")
POINTER_TYPES = ("down", "move", "up", "cancel", "wheel")
#: ``PointerEvent.buttons``: the bits.
BUTTON_PRIMARY = 1
BUTTON_SECONDARY = 2
BUTTON_AUXILIARY = 4
BUTTON_BACK = 8
BUTTON_FORWARD = 16
BUTTON_ERASER = 32
BUTTON_MASK = 63
#: A ``KeyboardEvent.code`` as a W3C names it: ``KeyA``, ``Digit1``, ``ArrowLeft``, ``F13``, ``Lang1``.
KEY_CODE = re.compile(r"[A-Za-z][A-Za-z0-9]{0,31}")
#: What ``KeyboardCaps.codes`` says for a device that takes every key.
ALL_CODES = "all"


@dataclass(frozen=True)
class PointerCaps:
    """The pointer a source takes: ``kinds`` of pointer (none: no pointer), whether it is told where in the picture
    (``absolute``) and/or by how far it moved (``relative``), whether it takes ``buttons`` beyond the primary one and
    a ``wheel``, and how many ``max_contacts`` (fingers) it follows at once.
    """

    kinds: tuple[str, ...] = ()
    absolute: bool = True
    relative: bool = False
    buttons: bool = False
    wheel: bool = False
    max_contacts: int = 0

    def __post_init__(self) -> None:
        """Refuse what the interface does not have."""
        if any(kind not in POINTER_KINDS for kind in self.kinds):
            raise ValueError(f"a pointer kind is one of {', '.join(POINTER_KINDS)}: {self.kinds!r}")
        contacts = self.max_contacts
        if isinstance(contacts, bool) or not isinstance(contacts, int) or not 0 <= contacts <= 32:
            raise ValueError("max_contacts is a whole number from 0 to 32")
        if self.kinds and self.max_contacts < 1:
            raise ValueError("a pointer that takes a kind follows at least one contact")
        if self.kinds and not (self.absolute or self.relative):
            raise ValueError("a pointer is absolute, relative or both")

    @classmethod
    def touch(cls, max_contacts: int = 1) -> PointerCaps:
        """A touch screen: absolute, ``max_contacts`` fingers, no buttons and no wheel."""
        return cls(kinds=("touch",), max_contacts=max_contacts)

    @classmethod
    def mouse(cls, *, buttons: bool = True, wheel: bool = True, relative: bool = False) -> PointerCaps:
        """A mouse: absolute (and relative if asked), one contact, with buttons and a wheel unless said otherwise."""
        return cls(kinds=("mouse",), buttons=buttons, wheel=wheel, relative=relative, max_contacts=1)

    @property
    def present(self) -> bool:
        """Whether there is a pointer at all."""
        return bool(self.kinds)

    def info(self) -> dict[str, Any]:
        """The ``hmi_info()`` form."""
        return {
            "kinds": list(self.kinds),
            "absolute": self.absolute and self.present,
            "relative": self.relative and self.present,
            "buttons": self.buttons and self.present,
            "wheel": self.wheel and self.present,
            "max_contacts": self.max_contacts if self.present else 0,
        }


@dataclass(frozen=True)
class KeyboardCaps:
    """The keyboard a source takes: the key ``codes`` (W3C ``KeyboardEvent.code`` names, or ``"all"``; none: no key
    events), whether it types ``text`` and in which ``layout`` (``us`` unless it says; a device that can only type text
    types it in this layout).
    """

    codes: tuple[str, ...] | str = ()
    text: bool = False
    layout: str | None = None

    def __post_init__(self) -> None:
        """Refuse what the interface does not have."""
        if self.codes != ALL_CODES and (
            isinstance(self.codes, str) or any(not isinstance(c, str) or not KEY_CODE.fullmatch(c) for c in self.codes)
        ):
            raise ValueError(f'codes is "all" or a list of KeyboardEvent.code names: {self.codes!r}')

    @property
    def present(self) -> bool:
        """Whether there is a keyboard at all."""
        return self.text or bool(self.codes)

    def allows(self, code: str) -> bool:
        """Whether the device takes this key."""
        if self.codes == ALL_CODES:
            return bool(KEY_CODE.fullmatch(code))
        return code in self.codes

    def info(self) -> dict[str, Any]:
        """The ``hmi_info()`` form."""
        return {
            "codes": ALL_CODES if self.codes == ALL_CODES else list(self.codes),
            "text": self.text,
            "layout": (self.layout or "us") if self.present else None,
        }


#: A rotary's id, as it is named on the wire: ``volume``, ``tune-knob``.
ROTARY_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
#: Most detents one ``rotary`` line may turn (a fast spin is several lines).
MAX_ROTARY_DELTA = 100


@dataclass(frozen=True)
class RotaryCaps:
    """A rotary knob or encoder the device has: turned by detents (positive is clockwise) and, if it has a push
    button, pushed with the hard key ``push`` (a name in the HMI's ``keys``, such as ``VOLUME_PUSH``).
    """

    id: str
    label: str = ""
    push: str | None = None

    def __post_init__(self) -> None:
        """Refuse what the interface does not have."""
        if not isinstance(self.id, str) or not ROTARY_ID.fullmatch(self.id) or not isinstance(self.label, str):
            raise ValueError(f"a rotary has a short id (letters, digits, - and _) and a text label: {self.id!r}")
        if self.push is not None:
            object.__setattr__(self, "push", canonical_key(self.push))

    @classmethod
    def parse(cls, value: object) -> RotaryCaps:
        """A rotary from an exporter's config: its id, or a mapping with ``id`` (and optionally ``label`` and ``push``).

        Raises:
            ValueError: If it is neither.
        """
        if isinstance(value, str):
            return cls(value)
        if isinstance(value, dict) and "id" in value and set(value) <= {"id", "label", "push"}:
            return cls(value["id"], value.get("label", ""), value.get("push"))
        raise ValueError(f"a rotary is an id or a mapping with id, label and push: {value!r}")

    def info(self) -> dict[str, Any]:
        """The ``hmi_info()`` form."""
        label = self.label or self.id.replace("-", " ").replace("_", " ").title()
        return {"id": self.id, "label": label, "push": self.push}


@dataclass(frozen=True)
class Capabilities:
    """What a HMI source is and takes: the interface's honest answer to ``hmi_info()``. A source that declares no input
    is a valid, view-only HMI.

    ``keyframe_cost`` says what a key-picture request costs the source: ``cheap`` (the hub may ask at once, for another
    viewer), ``restart`` (it restarts the capture for every viewer: the hub relies on the periodic key picture) or
    ``none`` (it cannot be asked).
    """

    #: The codecs the source can produce (``h264``, ``hevc``, ``mjpeg``), the first being its default.
    codecs: tuple[str, ...] = (DEFAULT_CODEC,)
    pointer: PointerCaps = field(default_factory=PointerCaps)
    keyboard: KeyboardCaps = field(default_factory=KeyboardCaps)
    #: Every named (hard) key the source accepts (the common vocabulary and its own), canonical.
    keys: tuple[str, ...] = ()
    #: Keys the source can send but does not offer by default (a power or lock button: pressing one can lock the user
    #: out of a device whose only way in is its screen). They are accepted only when the exporter lists them in
    #: ``hard_keys``.
    optional_keys: tuple[str, ...] = ()
    #: The buttons a UI draws, in order.
    hard_keys: tuple[HardKey, ...] = ()
    #: The rotary knobs the device has (none for most).
    rotaries: tuple[RotaryCaps, ...] = ()
    #: Whether the clipboard can be set and read through the HMI.
    clipboard: bool = False
    quality: QualityCaps | None = None
    keyframe_cost: str = "none"
    #: Whether the source reports a rotation in its codec line.
    rotation: bool = False
    extras: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)

    def __post_init__(self) -> None:
        """Refuse what the interface does not have."""
        codecs = self.codecs
        if not codecs or any(codec not in CODECS for codec in codecs) or len(set(codecs)) != len(codecs):
            raise ValueError(f"codecs is a list of {', '.join(CODECS)}, the default first: {self.codecs!r}")
        if self.keyframe_cost not in ("cheap", "restart", "none"):
            raise ValueError("keyframe_cost is cheap, restart or none")
        if len({r.id for r in self.rotaries}) != len(self.rotaries):
            raise ValueError("a rotary id is used once")
        for rotary in self.rotaries:
            if rotary.push is not None and rotary.push not in {canonical_key(k) for k in self.keys}:
                raise ValueError(f"the push button of rotary {rotary.id!r} is a key the HMI takes: {rotary.push!r}")

    @property
    def has_input(self) -> bool:
        """Whether anything can be sent to the device: a pointer, a keyboard, text, a hard key or a rotary."""
        return self.pointer.present or self.keyboard.present or bool(self.keys) or bool(self.rotaries)

    def info(
        self, *, width: int | None, height: int | None, rotation: int = 0, codec: str | None = None
    ) -> dict[str, Any]:
        """``hmi_info()``: what a UI shows. ``codec`` is the one in use or chosen."""
        quality = self.quality
        selected = codec or self.codecs[0]
        return {
            "interface": "hmi",
            "v": WIRE_VERSION,
            "codecs": list(self.codecs),
            "codec": selected,
            "width": width,
            "height": height,
            "rotation": rotation,
            "input": {
                "pointer": self.pointer.info(),
                "keyboard": self.keyboard.info(),
                "keys": list(self.keys) if self.has_input else [],
                "hard_keys": [k.info() for k in self.hard_keys] if self.has_input else [],
                "rotaries": [r.info() for r in self.rotaries],
                "clipboard": self.clipboard,
            },
            "quality": {
                "adjustable": quality is not None,
                "auto": bool(quality and quality.auto),
                "levels": list(quality.levels) if quality else [],
            },
            "recording": selected != "mjpeg",
            "screenshot": False,
            **self.extras,
        }


# -- input events ------------------------------------------------------------------------------------------------------


class InputError(ValueError):
    """An input line is wrong or cannot be taken: it is answered with an ``{"error": ...}`` line; the stream goes on."""


@dataclass(frozen=True)
class PointerEvent:
    """One pointer event. ``type`` is ``down``, ``move``, ``up`` or ``wheel`` (``cancel`` is read as ``up``); ``x`` and
    ``y`` are pixels of the shown picture (``None``: a relative ``move``); ``buttons`` is ``PointerEvent.buttons``
    (``None``: not said); ``dx`` and ``dy`` are the wheel deltas or the relative motion, in pixels.
    """

    type: str
    kind: str = "touch"
    x: float | None = None
    y: float | None = None
    id: int = 0
    buttons: int | None = None
    dx: float = 0.0
    dy: float = 0.0
    #: The size of the picture it was aimed at, when the sender said.
    aimed: tuple[int, int] | None = None


@dataclass(frozen=True)
class KeyboardEvent:
    """One key going down or up, by its W3C ``KeyboardEvent.code``."""

    code: str
    down: bool


@dataclass(frozen=True)
class KeyEvent:
    """One named hard key press by its canonical name."""

    key: str
    press: str = "short"


@dataclass(frozen=True)
class TextEvent:
    """Characters to type."""

    text: str


@dataclass(frozen=True)
class RotaryEvent:
    """A rotary turned by ``delta`` detents (positive is clockwise)."""

    id: str
    delta: int


InputEvent = PointerEvent | KeyboardEvent | KeyEvent | TextEvent | RotaryEvent


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


def parse_input(line: bytes | str) -> InputEvent:
    """One input line as an event.

    Raises:
        InputError: If the line is not valid JSON, not one of the events, or malformed.
    """
    try:
        event = json.loads(line)
    except ValueError:
        raise InputError("an input line is one JSON object") from None
    if not isinstance(event, dict) or len(event) != 1:
        raise InputError("an input line is one JSON object with pointer, keyboard, key, rotary or text")
    kind, body = next(iter(event.items()))
    if kind == "text":
        return _text(body)
    if isinstance(body, dict):
        if kind == "pointer":
            return _pointer(body, None)
        if kind == "touch":  # shorthand for a pointer of kind touch
            return _pointer(body, "touch")
        if kind == "keyboard":
            return _keyboard(body)
        if kind == "key":
            return _key(body)
        if kind == "rotary":
            return _rotary(body)
    raise InputError(
        'send {"pointer": {...}}, {"touch": {...}}, {"keyboard": {...}}, {"key": {...}}, {"rotary": {...}} or '
        '{"text": "..."}'
    )


def _text(text: object) -> TextEvent:
    if not isinstance(text, str):
        raise InputError("text must be a string")
    if len(text) > MAX_TEXT_CHARS:
        raise InputError(f"text is longer than {MAX_TEXT_CHARS} characters")
    return TextEvent(text)


def _pointer(body: dict[str, Any], default_kind: str | None) -> PointerEvent:
    name = "touch" if default_kind else "pointer"
    kind = body.get("type")
    if kind not in POINTER_TYPES:
        raise InputError(f'{name}.type must be one of {", ".join(POINTER_TYPES)}')
    pointer_kind = body.get("kind", default_kind)
    if pointer_kind not in POINTER_KINDS:
        raise InputError(f'{name}.kind must be one of {", ".join(POINTER_KINDS)}')
    contact = body.get("id", 0)
    if isinstance(contact, bool) or not isinstance(contact, int) or not 0 <= contact < 2**31:
        raise InputError(f"{name}.id must be a whole number")
    x, y = body.get("x"), body.get("y")
    dx, dy = body.get("dx", 0), body.get("dy", 0)
    if not finite(dx) or not finite(dy):
        raise InputError(f"{name}.dx and {name}.dy must be numbers")
    relative = kind == "move" and x is None and y is None and (dx or dy)
    if not relative and (not finite(x) or not finite(y)):
        raise InputError(f"{name}.x and {name}.y must be numbers")
    buttons = body.get("buttons")
    if buttons is not None and (
        isinstance(buttons, bool) or not isinstance(buttons, int) or not 0 <= buttons <= BUTTON_MASK
    ):
        raise InputError(f"{name}.buttons must be a whole number from 0 to {BUTTON_MASK}")
    aimed_w, aimed_h = body.get("w"), body.get("h")
    aimed = None
    if aimed_w is not None or aimed_h is not None:
        if not finite(aimed_w) or not finite(aimed_h):
            raise InputError(f"{name}.w and {name}.h must be numbers")
        aimed = (int(cast(float, aimed_w)), int(cast(float, aimed_h)))
    return PointerEvent(
        "up" if kind == "cancel" else kind,
        pointer_kind,
        None if relative else float(cast(float, x)),
        None if relative else float(cast(float, y)),
        contact,
        buttons,
        float(cast(float, dx)),
        float(cast(float, dy)),
        aimed,
    )


def _keyboard(body: dict[str, Any]) -> KeyboardEvent:
    code = body.get("code")
    if not isinstance(code, str) or not KEY_CODE.fullmatch(code):
        raise InputError("keyboard.code must be a KeyboardEvent.code name (KeyA, Digit1, ArrowLeft...)")
    if body.get("type") not in ("down", "up"):
        raise InputError('keyboard.type must be "down" or "up"')
    return KeyboardEvent(code, body["type"] == "down")


def _key(body: dict[str, Any]) -> KeyEvent:
    if "code" in body:
        raise InputError('a KeyboardEvent.code is sent as {"keyboard": {"code": ..., "type": "down" | "up"}}')
    press = body.get("press", "short")
    if press not in PRESSES:
        raise InputError("key.press must be short, long, down or up")
    try:
        return KeyEvent(canonical_key(body.get("key")), press)
    except ValueError as e:
        raise InputError(str(e)) from None


def _rotary(body: dict[str, Any]) -> RotaryEvent:
    rotary_id, delta = body.get("id"), body.get("delta")
    if not isinstance(rotary_id, str) or not ROTARY_ID.fullmatch(rotary_id):
        raise InputError("rotary.id must be the name of a rotary")
    if isinstance(delta, bool) or not isinstance(delta, int) or delta == 0 or abs(delta) > MAX_ROTARY_DELTA:
        raise InputError(f"rotary.delta must be a whole number of detents, not 0, at most {MAX_ROTARY_DELTA} each way")
    return RotaryEvent(rotary_id, delta)
