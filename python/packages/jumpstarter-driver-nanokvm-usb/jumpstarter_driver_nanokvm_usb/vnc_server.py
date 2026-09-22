"""Embedded RFB 3.8 server: JPEG frames in, HID events out."""

from __future__ import annotations

import logging
import os
import select
import socket
import struct
import threading
from collections.abc import Callable
from io import BytesIO
from ipaddress import ip_address
from typing import Protocol

import numpy as np
from PIL import Image

from .frame_pump import FramePump
from .mouse import MouseButton
from .vnc_keymap import char_combo, is_swallowed_keysym, named_key, normalize_layout

logger = logging.getLogger(__name__)

RFB_VERSION = b"RFB 003.008\n"
SEC_NONE = 1
SEC_VNCAUTH = 2
ENCODING_RAW = 0

MSG_SET_PIXEL_FORMAT = 0
MSG_SET_ENCODINGS = 2
MSG_FB_UPDATE_REQUEST = 3
MSG_KEY_EVENT = 4
MSG_POINTER_EVENT = 5
MSG_CLIENT_CUT_TEXT = 6
_CLIENT_IO_TIMEOUT = 30


class HidTarget(Protocol):
    def hid_key(self, key: str, down: bool) -> None: ...
    def hid_char(self, key: str, modifiers: frozenset[str], down: bool) -> None: ...
    def mouse_pointer(self, x: float, y: float, buttons: int, wheel: int = 0) -> None: ...


def keysym_to_key(keysym: int, layout: str = "us") -> str | None:
    """Map a named RFB keysym, or the HID key of a printable on ``layout``."""
    named = named_key(keysym)
    if named is not None:
        return named
    combo = char_combo(keysym, layout)
    if combo is None:
        return None
    return combo[0]


def rfb_buttons_to_hid(mask: int) -> tuple[int, int]:
    """Translate RFB pointer button mask to HID buttons and wheel delta."""
    hid = 0
    if mask & 0x01:
        hid |= MouseButton.LEFT
    if mask & 0x02:
        hid |= MouseButton.MIDDLE
    if mask & 0x04:
        hid |= MouseButton.RIGHT
    if mask & 0x80:
        hid |= MouseButton.BACK
    wheel = 0
    if mask & 0x08:
        wheel = 1
    elif mask & 0x10:
        wheel = -1
    return hid, wheel


def is_loopback_bind(host: str) -> bool:
    """Return True if ``host`` only accepts connections from the local machine."""
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _recvexact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("RFB client closed the connection")
        buf.extend(chunk)
    return bytes(buf)


def _bitrev8(value: int) -> int:
    value = ((value * 0x0202020202) & 0x010884422010) % 1023
    return value


def vnc_auth_response(challenge: bytes, password: str) -> bytes:
    """Encrypt the 16-byte VNC challenge with the bit-reversed DES password."""
    if len(challenge) != 16:
        raise ValueError("VNC challenge must be 16 bytes")
    key = password.encode("latin-1", "replace")[:8].ljust(8, b"\x00")
    des_key = bytes(_bitrev8(b) for b in key)
    return _des_ecb_encrypt(challenge[:8], des_key) + _des_ecb_encrypt(challenge[8:], des_key)


# DES encrypt (ECB, one 8-byte block). Tables from FIPS 46-3 / public domain d3des.
# fmt: off
_IP = (
    58, 50, 42, 34, 26, 18, 10, 2, 60, 52, 44, 36, 28, 20, 12, 4,
    62, 54, 46, 38, 30, 22, 14, 6, 64, 56, 48, 40, 32, 24, 16, 8,
    57, 49, 41, 33, 25, 17, 9, 1, 59, 51, 43, 35, 27, 19, 11, 3,
    61, 53, 45, 37, 29, 21, 13, 5, 63, 55, 47, 39, 31, 23, 15, 7,
)
_FP = (
    40, 8, 48, 16, 56, 24, 64, 32, 39, 7, 47, 15, 55, 23, 63, 31,
    38, 6, 46, 14, 54, 22, 62, 30, 37, 5, 45, 13, 53, 21, 61, 29,
    36, 4, 44, 12, 52, 20, 60, 28, 35, 3, 43, 11, 51, 19, 59, 27,
    34, 2, 42, 10, 50, 18, 58, 26, 33, 1, 41, 9, 49, 17, 57, 25,
)
_E = (
    32, 1, 2, 3, 4, 5, 4, 5, 6, 7, 8, 9, 8, 9, 10, 11, 12, 13,
    12, 13, 14, 15, 16, 17, 16, 17, 18, 19, 20, 21, 20, 21, 22, 23, 24, 25,
    24, 25, 26, 27, 28, 29, 28, 29, 30, 31, 32, 1,
)
_P = (
    16, 7, 20, 21, 29, 12, 28, 17, 1, 15, 23, 26, 5, 18, 31, 10,
    2, 8, 24, 14, 32, 27, 3, 9, 19, 13, 30, 6, 22, 11, 4, 25,
)
_PC1 = (
    57, 49, 41, 33, 25, 17, 9, 1, 58, 50, 42, 34, 26, 18,
    10, 2, 59, 51, 43, 35, 27, 19, 11, 3, 60, 52, 44, 36,
    63, 55, 47, 39, 31, 23, 15, 7, 62, 54, 46, 38, 30, 22,
    14, 6, 61, 53, 45, 37, 29, 21, 13, 5, 28, 20, 12, 4,
)
_PC2 = (
    14, 17, 11, 24, 1, 5, 3, 28, 15, 6, 21, 10,
    23, 19, 12, 4, 26, 8, 16, 7, 27, 20, 13, 2,
    41, 52, 31, 37, 47, 55, 30, 40, 51, 45, 33, 48,
    44, 49, 39, 56, 34, 53, 46, 42, 50, 36, 29, 32,
)
_SHIFTS = (1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1)
_SBOX = (
    (
        14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7,
        0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8,
        4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0,
        15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13,
    ),
    (
        15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10,
        3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5,
        0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15,
        13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9,
    ),
    (
        10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8,
        13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1,
        13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7,
        1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12,
    ),
    (
        7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15,
        13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9,
        10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4,
        3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14,
    ),
    (
        2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9,
        14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6,
        4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14,
        11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3,
    ),
    (
        12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11,
        10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8,
        9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6,
        4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13,
    ),
    (
        4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1,
        13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6,
        1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2,
        6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12,
    ),
    (
        13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7,
        1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2,
        7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8,
        2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11,
    ),
)
# fmt: on


def _permute(value: int, table: tuple[int, ...], nbits: int) -> int:
    out = 0
    for bit in table:
        out = (out << 1) | ((value >> (nbits - bit)) & 1)
    return out


def _des_subkeys(key: bytes) -> list[int]:
    k = int.from_bytes(key, "big")
    cd = _permute(k, _PC1, 64)
    c = (cd >> 28) & 0xFFFFFFF
    d = cd & 0xFFFFFFF
    keys = []
    for shift in _SHIFTS:
        c = ((c << shift) | (c >> (28 - shift))) & 0xFFFFFFF
        d = ((d << shift) | (d >> (28 - shift))) & 0xFFFFFFF
        keys.append(_permute((c << 28) | d, _PC2, 56))
    return keys


def _des_f(r: int, subkey: int) -> int:
    er = _permute(r, _E, 32) ^ subkey
    s = 0
    for i in range(8):
        chunk = (er >> (42 - 6 * i)) & 0x3F
        row = ((chunk & 0x20) >> 4) | (chunk & 1)
        col = (chunk >> 1) & 0xF
        s = (s << 4) | _SBOX[i][row * 16 + col]
    return _permute(s, _P, 32)


def _des_ecb_encrypt(block: bytes, key: bytes) -> bytes:
    ip = _permute(int.from_bytes(block, "big"), _IP, 64)
    left, right = ip >> 32, ip & 0xFFFFFFFF
    for subkey in _des_subkeys(key):
        left, right = right, left ^ _des_f(right, subkey)
    preout = ((right & 0xFFFFFFFF) << 32) | (left & 0xFFFFFFFF)
    return _permute(preout, _FP, 64).to_bytes(8, "big")


def _default_pixel_format() -> dict[str, int]:
    return {
        "bits_per_pixel": 32,
        "depth": 24,
        "big_endian": 0,
        "true_colour": 1,
        "red_max": 255,
        "green_max": 255,
        "blue_max": 255,
        "red_shift": 16,
        "green_shift": 8,
        "blue_shift": 0,
    }


def _pack_pixel_format(pf: dict[str, int]) -> bytes:
    return struct.pack(
        "!BBBBHHHBBB3x",
        pf["bits_per_pixel"],
        pf["depth"],
        pf["big_endian"],
        pf["true_colour"],
        pf["red_max"],
        pf["green_max"],
        pf["blue_max"],
        pf["red_shift"],
        pf["green_shift"],
        pf["blue_shift"],
    )


def _unpack_pixel_format(data: bytes) -> dict[str, int]:
    values = struct.unpack("!BBBBHHHBBB3x", data)
    keys = (
        "bits_per_pixel",
        "depth",
        "big_endian",
        "true_colour",
        "red_max",
        "green_max",
        "blue_max",
        "red_shift",
        "green_shift",
        "blue_shift",
    )
    return dict(zip(keys, values, strict=True))


def pack_rgb_frame(image: Image.Image, pf: dict[str, int]) -> bytes:
    """Pack an RGB image into RFB raw pixels for ``pf``."""
    rgb = image.convert("RGB")
    bpp = pf["bits_per_pixel"]
    if bpp not in (16, 32):
        bpp = 32
        pf = _default_pixel_format()
    arr = np.asarray(rgb, dtype=np.uint16 if bpp == 16 else np.uint32)
    r = arr[:, :, 0].astype(np.uint32)
    g = arr[:, :, 1].astype(np.uint32)
    b = arr[:, :, 2].astype(np.uint32)
    r = r * pf["red_max"] // 255
    g = g * pf["green_max"] // 255
    b = b * pf["blue_max"] // 255
    pix = (r << pf["red_shift"]) | (g << pf["green_shift"]) | (b << pf["blue_shift"])
    dtype = ">u4" if pf["big_endian"] else "<u4"
    if bpp == 16:
        dtype = ">u2" if pf["big_endian"] else "<u2"
    return pix.astype(dtype).tobytes()


class RfbServer:
    """Listen on a Unix socket (and optional TCP) and serve RFB sessions."""

    def __init__(
        self,
        path: str,
        pump: FramePump | Callable[[], FramePump | None],
        hid: HidTarget,
        *,
        width: int = 1920,
        height: int = 1080,
        password: str | None = None,
        tcp_port: int | None = None,
        tcp_bind: str = "127.0.0.1",
        layout: str = "us",
        on_client: Callable[[], None] | None = None,
    ) -> None:
        self.path = path
        self._pump = pump
        self._hid = hid
        self._width = max(1, int(width))
        self._height = max(1, int(height))
        self._password = password
        self._tcp_port = tcp_port
        self._tcp_bind = tcp_bind
        self._layout = normalize_layout(layout)
        self._on_client = on_client
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listen: socket.socket | None = None
        self._tcp: socket.socket | None = None
        self._clients: list[socket.socket] = []
        self._clients_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None:
            return
        if os.path.exists(self.path):
            os.unlink(self.path)
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        listen = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listen.bind(self.path)
        listen.listen(4)
        listen.setblocking(False)
        self._listen = listen
        if self._tcp_port is not None:
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind((self._tcp_bind, self._tcp_port))
            tcp.listen(4)
            tcp.setblocking(False)
            self._tcp = tcp
            self._tcp_port = tcp.getsockname()[1]
        self._stop.clear()
        self._thread = threading.Thread(target=self._accept_loop, name="nanokvm-usb-rfb", daemon=True)
        self._thread.start()
        if self._tcp is not None:
            logger.info("RFB server listening on %s and tcp %s:%s", self.path, self._tcp_bind, self._tcp_port)
        else:
            logger.info("RFB server listening on %s", self.path)

    @property
    def tcp_endpoint(self) -> tuple[str, int] | None:
        if self._tcp is None:
            return None
        addr = self._tcp.getsockname()
        return addr[0], addr[1]

    def stop(self) -> None:
        self._stop.set()
        for sock in (self._listen, self._tcp):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            try:
                client.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        self._listen = None
        self._tcp = None
        if os.path.exists(self.path):
            try:
                os.unlink(self.path)
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            socks = [s for s in (self._listen, self._tcp) if s is not None]
            if not socks:
                break
            try:
                readable, _, _ = select.select(socks, [], [], 0.5)
            except (OSError, ValueError):
                break
            for listener in readable:
                try:
                    conn, _addr = listener.accept()
                except OSError:
                    continue
                conn.settimeout(_CLIENT_IO_TIMEOUT)
                with self._clients_lock:
                    self._clients.append(conn)
                threading.Thread(
                    target=self._session,
                    args=(conn,),
                    name="nanokvm-usb-rfb-client",
                    daemon=True,
                ).start()

    def _session(self, conn: socket.socket) -> None:
        try:
            if self._on_client is not None:
                self._on_client()
            self._handshake(conn)
            self._serve(conn)
        except (ConnectionError, OSError, TimeoutError, struct.error) as exc:
            logger.debug("RFB client disconnected: %s", exc)
        except Exception:
            logger.exception("RFB session failed")
        finally:
            try:
                conn.close()
            except OSError:
                pass
            with self._clients_lock:
                if conn in self._clients:
                    self._clients.remove(conn)

    def _handshake(self, conn: socket.socket) -> None:
        conn.sendall(RFB_VERSION)
        client_ver = _recvexact(conn, 12)
        if not client_ver.startswith(b"RFB "):
            raise ConnectionError(f"invalid RFB version: {client_ver!r}")
        if self._password:
            conn.sendall(bytes([1, SEC_VNCAUTH]))
            chosen = _recvexact(conn, 1)[0]
            if chosen != SEC_VNCAUTH:
                conn.sendall(struct.pack("!I", 1))
                raise ConnectionError("client did not select VncAuth")
            challenge = os.urandom(16)
            conn.sendall(challenge)
            response = _recvexact(conn, 16)
            if response != vnc_auth_response(challenge, self._password):
                conn.sendall(struct.pack("!I", 1))
                raise ConnectionError("VNC authentication failed")
            conn.sendall(struct.pack("!I", 0))
        else:
            conn.sendall(bytes([1, SEC_NONE]))
            chosen = _recvexact(conn, 1)[0]
            if chosen != SEC_NONE:
                conn.sendall(struct.pack("!I", 1))
                raise ConnectionError("client did not select None security")
            conn.sendall(struct.pack("!I", 0))
        _recvexact(conn, 1)  # ClientInit shared-flag
        name = b"NanoKVM-USB"
        conn.sendall(
            struct.pack("!HH", self._width, self._height)
            + _pack_pixel_format(_default_pixel_format())
            + struct.pack("!I", len(name))
            + name
        )

    def _serve(self, conn: socket.socket) -> None:
        pf = _default_pixel_format()
        last_gen = -1
        want_update = True
        conn.settimeout(0)
        while not self._stop.is_set():
            try:
                readable, _, _ = select.select([conn], [], [], 0.05)
            except (OSError, ValueError):
                break
            if readable:
                result = self._read_client_message(conn, pf)
                if result is None:
                    break
                pf, requested = result
                if requested:
                    want_update = True
            if want_update:
                sent = self._send_frame(conn, pf, last_gen)
                if sent is not None:
                    last_gen = sent
                    want_update = False

    def _read_client_message(  # noqa: C901
        self, conn: socket.socket, pf: dict[str, int]
    ) -> tuple[dict[str, int], bool] | None:
        try:
            header = conn.recv(1)
        except BlockingIOError:
            header = b""
        if not header:
            return None
        msg = header[0]
        requested = False
        conn.settimeout(_CLIENT_IO_TIMEOUT)
        try:
            if msg == MSG_SET_PIXEL_FORMAT:
                _recvexact(conn, 3)
                pf = _unpack_pixel_format(_recvexact(conn, 16))
            elif msg == MSG_SET_ENCODINGS:
                _recvexact(conn, 1)
                count = struct.unpack("!H", _recvexact(conn, 2))[0]
                if count:
                    _recvexact(conn, 4 * count)
            elif msg == MSG_FB_UPDATE_REQUEST:
                _recvexact(conn, 9)
                requested = True
            elif msg == MSG_KEY_EVENT:
                payload = _recvexact(conn, 7)
                down = payload[0] != 0
                keysym = struct.unpack("!I", payload[3:7])[0]
                self._handle_key(keysym, down)
            elif msg == MSG_POINTER_EVENT:
                payload = _recvexact(conn, 5)
                mask, x, y = struct.unpack("!BHH", payload)
                self._handle_pointer(mask, x, y)
            elif msg == MSG_CLIENT_CUT_TEXT:
                _recvexact(conn, 3)
                length = struct.unpack("!I", _recvexact(conn, 4))[0]
                if length:
                    _recvexact(conn, length)
            else:
                logger.debug("ignoring unknown RFB client message %s", msg)
        finally:
            conn.settimeout(0)
        return pf, requested

    def _handle_key(self, keysym: int, down: bool) -> None:
        if is_swallowed_keysym(keysym):
            return
        named = named_key(keysym)
        if named is not None:
            try:
                self._hid.hid_key(named, down)
            except Exception:
                logger.debug("HID key event failed", exc_info=True)
            return
        combo = char_combo(keysym, self._layout)
        if combo is None:
            logger.debug("unmapped RFB keysym 0x%04x", keysym)
            return
        key, modifiers = combo
        try:
            self._hid.hid_char(key, modifiers, down)
        except Exception:
            logger.debug("HID character event failed", exc_info=True)

    def _handle_pointer(self, mask: int, x: int, y: int) -> None:
        nx = 0.0 if self._width <= 1 else max(0.0, min(1.0, x / (self._width - 1)))
        ny = 0.0 if self._height <= 1 else max(0.0, min(1.0, y / (self._height - 1)))
        buttons, wheel = rfb_buttons_to_hid(mask)
        try:
            self._hid.mouse_pointer(nx, ny, buttons, wheel)
        except Exception:
            logger.debug("HID pointer event failed", exc_info=True)

    def _send_frame(self, conn: socket.socket, pf: dict[str, int], last_gen: int) -> int | None:
        pump = self._pump() if callable(self._pump) else self._pump
        if pump is None:
            return None
        got = pump.wait_jpeg(timeout=0.05, after_generation=last_gen if last_gen >= 0 else None)
        if got is None:
            return None
        jpeg, gen = got
        try:
            image = Image.open(BytesIO(jpeg)).convert("RGB")
        except Exception:
            logger.debug("failed to decode JPEG for RFB", exc_info=True)
            return gen
        if image.size != (self._width, self._height):
            image = image.resize((self._width, self._height))
        pixels = pack_rgb_frame(image, pf)
        header = struct.pack("!BxH", 0, 1) + struct.pack("!HHHHi", 0, 0, self._width, self._height, ENCODING_RAW)
        conn.settimeout(_CLIENT_IO_TIMEOUT)
        try:
            conn.sendall(header + pixels)
        finally:
            conn.settimeout(0)
        return gen
