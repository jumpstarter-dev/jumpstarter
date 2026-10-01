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

from .des import vnc_auth_response
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


def _is_default_32le(pf: dict[str, int]) -> bool:
    return (
        pf.get("bits_per_pixel") == 32
        and pf.get("big_endian") == 0
        and pf.get("true_colour") == 1
        and pf.get("red_max") == 255
        and pf.get("green_max") == 255
        and pf.get("blue_max") == 255
        and pf.get("red_shift") == 16
        and pf.get("green_shift") == 8
        and pf.get("blue_shift") == 0
    )


def _bgrx_with_zero_pad(arr: np.ndarray) -> bytes:
    out = np.array(arr, dtype=np.uint8, copy=True, order="C")
    out[:, :, 3] = 0
    return out.tobytes()


def _pack_default_32le(image: Image.Image) -> bytes:
    """Pack to RFB raw 32bpp LE (B, G, R, 0 bytes in memory)."""
    mode = image.mode
    if mode in ("BGRX", "BGRA"):
        arr = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(image.height, image.width, 4)
        return _bgrx_with_zero_pad(arr)
    if mode in ("RGBX", "RGBA"):
        arr = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(image.height, image.width, 4)
        return _bgrx_with_zero_pad(arr[:, :, [2, 1, 0, 3]])
    rgbx = image.convert("RGBX")
    arr = np.frombuffer(rgbx.tobytes(), dtype=np.uint8).reshape(image.height, image.width, 4)
    return _bgrx_with_zero_pad(arr[:, :, [2, 1, 0, 3]])


def pack_rgb_frame(image: Image.Image, pf: dict[str, int]) -> bytes:
    """Pack an RGB image into RFB raw pixels for ``pf``."""
    bpp = pf["bits_per_pixel"]
    if bpp not in (16, 32):
        bpp = 32
        pf = _default_pixel_format()
    if bpp == 32 and _is_default_32le(pf):
        return _pack_default_32le(image)
    rgb = image.convert("RGB")
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
        max_clients: int = 2,
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
        self._max_clients = max(1, int(max_clients))
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
        listen.listen(self._max_clients)
        listen.setblocking(False)
        self._listen = listen
        if self._tcp_port is not None:
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind((self._tcp_bind, self._tcp_port))
            tcp.listen(self._max_clients)
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
                    if len(self._clients) >= self._max_clients:
                        try:
                            conn.close()
                        except OSError:
                            pass
                        continue
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
        last_pixels: bytes | None = None
        want_update = True
        force_full = True
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
                pf, requested, incremental = result
                if requested:
                    want_update = True
                    if not incremental:
                        force_full = True
            if want_update:
                sent = self._send_frame(conn, pf, last_gen, last_pixels, force_full)
                if sent is not None:
                    last_gen, last_pixels, did_send = sent
                    if did_send:
                        want_update = False
                        force_full = False

    def _read_client_message(  # noqa: C901
        self, conn: socket.socket, pf: dict[str, int]
    ) -> tuple[dict[str, int], bool, bool] | None:
        try:
            header = conn.recv(1)
        except BlockingIOError:
            header = b""
        if not header:
            return None
        msg = header[0]
        requested = False
        incremental = True
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
                payload = _recvexact(conn, 9)
                incremental = payload[0] != 0
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
        return pf, requested, incremental

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

    def _send_frame(
        self,
        conn: socket.socket,
        pf: dict[str, int],
        last_gen: int,
        last_pixels: bytes | None,
        force_full: bool,
    ) -> tuple[int, bytes | None, bool] | None:
        pump = self._pump() if callable(self._pump) else self._pump
        if pump is None:
            return None
        got = pump.wait_jpeg(timeout=0.05, after_generation=last_gen if last_gen >= 0 else None)
        if got is None:
            return None
        jpeg, gen = got
        try:
            image = Image.open(BytesIO(jpeg))
            image.load()
        except Exception:
            logger.debug("failed to decode JPEG for RFB", exc_info=True)
            return gen, last_pixels, False
        if image.size != (self._width, self._height):
            image = image.resize((self._width, self._height))
        pixels = pack_rgb_frame(image, pf)
        conn.settimeout(_CLIENT_IO_TIMEOUT)
        try:
            if not force_full and last_pixels is not None and pixels == last_pixels:
                conn.sendall(struct.pack("!BxH", 0, 0))
            else:
                header = struct.pack("!BxH", 0, 1) + struct.pack(
                    "!HHHHi", 0, 0, self._width, self._height, ENCODING_RAW
                )
                conn.sendall(header + pixels)
        finally:
            conn.settimeout(0)
        return gen, pixels, True
