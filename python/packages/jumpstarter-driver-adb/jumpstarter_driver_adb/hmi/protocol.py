"""The wire protocol of ``scrcpy-server`` 4.1, as far as the HMI speaks it.

scrcpy's protocol is internal and changes between versions: the server refuses a client of another version, so this
module is written for exactly one (``SERVER_VERSION``), the one whose ``scrcpy-server`` is pinned. Everything here is
from the scrcpy source of that tag (``doc/develop.md``, ``Streamer.java``, ``DesktopConnection.java``,
``ControlMessageReader.java``).

The server is started with ``tunnel_forward=true``: the device listens on an abstract socket
(``localabstract:scrcpy_<scid>``), the host reaches it through ``adb forward``, and connects twice, in this order:

1. **video**: the server writes a dummy byte as soon as it accepts the connection (so that a connection with nothing
   behind it is noticed) and says nothing more until the control connection is accepted too. Then it sends 64 bytes of
   device name, the codec id (u32: the ASCII of the codec's name, ``h264`` or ``h265``, for the codec asked with the
   ``video_codec`` option), a *session* packet (12 bytes: a flag word with the top bit set, width, height) and *media*
   packets (a 12-byte header: a u64 with the ``0 C K`` flag bits and a 61-bit PTS in microseconds, then a u32 size;
   then the encoder's payload). The config packet (flag ``C``) carries the parameter sets in Annex-B (SPS and PPS for
   H.264; VPS, SPS and PPS for H.265); the key-frame flag ``K`` marks an IDR (H.265: an IRAP picture).
2. **control**: the client sends control messages (below); the server answers with device messages (the clipboard).

The interface's codec names are ``h264`` and ``hevc``; the server calls the second one ``h265`` (:data:`SERVER_CODECS`).
Audio is never requested (``audio=false``).
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass

#: The scrcpy version this module speaks. The server must be of exactly this version (it refuses another).
SERVER_VERSION = "4.1"

CODEC_H264 = 0x68323634  # "h264"
CODEC_H265 = 0x68323635  # "h265"
#: The codec id the video stream starts with, by the interface's codec name (``VideoCodec`` of the server).
CODEC_IDS = {"h264": CODEC_H264, "hevc": CODEC_H265}
#: The name the server's ``video_codec`` option (and its encoder list) gives each of the interface's codecs.
SERVER_CODECS = {"h264": "h264", "hevc": "h265"}
DEVICE_NAME_LENGTH = 64

_SESSION_FLAG = 1 << 63
_CONFIG_FLAG = 1 << 62
_KEY_FLAG = 1 << 61
_PTS_MASK = _KEY_FLAG - 1

# KeyEvent actions and MotionEvent actions (android.view)
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2
MOTION_ACTION = {"down": ACTION_DOWN, "up": ACTION_UP, "move": ACTION_MOVE}

# ``android.view.KeyEvent`` meta state flags: what modifiers are down (and which lock keys are on) while a key is
# pressed. An injected key event carries them as given: nothing on the device adds the modifiers that were injected
# before, so the client says them in every INJECT_KEYCODE.
META_SHIFT_ON = 0x1
META_ALT_ON = 0x2
META_ALT_LEFT_ON = 0x10
META_ALT_RIGHT_ON = 0x20
META_SHIFT_LEFT_ON = 0x40
META_SHIFT_RIGHT_ON = 0x80
META_CTRL_ON = 0x1000
META_CTRL_LEFT_ON = 0x2000
META_CTRL_RIGHT_ON = 0x4000
META_META_ON = 0x10000
META_META_LEFT_ON = 0x20000
META_META_RIGHT_ON = 0x40000
META_CAPS_LOCK_ON = 0x100000
META_NUM_LOCK_ON = 0x200000

# control message types (ControlMessage.java)
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_EXPAND_NOTIFICATION_PANEL = 5
TYPE_EXPAND_SETTINGS_PANEL = 6
TYPE_COLLAPSE_PANELS = 7
TYPE_GET_CLIPBOARD = 8
TYPE_SET_CLIPBOARD = 9
TYPE_SET_DISPLAY_POWER = 10
TYPE_ROTATE_DEVICE = 11
TYPE_START_APP = 16
TYPE_RESET_VIDEO = 17

#: The most characters one ``inject text`` message may carry (``ControlMessageReader.INJECT_TEXT_MAX_LENGTH``).
INJECT_TEXT_MAX_LENGTH = 300
#: What the server takes of a clipboard text (its own limit: 256 KiB less the message's header), and of an app's name
#: (one length byte).
CLIPBOARD_MAX_BYTES = (1 << 18) - 14
START_APP_MAX_BYTES = 255
#: The server's messages on the control socket (device to client).
DEVICE_CLIPBOARD = 0
DEVICE_ACK_CLIPBOARD = 1
DEVICE_UHID_OUTPUT = 2


class ProtocolError(RuntimeError):
    """The server said something this module does not understand (a version mismatch looks like this)."""


# ---- the video stream -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Session:
    """A new capture session: the picture size changes with it (rotation, a ``RESET_VIDEO``)."""

    width: int
    height: int
    client_resized: bool = False


@dataclass(frozen=True)
class Config:
    """The codec configuration packet: SPS and PPS, Annex-B."""

    data: bytes


@dataclass(frozen=True)
class Media:
    """One access unit as the encoder produced it (without SPS/PPS unless the encoder puts them in)."""

    pts_us: int
    key: bool
    data: bytes


def recv_exact(sock: socket.socket, count: int) -> bytes:
    """Exactly ``count`` bytes; ``EOFError`` when the peer closed first."""
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        chunk = sock.recv(min(remaining, 1 << 20))
        if not chunk:
            raise EOFError("the scrcpy server closed the video socket")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_device_name(raw: bytes) -> str:
    """The device name out of the 64-byte field the server sends first (after the dummy byte)."""
    return raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")


def read_device_name(sock: socket.socket) -> str:
    """The 64-byte device name field the server sends first (after the dummy byte)."""
    return parse_device_name(recv_exact(sock, DEVICE_NAME_LENGTH))


def parse_codec(raw: bytes) -> int:
    """The codec id (u32) out of its 4 bytes. ``0`` and ``1`` are the server saying it could not start the stream."""
    (codec,) = struct.unpack(">I", raw)
    if codec in (0, 1):
        raise ProtocolError("the scrcpy server could not set up the video stream (see its log)")
    return codec


def codec_of(codec_id: int) -> str | None:
    """The interface's name (``h264`` or ``hevc``) of a codec id the video stream starts with, or ``None`` for one the
    HMI does not take (the server also has AV1)."""
    return next((name for name, known in CODEC_IDS.items() if known == codec_id), None)


def read_codec(sock: socket.socket) -> int:
    """The codec id (u32). ``0`` and ``1`` are the server saying it could not start the stream."""
    return parse_codec(recv_exact(sock, 4))


def read_packet(sock: socket.socket) -> Session | Config | Media:
    """The next video packet: a session packet, the codec config, or a media packet."""
    head = recv_exact(sock, 12)
    flags_word = struct.unpack_from(">I", head, 0)[0]
    if flags_word & 0x80000000:
        width, height = struct.unpack_from(">II", head, 4)
        return Session(width=width, height=height, client_resized=bool(flags_word & 1))
    pts_and_flags, size = struct.unpack(">QI", head)
    if size > 64 * 1024 * 1024:
        raise ProtocolError(f"a video packet of {size} bytes: the stream is out of step")
    payload = recv_exact(sock, size) if size else b""
    if pts_and_flags & _CONFIG_FLAG:
        return Config(data=payload)
    return Media(pts_us=pts_and_flags & _PTS_MASK, key=bool(pts_and_flags & _KEY_FLAG), data=payload)


# ---- control messages (client -> server) -------------------------------------------------------------------


def inject_keycode(action: int, keycode: int, repeat: int = 0, meta_state: int = 0) -> bytes:
    """Press (``ACTION_DOWN``) or release (``ACTION_UP``) an Android key.

    The message is 14 bytes: the type, the action (u8), the key code, the repeat count and the meta state (``META_*``,
    the modifiers that are down), each an i32 and big-endian (``ControlMessageReader.parseInjectKeycode``).
    """
    return struct.pack(">BBiii", TYPE_INJECT_KEYCODE, action, keycode, repeat, meta_state)


def inject_text(text: str) -> bytes:
    """Type ``text`` into the focused field (at most :data:`INJECT_TEXT_MAX_LENGTH` characters per message)."""
    data = text[:INJECT_TEXT_MAX_LENGTH].encode("utf-8")
    return struct.pack(">BI", TYPE_INJECT_TEXT, len(data)) + data


def inject_touch(action: int, pointer_id: int, x: int, y: int, width: int, height: int, pressure: float) -> bytes:
    """A touch at ``(x, y)`` of a picture ``width`` x ``height``: the server maps it to the device display.

    The size is the picture the point was aimed at; the server drops the event if it is not the picture it is encoding
    now (the
    device was rotated in between).
    """
    fixed = max(0, min(0xFFFF, int(pressure * 0x10000)))
    return struct.pack(">BBqiiHHHii", TYPE_INJECT_TOUCH_EVENT, action, pointer_id, x, y, width, height, fixed, 0, 0)


def simple(message_type: int) -> bytes:
    """A message that is only its type: rotate, reset video, the panels."""
    return struct.pack(">B", message_type)


def get_clipboard(copy_key: int = 0) -> bytes:
    """Ask the server for the device's clipboard.

    It answers with a :class:`DeviceClipboard`, and says nothing when there is none. ``copy_key`` 0 only reads; 1 and 2
    press copy and cut first (the focused field's selection goes to the clipboard).
    """
    return struct.pack(">BB", TYPE_GET_CLIPBOARD, copy_key)


def set_clipboard(sequence: int, text: str, paste: bool = False) -> bytes:
    """Put ``text`` on the device's clipboard (and paste it into the focused field if ``paste``).

    The server answers with a :class:`DeviceAck` of ``sequence`` (a non-zero number) once it is done.

    Raises:
        ValueError: If the text is longer than the server takes.
    """
    data = text.encode("utf-8")
    if len(data) > CLIPBOARD_MAX_BYTES:
        raise ValueError(f"the clipboard text is {len(data)} bytes; the server takes at most {CLIPBOARD_MAX_BYTES}")
    return struct.pack(">BQ?I", TYPE_SET_CLIPBOARD, sequence, paste, len(data)) + data


def start_app(name: str) -> bytes:
    """Start an app by its package name (the server finds its launcher activity).

    A ``+`` in front force-stops it first; a ``?`` in front searches the installed apps by name.

    Raises:
        ValueError: If the name is empty, has a control character, or is longer than the one length byte allows.
    """
    data = name.encode("utf-8")
    if not 1 <= len(data) <= START_APP_MAX_BYTES or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise ValueError(f"an app's name is 1 to {START_APP_MAX_BYTES} bytes with no control character")
    return struct.pack(">BB", TYPE_START_APP, len(data)) + data


@dataclass(frozen=True)
class DeviceClipboard:
    """The device's clipboard text, as the server sent it (after a :func:`get_clipboard`)."""

    text: str


@dataclass(frozen=True)
class DeviceAck:
    """The server has set the clipboard of the :func:`set_clipboard` with this sequence number."""

    sequence: int


class DeviceMessages:
    """Reads the server's messages on the control socket, however the bytes arrive: ``feed(data)`` gives the complete
    ones.

    The messages: ``0`` a clipboard text (``u32`` length and the UTF-8 text), ``1`` an acknowledgement (``u64``
    sequence), ``2`` a UHID output (``u16`` id, ``u16`` size and the data; nothing here uses UHID, so it is read and
    left out).
    """

    def __init__(self) -> None:
        """Start with nothing buffered."""
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[DeviceClipboard | DeviceAck]:
        """Take more bytes; the messages they completed, in order.

        Raises:
            ProtocolError: If a message has a type this protocol does not have (the stream is out of step).
        """
        self._buf += data
        found: list[DeviceClipboard | DeviceAck] = []
        while self._buf:
            taken = self._take()
            if taken is None:
                break
            message, length = taken
            del self._buf[:length]
            if message is not None:
                found.append(message)
        return found

    def _take(self) -> tuple[DeviceClipboard | DeviceAck | None, int] | None:
        """The first buffered message and the bytes it takes (``None`` as the message: one that is left out), or
        ``None`` when it is not complete yet."""
        buf = self._buf
        kind = buf[0]
        if kind == DEVICE_CLIPBOARD:
            if len(buf) < 5:
                return None
            size = struct.unpack_from(">I", buf, 1)[0]
            if size > CLIPBOARD_MAX_BYTES * 4:
                raise ProtocolError(f"a clipboard message of {size} bytes")
            if len(buf) < 5 + size:
                return None
            return DeviceClipboard(bytes(buf[5 : 5 + size]).decode("utf-8", "replace")), 5 + size
        if kind == DEVICE_ACK_CLIPBOARD:
            if len(buf) < 9:
                return None
            return DeviceAck(struct.unpack_from(">Q", buf, 1)[0]), 9
        if kind == DEVICE_UHID_OUTPUT:
            if len(buf) < 5:
                return None
            size = struct.unpack_from(">H", buf, 3)[0]
            if len(buf) < 5 + size:
                return None
            return None, 5 + size
        raise ProtocolError(f"the server sent a message of type {kind}")


def set_display_power(on: bool) -> bytes:
    """Switch the device's display on or off (the device keeps running)."""
    return struct.pack(">B?", TYPE_SET_DISPLAY_POWER, on)


# ---- Android key codes ----------------------------------------------------------------------------------------

#: ``KEYCODE_*`` of ``android.view.KeyEvent`` by name, without the prefix. Names are matched case-insensitively.
KEYCODES: dict[str, int] = {
    "HOME": 3,
    "BACK": 4,
    "CALL": 5,
    "ENDCALL": 6,
    "DPAD_UP": 19,
    "DPAD_DOWN": 20,
    "DPAD_LEFT": 21,
    "DPAD_RIGHT": 22,
    "DPAD_CENTER": 23,
    "VOLUME_UP": 24,
    "VOLUME_DOWN": 25,
    "POWER": 26,
    "CAMERA": 27,
    "CLEAR": 28,
    "COMMA": 55,
    "PERIOD": 56,
    "TAB": 61,
    "SPACE": 62,
    "ENTER": 66,
    "DEL": 67,
    "MENU": 82,
    "NOTIFICATION": 83,
    "SEARCH": 84,
    "MEDIA_PLAY_PAUSE": 85,
    "MEDIA_STOP": 86,
    "MEDIA_NEXT": 87,
    "MEDIA_PREVIOUS": 88,
    "PAGE_UP": 92,
    "PAGE_DOWN": 93,
    "VOLUME_MUTE": 164,
    "SETTINGS": 176,
    "APP_SWITCH": 187,
    "ASSIST": 219,
    "SLEEP": 223,
    "WAKEUP": 224,
    "ESCAPE": 111,
    "FORWARD_DEL": 112,
    "MOVE_HOME": 122,
    "MOVE_END": 123,
    "MEDIA_PLAY": 126,
    "MEDIA_PAUSE": 127,
    "BRIGHTNESS_DOWN": 220,
    "BRIGHTNESS_UP": 221,
    **{str(d): 7 + d for d in range(10)},
    **{chr(ord("A") + i): 29 + i for i in range(26)},
}

#: What people and UIs call keys that Android calls something else.
KEY_ALIASES: dict[str, str] = {
    "RECENTS": "APP_SWITCH",
    "OVERVIEW": "APP_SWITCH",
    "APPS": "APP_SWITCH",
    "BACKSPACE": "DEL",
    "DELETE": "FORWARD_DEL",
    "RETURN": "ENTER",
    "ESC": "ESCAPE",
    "VOL_UP": "VOLUME_UP",
    "VOL_DOWN": "VOLUME_DOWN",
    "VOLUME_PLUS": "VOLUME_UP",
    "VOLUME_MINUS": "VOLUME_DOWN",
    "MUTE": "VOLUME_MUTE",
    "PLAY_PAUSE": "MEDIA_PLAY_PAUSE",
    "NEXT": "MEDIA_NEXT",
    "PREVIOUS": "MEDIA_PREVIOUS",
    "PREV": "MEDIA_PREVIOUS",
    "UP": "DPAD_UP",
    "DOWN": "DPAD_DOWN",
    "LEFT": "DPAD_LEFT",
    "RIGHT": "DPAD_RIGHT",
    "OK": "DPAD_CENTER",
    "SELECT": "DPAD_CENTER",
    "WAKE": "WAKEUP",
    "ASSISTANT": "ASSIST",
}


def keycode_of(name: str | int) -> int:
    """The Android key code of a key name (``HOME``, ``KEYCODE_BACK``, ``recents``, ``volume_up``) or number.

    Raises:
        ValueError: For a name that is not a key (or a number outside the key code range).
    """
    if isinstance(name, bool):
        raise ValueError(f"not a key: {name!r}")  # noqa: TRY004
    if isinstance(name, float) and name.is_integer():  # a generic call carries every number as a float
        name = int(name)
    if isinstance(name, int):
        if not 0 < name < 1000:
            raise ValueError(f"key code out of range: {name}")
        return name
    text = str(name).strip().upper().replace("-", "_").replace(" ", "_")
    text = text.removeprefix("KEYCODE_")
    text = KEY_ALIASES.get(text, text)
    if text.isdigit() and len(text) > 1:  # "187" is a code; "7" is the digit key
        return keycode_of(int(text))
    try:
        return KEYCODES[text]
    except KeyError:
        raise ValueError(f"unknown key {name!r}") from None
