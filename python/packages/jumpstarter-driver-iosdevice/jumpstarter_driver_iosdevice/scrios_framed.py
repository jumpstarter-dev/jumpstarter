"""The framed protocol (version 1) that ``scrios --format framed`` writes on its standard output.

scrios is a separate program that streams an iPhone's or iPad's screen; its machine interface is documented in its own
repository (``docs/protocol.md``). This module is the reader of that interface: bytes in, what they mean out, with no
process, no thread and no Jumpstarter in it. All integers are big-endian::

    u32 len | u64 pts_us | u8 flags | payload[len]
    flags: 0x01 KEY      the access unit has a key picture (H.264: an IDR slice; HEVC: an IRAP picture)
           0x02 PARAMS   the parameter sets (H.264: SPS and PPS; HEVC: VPS, SPS and PPS) are at the start of the
                         payload
           0x80 CONTROL  the payload is one UTF-8 JSON object ``{"event": ...}``, not an access unit

``len`` counts the payload only and is at most 16 MiB. Any other frame is one Annex-B access unit, H.264 or HEVC: the
``codec`` of the ``config`` before it says which (``avc1.*``, or ``hev1.*``/``hvc1.*``). The control events are
``config`` (the picture; always before the first access unit, and again when anything in it changes), ``stats``,
``hid`` (what the touch and key services said), ``error`` (once, before a non-zero exit) and ``stopped`` (last, on a
clean stop). A program ignores the events and fields it does not know; the version number (``protocol`` in every
``config``) changes only when something incompatible does.

:class:`ScriosStream` is the reader: feed it whatever chunks the pipe gave and it returns the items they completed.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from typing import Any

from jumpstarter_driver_hmi.common import CodecLine, codec_family

#: The version of the framed protocol this reader speaks (``protocol`` in every ``config`` event).
PROTOCOL = 1
#: The codecs scrios streams, by the HMI interface's names: ``config``'s ``codec`` is ``avc1.*`` for the first and
#: ``hev1.*`` or ``hvc1.*`` for the second. Any other codec is refused.
CODEC_FAMILIES = ("h264", "hevc")
#: What a codec is called in a message.
CODEC_NAMES = {"h264": "H.264", "hevc": "HEVC"}
HEADER = struct.Struct(">IQB")
#: The largest payload scrios writes; a length beyond it means the stream is out of step.
MAX_PAYLOAD = 16 * 1024 * 1024
FLAG_KEY = 0x01
FLAG_PARAMS = 0x02
FLAG_CONTROL = 0x80
#: What else the ``config`` event says about the picture: kept for ``status()``, not part of the codec line.
PICTURE_LABELS = ("current_orientation", "native_orientation", "clock_source")
#: The longest control payload that is parsed (a ``config`` is a few hundred bytes); a larger one is not an event.
MAX_CONTROL_BYTES = 64 * 1024


class FrameError(ValueError):
    """The stream cannot be right (a length beyond the limit). ``items`` are what was complete before the bad frame."""

    def __init__(self, message: str, items: list[Item] | None = None) -> None:
        """Say what is wrong; ``items`` are what the reader had decoded before the bad frame."""
        super().__init__(message)
        self.items = items or []


@dataclass(frozen=True, slots=True)
class Unit:
    """One access unit. ``key`` and ``params`` are scrios's own flags: it says what the unit is, nothing is guessed."""

    pts_us: int
    key: bool
    params: bool
    payload: bytes


@dataclass(frozen=True, slots=True)
class Config:
    """A ``config`` event: the picture, as a codec line (with ``input`` left for the caller to say), and the labels the
    event carries besides it.
    """

    line: CodecLine
    labels: dict[str, str | None] = field(default_factory=dict)
    protocol: int = PROTOCOL


@dataclass(frozen=True, slots=True)
class Event:
    """Any other control event (``stats``, ``hid``, ``error``, ``stopped``, one this reader does not know): ``name`` and
    the whole object.
    """

    name: str
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Problem:
    """Something wrong with one frame that does not end the stream: the frame is skipped and this says why."""

    message: str


@dataclass(frozen=True, slots=True)
class Fatal:
    """The stream cannot be used: another protocol version, or a codec that is not the one asked for (or not H.264 or
    HEVC at all). The caller ends the run.
    """

    message: str


Item = Unit | Config | Event | Problem | Fatal


def pack_frame(payload: bytes, pts_us: int = 0, flags: int = 0) -> bytes:
    """One frame as scrios writes it (what the tests build)."""
    return HEADER.pack(len(payload), pts_us, flags) + payload


def pack_event(**event: Any) -> bytes:
    """One control frame (what the tests build)."""
    return pack_frame(json.dumps(event).encode(), 0, FLAG_CONTROL)


def _label(value: object) -> str | None:
    """A short printable string scrios sent, or ``None`` (not sent, or not a string worth keeping)."""
    return value if isinstance(value, str) and 1 <= len(value) <= 64 and value.isprintable() else None


def _whole(config: dict[str, Any], name: str) -> None:
    """Turn a whole number that was sent as a float (``1206.0``) into an integer; leave anything else for the codec line
    to refuse.
    """
    value = config.get(name)
    if isinstance(value, float) and math.isfinite(value) and value == int(value):
        config[name] = int(value)


def _codec_line(event: dict[str, Any]) -> CodecLine:
    """The codec line of a ``config`` event.

    Raises:
        ValueError: If a field is missing or cannot be right.
    """
    config = dict(event)
    for name in ("width", "height", "coded_width", "coded_height", "rotation"):
        _whole(config, name)
    return CodecLine.from_json(config)


class ScriosStream:
    """Reads scrios's standard output, however the pipe chunked it (a header may arrive in pieces).

    Access units that arrive before the first valid ``config`` are dropped (scrios says what the picture is before its
    first unit; one that does not is not served): the first is reported as a :class:`Problem`, all are counted in
    :attr:`units_before_config`. A control frame that is not a JSON object, or a ``config`` that cannot be right, is a
    :class:`Problem` and is counted in :attr:`bad_control`. A ``config`` of another ``protocol``, of a codec that is
    neither H.264 nor HEVC, or (when ``expect`` names the codec that was asked for) of the other one of the two, is a
    :class:`Fatal`; everything after it is dropped.
    """

    def __init__(self, max_payload: int = MAX_PAYLOAD, expect: str | None = None) -> None:
        """Start with nothing buffered; refuse a payload longer than ``max_payload``.

        ``expect`` is the codec the stream was asked for (``h264`` or ``hevc``): a ``config`` that names the other one
        is fatal. Left out, either is taken.

        Raises:
            ValueError: If ``expect`` is not one of the codecs scrios streams.
        """
        if expect is not None and expect not in CODEC_FAMILIES:
            raise ValueError(f"scrios streams {' or '.join(CODEC_FAMILIES)}, not {expect!r}")
        self._buf = bytearray()
        self._max = max_payload
        self._expect = expect
        self._configured = False
        self._fatal = False
        self.units_before_config = 0
        self.bad_control = 0
        self.frames = 0

    @property
    def buffered(self) -> int:
        """Bytes of an incomplete frame held back."""
        return len(self._buf)

    @property
    def configured(self) -> bool:
        """Whether a valid ``config`` has been read (units are passed on from then)."""
        return self._configured

    def feed(self, data: bytes) -> list[Item]:
        """The items ``data`` completed.

        Raises:
            FrameError: On a length beyond the limit; it carries the items completed before it.
        """
        self._buf += data
        items: list[Item] = []
        pos, size, head = 0, len(self._buf), HEADER.size
        while size - pos >= head:
            length, pts_us, flags = HEADER.unpack_from(self._buf, pos)
            if length > self._max:
                del self._buf[:pos]
                raise FrameError(
                    f"scrios sent a frame of {length} bytes (at most {self._max}): its stream is out of step", items
                )
            end = pos + head + length
            if size < end:
                break
            self.frames += 1
            self._frame(items, pts_us, flags, bytes(self._buf[pos + head : end]))
            pos = end
        if pos:
            del self._buf[:pos]
        return items

    # -- one frame -----------------------------------------------------------------------------------------------------

    def _frame(self, items: list[Item], pts_us: int, flags: int, payload: bytes) -> None:
        if flags & FLAG_CONTROL:
            self._control(items, payload)
        elif self._fatal:
            return
        elif not self._configured:
            self.units_before_config += 1
            if self.units_before_config == 1:
                items.append(Problem("scrios sent a picture before its config: ignoring pictures until it does"))
        else:
            items.append(Unit(pts_us, bool(flags & FLAG_KEY), bool(flags & FLAG_PARAMS), payload))

    def _control(self, items: list[Item], payload: bytes) -> None:
        if self._fatal:
            return
        try:
            if len(payload) > MAX_CONTROL_BYTES:
                raise ValueError(f"{len(payload)} bytes")
            message = json.loads(payload)
            if not isinstance(message, dict):
                raise ValueError("not an object")  # noqa: TRY004
        except ValueError as e:
            self.bad_control += 1
            items.append(Problem(f"scrios sent a control frame that is not a JSON object ({e})"))
            return
        name = message.get("event")
        if name == "config":
            self._config(items, message)
        elif isinstance(name, str):
            items.append(Event(name, message))
        else:
            self.bad_control += 1
            items.append(Problem("scrios sent a control frame with no event name"))

    def _config(self, items: list[Item], message: dict[str, Any]) -> None:
        protocol = message.get("protocol")
        if isinstance(protocol, bool) or protocol != PROTOCOL:
            self._fatal = True
            items.append(Fatal(_protocol_text(protocol)))
            return
        if (refusal := self._refused_codec(message.get("codec"))) is not None:
            self._fatal = True
            items.append(Fatal(refusal))
            return
        try:
            line = _codec_line(message)
        except ValueError as e:
            self.bad_control += 1
            items.append(Problem(f"scrios sent a config that cannot be right: {e}"))
            return
        self._configured = True
        items.append(Config(line, {name: _label(message.get(name)) for name in PICTURE_LABELS}, PROTOCOL))

    def _refused_codec(self, codec: object) -> str | None:
        """Why a ``config``'s codec cannot be used (``None``: it can). A value that is no codec string at all is left
        to the codec line, which calls the whole ``config`` a :class:`Problem`.
        """
        if not isinstance(codec, str) or not 1 <= len(codec) <= 64 or not codec.isascii() or not codec.isprintable():
            return None
        try:
            family = codec_family(codec)
        except ValueError:
            family = None
        if family not in CODEC_FAMILIES:  # not a codec of this interface either, or MJPEG, which scrios does not make
            return f"scrios streams {codec}, which is neither H.264 nor HEVC"
        if self._expect is not None and family != self._expect:
            return f"scrios streams {CODEC_NAMES[family]} ({codec}), though {CODEC_NAMES[self._expect]} was asked for"
        return None


def _protocol_text(protocol: object) -> str:
    if protocol is None:
        return (
            f"scrios's config has no protocol number: it is older than the framed protocol {PROTOCOL} this driver "
            "speaks; install a newer scrios"
        )
    return (
        f"scrios speaks framed protocol {protocol!r}, this driver speaks {PROTOCOL}: use a scrios release that "
        "matches this version of jumpstarter-driver-iosdevice"
    )
