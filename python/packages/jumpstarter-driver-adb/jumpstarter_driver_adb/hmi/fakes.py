"""A fake device, a fake ``adb`` and a fake ``scrcpy-server``: the HMI's own code runs against a real socket, with no
device, no adb and no network.

Not collected as tests; the tests of this package import what they need::

    driver, adb = build_hmi(tmp_path, display_id=0)       # an AdbDeviceHmi on a FakeDevice and a FakeAdb
    with serve(driver) as client, client.video() as video:   # the real client, framing and hub in the loop
        ...

:class:`FakeScrcpyServer` listens like the device's abstract socket behind ``adb forward`` (the dummy byte, the device
name, the codec id, a session packet, then pictures; control messages are read and kept), so the session connects to it
as it would to a device. It makes the codec it was started for (``video_codec=h264`` or ``h265`` on the command line
that :class:`FakeAdb` is given): synthetic H.264 pictures, or the real HEVC test pattern of the HMI package, with the
parameter sets in the config packet and none in the key pictures, as scrcpy sends them. :class:`FakeAdb` stands in for
:class:`~jumpstarter_driver_adb.hmi.adb.Adb`: it remembers the files "on the device", hands out a new server for each
``forward``, and records every call.
"""

from __future__ import annotations

import hashlib
import logging
import re
import socket
import struct
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jumpstarter_driver_hmi.hub.codecs import HEVC_AUD, HEVC_PPS, HEVC_SPS, HEVC_VPS, has_idr, iter_nals, nal_begin
from jumpstarter_driver_hmi.hub.codecs import parameter_sets as parameter_sets_of
from jumpstarter_driver_hmi.testing import make_sps
from jumpstarter_driver_hmi.testing.pattern import pattern_units

from .driver import AdbDeviceHmi

_START = b"\x00\x00\x00\x01"
#: The PPS that goes with :func:`~jumpstarter_driver_hmi.testing.make_sps` (baseline profile, CAVLC).
PPS = b"\x68\xce\x3c\x80"
#: A key slice and a delta slice with some payload: the HMI only reads their headers.
IDR = _START + bytes([0x65, 0x88, 0x84, 0x00, 0x2F, 0xFF]) + b"\x11" * 200
DELTA = _START + bytes([0x41, 0x9A, 0x10, 0x30]) + b"\x22" * 40
#: The codec string of the fake's H.264 pictures (a baseline-profile SPS, level 3.1).
CODEC = "avc1.42C01F"
#: The codec string of the fake's HEVC pictures: the test pattern's own SPS (main profile, level 2.0).
HEVC_CODEC = "hev1.1.6.L60.90"
#: The codec id the video stream starts with, by the server's name for the codec (``VideoCodec`` of the server).
CODEC_IDS = {"h264": 0x68323634, "h265": 0x68323635, "av1": 0x00617631}
#: Bytes of each control message by type (type byte included); the others are told by their own length field.
_CONTROL_SIZES = {0: 14, 2: 32, 4: 2, 5: 1, 6: 1, 7: 1, 8: 2, 10: 2, 11: 1, 17: 1}


def parameter_sets(width: int, height: int) -> bytes:
    """An SPS and a PPS (with start codes) for a picture of this size."""
    return _START + make_sps(width, height) + _START + PPS


def hevc_stream() -> tuple[bytes, tuple[tuple[bytes, bool], ...]]:
    """The HEVC test pattern as scrcpy sends it: the parameter sets (VPS, SPS, PPS) for the config packet, and the
    pictures as ``(unit, key)`` with nothing but their slices (an encoder's output has no delimiters, and the key
    pictures none of the parameter sets: the session puts them in front)."""
    units = pattern_units("hevc")
    sets = {HEVC_AUD, HEVC_VPS, HEVC_SPS, HEVC_PPS}
    pictures = []
    for unit in units:
        spans = [(kind, nal_begin(unit, start)) for kind, start, _ in iter_nals(unit, "hevc")]
        ends = [begin for _, begin in spans[1:]] + [len(unit)]
        bare = b"".join(unit[begin:end] for (kind, begin), end in zip(spans, ends, strict=True) if kind not in sets)
        pictures.append((bare, has_idr(unit, "hevc")))
    return parameter_sets_of(units[0], "hevc"), tuple(pictures)


def session_packet(width: int, height: int) -> bytes:
    """The video socket's session packet: the picture's size."""
    return struct.pack(">III", 0x80000000, width, height)


def media_packet(pts_us: int, payload: bytes, *, key: bool = False, config: bool = False) -> bytes:
    """One packet of the video socket: a picture (``key``: an IDR), or the codec configuration (``config``)."""
    flags = (1 << 62) if config else (pts_us | ((1 << 61) if key else 0))
    return struct.pack(">QI", flags, len(payload)) + payload


class FakeScrcpyServer:
    """The device's side of the two sockets: video first (dummy byte, name, codec, packets), then control."""

    def __init__(
        self,
        width: int = 540,
        height: int = 1200,
        fps: float = 30.0,
        frames: int = 10_000,
        expect_control: bool = True,
        codec: str | None = None,
    ) -> None:
        """Listen on a port of its own; ``frames`` pictures are made (then the display is "still").

        ``codec`` is what the server makes, by its own names (``h264``, ``h265``, ``av1``): ``None`` is the one it is
        started for (the ``video_codec`` of the command line, H.264 without one), and another is a server that did not
        do as it was asked.
        """
        self.width, self.height, self.fps, self.max_frames = width, height, fps, frames
        self.expect_control = expect_control
        self.codec = codec
        #: The ``video_codec`` of the command line this server was started with (set by :class:`FakeAdb`).
        self.asked = "h264"
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.port = self.listener.getsockname()[1]
        #: Every control message received, in order, as its bytes.
        self.control_messages: list[bytes] = []
        #: The device's clipboard: what SET_CLIPBOARD puts there and GET_CLIPBOARD answers with (``None``: empty, and
        #: the server says nothing).
        self.clipboard: str | None = None
        #: Whether the fake acknowledges a SET_CLIPBOARD (the real server does).
        self.ack_clipboard = True
        #: Whether RESET_VIDEO is answered with a new session and a key picture (the real server does).
        self.answer_resets = True
        self.control_buffer = bytearray()
        self.resets = 0
        self.connections = 0
        self.stopped = threading.Event()
        #: Cleared, the fake stops reading its control socket (a device that does not take input any more).
        self.reading_control = threading.Event()
        self.reading_control.set()
        self._want_idr = False
        self._video: socket.socket | None = None
        self._control: socket.socket | None = None
        self._lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()

    # -- the knobs -------------------------------------------------------------------------------------------------

    def change_picture(self, width: int, height: int) -> None:
        """The device was rotated (or its display resized): a new session packet, new parameter sets, a key picture."""
        with self._lock:
            self.width, self.height = width, height
            self._announce()

    @property
    def making(self) -> str:
        """The codec the server makes, by its own name."""
        return self.codec or self.asked

    def stop_reading_control(self) -> None:
        """Stop reading the control socket (what is sent after this piles up until adb's buffers are full)."""
        self.reading_control.clear()

    def touches(self) -> list[tuple]:
        """The touch messages received, as ``("touch", kind, x, y)``."""
        found = []
        for message in list(self.control_messages):
            if message[0] == 2:
                x, y = struct.unpack_from(">ii", message, 10)
                found.append(("touch", {0: "down", 1: "up", 2: "move"}[message[1]], x, y))
        return found

    def key_events(self) -> list[tuple[int, int, int]]:
        """The key messages received, as ``(action, keycode, repeat)``."""
        return [(m[1], *struct.unpack_from(">ii", m, 2)) for m in list(self.control_messages) if m[0] == 0]

    def key_messages(self) -> list[tuple[int, int, int, int]]:
        """The key messages received, as ``(action, keycode, repeat, meta state)``."""
        return [(m[1], *struct.unpack_from(">iii", m, 2)) for m in list(self.control_messages) if m[0] == 0]

    def messages_of(self, kind: int) -> list[bytes]:
        """The control messages of one type, as received."""
        return [m for m in list(self.control_messages) if m[0] == kind]

    # -- the sockets -----------------------------------------------------------------------------------------------

    def _accept(self) -> None:
        try:
            video, _ = self.listener.accept()
            self.connections += 1
            self._video = video
            video.sendall(b"\x00")  # the dummy byte
            control = None
            if self.expect_control:
                control, _ = self.listener.accept()  # like the real server: nothing more is said until both are there
                self.connections += 1
                self._control = control
            video.sendall(b"Fake Phone".ljust(64, b"\x00"))
            video.sendall(struct.pack(">I", CODEC_IDS[self.making]))
            if control is not None:
                threading.Thread(target=self._read_control, args=(control,), daemon=True).start()
            threading.Thread(target=self._stream, args=(video,), daemon=True).start()
        except OSError:
            return

    def _announce(self) -> None:
        """The picture's size and parameter sets, and a key picture to follow (lock held)."""
        video = self._video
        if video is None:
            return
        video.sendall(session_packet(self.width, self.height))
        if self.making == "h265":
            video.sendall(media_packet(0, hevc_stream()[0], config=True))
        else:
            video.sendall(media_packet(0, parameter_sets(self.width, self.height), config=True))
        self._want_idr = True

    def _picture(self, index: int, cursor: int) -> tuple[bytes, bool, int]:
        """``(the picture, whether it is a key picture, the cursor after it)`` (lock held). H.264: a synthetic IDR every
        30 pictures and when one is wanted. HEVC: the test pattern's pictures in order, from its first (a key picture)
        when one is wanted."""
        if self.making == "h265":
            pictures = hevc_stream()[1]
            cursor = 0 if self._want_idr else cursor
            self._want_idr = False
            unit, key = pictures[cursor % len(pictures)]
            return unit, key, cursor + 1
        key = index % 30 == 0 or self._want_idr
        self._want_idr = False
        return (IDR if key else DELTA), key, cursor

    def _stream(self, video: socket.socket) -> None:
        try:
            with self._lock:
                self._announce()
        except OSError:  # the session ended before the first picture
            return
        index = 0
        cursor = 0
        start = time.monotonic()
        while not self.stopped.is_set() and index < self.max_frames:
            pts = int(index * 1_000_000 / self.fps)
            with self._lock:
                try:
                    picture, key, cursor = self._picture(index, cursor)
                    video.sendall(media_packet(pts, picture, key=key))
                except OSError:
                    return
            index += 1
            wait = start + index / self.fps - time.monotonic()
            if wait > 0:
                time.sleep(wait)

    def _next_message(self) -> bytes | None:
        """The first complete control message in the buffer (removed from it), or ``None``."""
        buffer = self.control_buffer
        kind = buffer[0]
        if kind == 1:  # text: u32 length + bytes
            size = 5 + struct.unpack_from(">I", buffer, 1)[0] if len(buffer) >= 5 else None
        elif kind == 9:  # SET_CLIPBOARD: u64 sequence, u8 paste, u32 length + bytes
            size = 14 + struct.unpack_from(">I", buffer, 10)[0] if len(buffer) >= 14 else None
        elif kind == 16:  # START_APP: u8 length + the name
            size = 2 + buffer[1] if len(buffer) >= 2 else None
        else:
            size = _CONTROL_SIZES[kind]
        if size is None or len(buffer) < size:
            return None
        message = bytes(buffer[:size])
        del buffer[:size]
        return message

    def _answer(self, control: socket.socket, message: bytes) -> None:
        kind = message[0]
        if kind == 9:
            (sequence,) = struct.unpack_from(">Q", message, 1)
            self.clipboard = message[14:].decode("utf-8")
            if self.ack_clipboard:
                control.sendall(struct.pack(">BQ", 1, sequence))
        elif kind == 8 and self.clipboard is not None:
            data = self.clipboard.encode("utf-8")
            control.sendall(struct.pack(">BI", 0, len(data)) + data)
        elif kind == 17:  # RESET_VIDEO: a new capture session, same size
            self.resets += 1
            if self.answer_resets:
                with self._lock:
                    self._announce()

    def _read_control(self, control: socket.socket) -> None:
        try:
            while True:
                self.reading_control.wait()
                data = control.recv(4096)
                if not data:
                    return
                self.control_buffer += data
                while self.control_buffer and (message := self._next_message()) is not None:
                    self.control_messages.append(message)
                    self._answer(control, message)
        except OSError:
            return

    def close(self) -> None:
        """Stop making pictures and close what is open."""
        self.stopped.set()
        self.reading_control.set()
        for sock in (self._video, self._control, self.listener):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass


class FakeProcess:
    """What ``adb shell app_process ...`` returns: a pipe of log lines that ends when the process does."""

    def __init__(self) -> None:
        """Running, with one line of log."""
        self.returncode: int | None = None
        self._done = threading.Event()
        self.stdout = self._lines()

    def _lines(self):
        yield "[server] INFO: Device: Fake Phone\n"
        self._done.wait()

    def poll(self):
        """The return code, once it has ended."""
        return self.returncode

    def terminate(self):
        """End it politely."""
        self.returncode = -15
        self._done.set()

    def kill(self):
        """End it."""
        self.returncode = -9
        self._done.set()

    def wait(self, timeout=None):
        """Wait for it to end; its return code."""
        self._done.wait(timeout)
        return self.returncode


class FakeAdb:
    """The methods of :class:`~jumpstarter_driver_adb.hmi.adb.Adb` that the HMI uses, against a
    :class:`FakeScrcpyServer`. Every call is recorded in :attr:`calls`."""

    def __init__(self, make_server: Callable[[], Any] = FakeScrcpyServer) -> None:
        """A fresh server is made for every ``forward``, as a device starts one for every start."""
        self.make_server = make_server
        self.servers: list[Any] = []
        self.calls: list[tuple] = []
        self.process: FakeProcess | None = None
        self.keyevents: list[tuple[int, bool]] = []
        #: ``remote path -> sha256`` of the files "on the device".
        self.device_files: dict[str, str] = {}
        #: What ``server_alive`` answers: ``True``, ``False``, or ``None`` (adb did not answer).
        self.server_is_alive: bool | None = True
        self.wm_size = "Physical size: 1080x2340\n"
        #: What the server prints for ``list_encoders=true`` (shortened).
        self.encoder_listing = (
            "[server] INFO: Device: [example] example Phone (Android 17)\n"
            "[server] INFO: List of video encoders:\n"
            "    --video-codec=h264 --video-encoder=c2.example.avc.encoder          (hw) [vendor]\n"
            "    --video-codec=h264 --video-encoder=OMX.example.video.encoder.avc   (hw) [vendor] "
            "(alias for c2.example.avc.encoder)\n"
            "    --video-codec=h264 --video-encoder=c2.android.avc.encoder          (sw)\n"
            "    --video-codec=h265 --video-encoder=c2.example.hevc.encoder         (hw) [vendor]\n"
            "[server] INFO: List of audio encoders:\n"
            "    --audio-codec=opus --audio-encoder=c2.android.opus.encoder         (sw)\n"
        )
        self.rotations: list[tuple] = []
        self.auto_rotate, self.locked_rotation = True, 0
        #: ``method name -> call``, made when that method is entered (before it does anything): a stop that comes at
        #: that moment, or a wait for the test's say-so.
        self.hooks: dict[str, Callable[[], None]] = {}

    def _enter(self, name: str) -> None:
        hook = self.hooks.get(name)
        if hook is not None:
            hook()

    @property
    def server(self) -> Any:
        """The server of the latest ``forward``."""
        return self.servers[-1]

    def names(self) -> list[str]:
        """The name of every call so far, in order."""
        return [c[0] for c in self.calls]

    def push(self, serial: str, local: Path, remote: str) -> None:
        """Copy a file to the "device"."""
        self._enter("push")
        data = Path(local).read_bytes()
        self.calls.append(("push", serial, remote, data[:4]))
        self.device_files[remote] = hashlib.sha256(data).hexdigest()

    def device_sha256(self, serial: str, remote: str) -> str | None:
        """The hash of a file on the "device"."""
        self.calls.append(("sha256", remote))
        return self.device_files.get(remote)

    def move(self, serial: str, source: str, target: str) -> None:
        """Move a file on the "device"."""
        self.calls.append(("move", source, target))
        self.device_files[target] = self.device_files.pop(source)

    def remove(self, serial: str, remote: str, *, timeout: float = 10) -> None:
        """Delete a file on the "device"."""
        self.calls.append(("remove", remote))
        self.device_files.pop(remote, None)

    def kill_server_process(self, serial: str, marker: str, *, timeout: float = 10) -> None:
        """Kill the server by its marker."""
        self.calls.append(("kill_server_process", marker))

    def server_alive(self, serial: str, marker: str) -> bool | None:
        """Whether the server is running (what :attr:`server_is_alive` says)."""
        self.calls.append(("server_alive", marker))
        return self.server_is_alive

    def drop_stale_forwards(self, serial: str, abstract_name: str) -> int:
        """Remove old forwards (there are none)."""
        self._enter("drop_stale_forwards")
        self.calls.append(("drop_stale", abstract_name))
        return 0

    def forward(self, serial: str, abstract_name: str) -> int:
        """A new server, and the port it listens on."""
        self._enter("forward")
        self.calls.append(("forward", abstract_name))
        self.servers.append(self.make_server())
        return self.server.port

    def forward_remove(self, serial: str, port: int, *, timeout: float = 10) -> None:
        """Remove a forward."""
        self.calls.append(("forward_remove", port))

    def spawn(self, serial: str, command: str) -> FakeProcess:
        """Start the server's process (the server that was forwarded to is told which codec it is started for)."""
        self._enter("spawn")
        self.calls.append(("spawn", command))
        self.process = FakeProcess()
        server = self.servers[-1] if self.servers else None
        if isinstance(server, FakeScrcpyServer):
            asked = re.search(r"(?:^| )video_codec=(\S+)", command)
            server.asked = asked.group(1) if asked else "h264"
        return self.process

    def shell(self, serial: str, command: str, *, timeout: float | None = None) -> str:
        """Run a shell command: the encoder list, ``wm size`` and ``getprop`` are answered."""
        if "list_encoders=true" in command:
            self.calls.append(("list_encoders", command))
            return self.encoder_listing
        if command.startswith("wm size"):
            self.calls.append(("wm_size", command))
            return self.wm_size
        return "Fake Model\n15\n35\n"

    def keyevent(self, serial: str, code: int, *, long: bool = False) -> None:
        """A key press without the server."""
        self.keyevents.append((code, long))

    def rotation_state(self, serial: str) -> tuple[bool, int]:
        """Whether the sensor decides, and the locked rotation."""
        return self.auto_rotate, self.locked_rotation

    def lock_rotation(self, serial: str, rotation: int, display_id: int = 0) -> None:
        """Lock the rotation."""
        self.rotations.append(("lock", rotation, display_id))
        self.auto_rotate, self.locked_rotation = False, rotation

    def free_rotation(self, serial: str, display_id: int = 0) -> None:
        """Give the rotation back to the sensor."""
        self.rotations.append(("free", None, display_id))
        self.auto_rotate = True

    def close(self) -> None:
        """Close every server that was made."""
        for server in self.servers:
            server.close()


class FakeDevice:
    """The parts of an ``AdbDevice`` that its HMI uses: how it is named, its adb and its serial now."""

    def __init__(self, serial: str = "FAKE123") -> None:
        """A device that is present."""
        self.transport = "usb"
        self.usb_port: str | None = "usb:1-4.2"
        self.serial: str | None = None
        self.address: str | None = None
        self.adb_path = "adb"
        self.server_port = 15037
        self.connect_timeout = 30.0
        self.present_serial = serial
        #: Set, ``current_serial`` raises it (the device is not there, or not ready).
        self.error: str | None = None
        self.adb_env_calls = 0

    def current_serial(self) -> str:
        """The serial now; raises when :attr:`error` is set."""
        if self.error:
            raise RuntimeError(self.error)
        return self.present_serial

    def adb_env(self) -> dict[str, str]:
        """The environment of the server; counts that a reference to it was taken."""
        self.adb_env_calls += 1
        return {"ANDROID_ADB_SERVER_PORT": str(self.server_port)}


def build_hmi(
    tmp_path: Path, *, adb_server: Callable[[], Any] = FakeScrcpyServer, **config: Any
) -> tuple[AdbDeviceHmi, FakeAdb]:
    """An :class:`~jumpstarter_driver_adb.hmi.driver.AdbDeviceHmi` on a :class:`FakeDevice`, whose adb is a
    :class:`FakeAdb` (its servers are made by ``adb_server``) and whose scrcpy-server is a small file in ``tmp_path``.

    ``config`` is the HMI's options (``idle_stop_s`` is 0 unless given); the driver's ``device`` and the fake adb are
    attributes of the result (``driver.device``, ``driver._adb``).
    """
    jar = tmp_path / "scrcpy-server"
    jar.write_bytes(b"PK\x03\x04 not really a server")
    config.setdefault("idle_stop_s", 0)
    config.setdefault("server_path", str(jar))
    config.setdefault("server_sha256", hashlib.sha256(jar.read_bytes()).hexdigest())
    driver = AdbDeviceHmi(device=FakeDevice(), **config)  # ty: ignore[invalid-argument-type]
    fake = FakeAdb(adb_server)
    driver._adb = fake  # ty: ignore[invalid-assignment]
    return driver, fake


def invoke(group: Any, args: list[str]) -> Any:
    """Run a click command on a ``CliRunner`` with logging off while it runs.

    A package's live logging (``log_cli``) suspends and resumes pytest's output capture around every record, and
    resuming it replaces the ``sys.stdout`` the runner swapped in: a record logged by the driver while a command runs
    would send the command's output to pytest instead of to the runner.
    """
    from click.testing import CliRunner

    logging.disable(logging.INFO)
    try:
        return CliRunner().invoke(group, args)
    finally:
        logging.disable(logging.NOTSET)
