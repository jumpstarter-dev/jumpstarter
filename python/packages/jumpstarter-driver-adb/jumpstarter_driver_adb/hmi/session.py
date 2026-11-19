"""One running ``scrcpy-server`` on one device: its process, its two sockets, and the threads that read them.

A :class:`ScrcpySession` is what ``start`` makes and ``stop`` ends: push the pinned server, forward its abstract socket
to a free exporter port, start it under ``adb shell`` (so that it dies with the adb connection), connect the video and
the control socket, and then read the video into a
:class:`~jumpstarter_driver_hmi.source.HubSink` until the server goes away. Nothing in the session decodes or
re-encodes a picture: the device's own hardware encoder made it, and the exporter's hub gives viewers what came (with
the parameter sets put in front of every key picture, which scrcpy sends only once). The codec is H.264 or HEVC, as the
settings say: the server is asked for it, and the stream must start with its codec id.

**Who writes the control socket.** Only the session's :class:`~jumpstarter_driver_hmi.proc.writer.CommandWriter`
thread does. Everything else (the input sink, the clipboard, the app start) enqueues and returns, so a device that stops
reading its control socket stalls nothing but that thread: not the reader of the video, not ``stop``. A failed write
ends the session.
"""

from __future__ import annotations

import collections
import contextlib
import logging
import os
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from jumpstarter_driver_hmi.common import CodecLine
from jumpstarter_driver_hmi.hub.codecs import codec_string_of, has_sps
from jumpstarter_driver_hmi.proc.writer import CommandWriter
from jumpstarter_driver_hmi.source import HubSink

from . import protocol
from .adb import Adb, AdbError
from .server_binary import device_path

log = logging.getLogger(__name__)

#: Option names and values go through the device's shell, so they are limited to what needs no quoting.
_SAFE = re.compile(r"^[A-Za-z0-9_.:=,\-]+$")
_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
#: What the server logs (at debug level) when it has chosen its encoder.
_ENCODER = re.compile(r"Using video encoder: '([^']+)'")
#: How long a stop lets the control writer finish what is queued before it gives up on it.
WRITER_GRACE_S = 0.25
#: How long a process, and each thread, is given to end before it is killed or left behind.
PROCESS_WAIT_S = 3.0
#: How long the start waits for the server at a time (a stop is noticed within one of these).
HANDSHAKE_SLICE_S = 0.25
#: How long a failed start gives itself to undo what it made.
STOP_DEADLINE_S = 6.0

#: Server options the session sets itself (they are what makes the stream this driver's stream), not open to
#: ``server_options``.
_OWNED = frozenset(
    {
        "scid", "log_level", "video", "audio", "control", "video_codec", "tunnel_forward", "cleanup", "max_size",
        "video_bit_rate", "max_fps", "display_id", "clipboard_autosync", "send_device_meta", "send_frame_meta",
        "send_dummy_byte", "send_stream_meta", "raw_stream", "video_source", "audio_source", "camera_id",
        "list_encoders", "list_displays", "list_cameras", "list_camera_sizes", "list_apps", "video_codec_options",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Settings:
    """What the stream is: the picture size cap, the bit rate, the frame rate, and the options of the server."""

    #: Longest side of the picture in pixels (0: the device's own size). The server scales; touches are mapped back.
    max_size: int = 1280
    #: Video bit rate in bit/s.
    bit_rate: int = 4_000_000
    #: Frame rate cap. The encoder emits a picture only when the display changes (and repeats the last one after
    #: 100 ms).
    max_fps: int = 30
    #: Seconds between key pictures. Short, because a viewer that lost a picture has to wait for the next one.
    idr_interval_s: int = 2
    #: Which display to mirror (a device may have several; 0 is the default one).
    display_id: int = 0
    #: A ``MediaCodec`` encoder by name (``c2.android.avc.encoder``), for a device whose default one is poor. It must
    #: be one that makes the stream's ``codec``.
    video_encoder: str | None = None
    #: Whether the control channel exists (false: a picture and nothing else).
    control: bool = True
    #: Wake the device when the server starts (scrcpy's own default).
    power_on: bool = True
    #: Keep the display on while plugged in; restored when the server ends.
    stay_awake: bool = False
    #: Draw the touch points on the device's display.
    show_touches: bool = False
    #: Ask the encoder for this many temporal layers (0 or 1: none). With 2 the enhancement layer's pictures are
    #: non-reference ones, which a congested viewer can drop without breaking the picture. Whether the device's encoder
    #: honors it is its own business (the hub reads ``nal_ref_idc`` and only drops what really is non-reference).
    temporal_layers: int = 0
    #: Other ``key=value`` options of the server (checked against :data:`_SAFE`).
    server_options: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    #: The stream's codec, by the interface's names: ``h264`` or ``hevc`` (the server calls the second ``h265``). The
    #: HMI chooses it at every start; it is not one of ``server_options``.
    codec: str = "h264"

    def validate(self) -> None:
        """Raise ``ValueError`` for settings the server would refuse or the shell would misread."""
        if not 0 <= self.max_size <= 8192:
            raise ValueError("max_size must be 0 (the device's size) or up to 8192")
        if not 100_000 <= self.bit_rate <= 100_000_000:
            raise ValueError("bit_rate must be between 100 kbit/s and 100 Mbit/s")
        if not 1 <= self.max_fps <= 120:
            raise ValueError("max_fps must be 1 to 120")
        if not 1 <= self.idr_interval_s <= 30:
            raise ValueError("idr_interval_s must be 1 to 30")
        if self.temporal_layers not in (0, 1, 2, 3, 4):
            raise ValueError("temporal_layers must be 0 to 4")
        if self.codec not in protocol.SERVER_CODECS:
            raise ValueError(f"codec must be one of {', '.join(protocol.SERVER_CODECS)}: {self.codec!r}")
        if not 0 <= self.display_id <= 1_000_000:
            raise ValueError("display_id must be a non-negative display id")
        if self.video_encoder is not None and not _SAFE.fullmatch(self.video_encoder):
            raise ValueError("video_encoder has characters that are not allowed")
        self._validate_server_options()

    def _validate_server_options(self) -> None:
        for key, value in self.server_options:
            if not _KEY.fullmatch(key) or key in _OWNED:
                raise ValueError(f"server option {key!r} is not allowed")
            if not _SAFE.fullmatch(value):
                raise ValueError(f"server option {key!r} has a value with characters that are not allowed")

    @property
    def needs_cleanup(self) -> bool:
        """Whether the server has to run its cleanup helper when it ends: only where it changed a device setting to
        restore.

        Without it nothing of ours runs on the device but the server itself, and the server file it was started from
        stays on the device (the cache of the next start); with it, the server deletes its own file when it ends.
        """
        changing = {"hmi_off_timeout", "power_off_on_close", "display_ime_policy", "keep_active"}
        return self.stay_awake or self.show_touches or any(key in changing for key, _ in self.server_options)

    def arguments(self, scid: int) -> list[str]:
        """The ``key=value`` arguments after the version, in the form the server takes."""
        self.validate()
        codec_options = f"i-frame-interval:int={self.idr_interval_s}"
        if self.temporal_layers > 1:
            codec_options += f",ts-schema:string=android.generic.{self.temporal_layers}"
        args = [
            f"scid={scid:08x}",
            "log_level=debug",  # the server then says which encoder it chose ("Using video encoder"), and that is kept
            "video=true",
            "audio=false",
            f"control={'true' if self.control else 'false'}",
            f"video_codec={protocol.SERVER_CODECS[self.codec]}",
            "tunnel_forward=true",
            f"cleanup={'true' if self.needs_cleanup else 'false'}",
            "clipboard_autosync=false",
            f"max_size={self.max_size}",
            f"video_bit_rate={self.bit_rate}",
            f"max_fps={self.max_fps}",
            f"display_id={self.display_id}",
            f"power_on={'true' if self.power_on else 'false'}",
            f"stay_awake={'true' if self.stay_awake else 'false'}",
            f"show_touches={'true' if self.show_touches else 'false'}",
            f"video_codec_options={codec_options}",
        ]
        if self.video_encoder:
            args.append(f"video_encoder={self.video_encoder}")
        args.extend(f"{key}={value}" for key, value in self.server_options)
        return args


#: One line of the server's ``list_encoders`` output, for example
#: ``--video-codec=h264 --video-encoder=c2.example.avc.encoder  (hw) [vendor] (alias for ...)``.
_ENCODER_LINE = re.compile(
    r"--video-codec=(?P<codec>\w+)\s+--video-encoder=(?P<name>\S+)\s+\((?P<kind>hw|sw)\)(?P<rest>.*)$"
)


def ensure_device_file(adb: Adb, serial: str, data: bytes, sha256: str, remote: str) -> bool:
    """Have ``data`` at ``remote`` on the device, pushing only if the device's own ``sha256sum`` of what is there is not
    ``sha256``.

    Pushed to a name of its own first and moved into place, so that a start that looks at the file at the same moment
    sees it whole or not at all. Returns whether anything was pushed.
    """
    if adb.device_sha256(serial, remote) == sha256:
        return False
    handle, temporary = tempfile.mkstemp(prefix="scrcpy-server.", suffix=".jar")  # 0600, in a directory of ours
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        partial = f"{remote}.{secrets.token_hex(3)}"
        adb.push(serial, Path(temporary), partial)
        adb.move(serial, partial, remote)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return True


def list_encoders(adb: Adb, serial: str, data: bytes, sha256: str) -> list[dict]:
    """The video encoders the device has, as the server itself lists them (``list_encoders=true``): ``{"codec", "name",
    "hardware", "alias_of"}`` in the server's own order.

    Uses (and leaves) the cached server file; starts nothing that outlives the call.
    """
    remote = device_path(sha256)
    ensure_device_file(adb, serial, data, sha256, remote)
    command = (
        f"CLASSPATH={remote} app_process / com.genymobile.scrcpy.Server {protocol.SERVER_VERSION} "
        "list_encoders=true cleanup=false log_level=info"
    )
    out = adb.shell(serial, command, timeout=30)
    encoders = []
    for line in out.splitlines():
        match = _ENCODER_LINE.search(line.strip())
        if match:
            alias = re.search(r"alias for (\S+?)\)", match["rest"])
            encoders.append(
                {
                    "codec": match["codec"],
                    "name": match["name"],
                    "hardware": match["kind"] == "hw",
                    "alias_of": alias.group(1) if alias else None,
                }
            )
    return encoders


class _TimestampMap:
    """The encoder's PTS as the stream's timestamp: microseconds from the first picture, never going back (a restart may
    reset the encoder's clock)."""

    def __init__(self) -> None:
        self._origin: int | None = None
        self._offset = 0
        self._last_pts = 0
        self._last_ts = -1

    def __call__(self, pts_us: int) -> int:
        if self._origin is None:
            self._origin = pts_us
        elif pts_us < self._last_pts:  # a new capture session restarted the clock
            self._origin = pts_us
            self._offset = self._last_ts + 1000
        self._last_pts = pts_us
        ts = pts_us - self._origin + self._offset
        self._last_ts = max(ts, self._last_ts + 1)
        return self._last_ts


class ScrcpySession:
    """The server on the device and our two connections to it. Build, :meth:`start`, then :meth:`stop` (idempotent).

    One instance is one run: a restart is a new instance.
    """

    #: How long (seconds) a clipboard read or write waits for the server's answer.
    CLIPBOARD_TIMEOUT_S = 3.0

    def __init__(
        self,
        *,
        adb: Adb,
        serial: str,
        server_bytes: bytes,
        server_sha256: str,
        settings: Settings,
        sink: HubSink,
        scid: int,
    ) -> None:
        """Nothing is touched until :meth:`start`."""
        self.adb = adb
        self.serial = serial
        self.server_bytes = server_bytes
        self.server_sha256 = server_sha256
        self.settings = settings
        self.sink = sink
        self.scid = scid
        self.device_name = ""
        self.size: tuple[int, int] | None = None
        self.codec: str | None = None
        self.started_at: float | None = None
        self.ended = threading.Event()
        self.error: str | None = None
        #: Where the control messages go (``None``: no control channel). The only thing that writes the socket.
        self.writer: CommandWriter | None = None
        # Without the cleanup helper (the usual case) the server file stays on the device between leases, under a name
        # that is its hash's, and is pushed only when the device does not have it: a cache, checked by sha256 on the
        # device. With the helper the server deletes its own file when it ends, so each session has a file of its own
        # (another child of the same device, or the start that follows a stop that has not finished, must not have its
        # file deleted under it).
        self.cached = not settings.needs_cleanup
        self._remote = device_path(server_sha256, "" if self.cached else secrets.token_hex(4))
        self.pushed = False
        self.encoder: str | None = None
        self.last_packet_at = time.monotonic()
        self._abstract = f"scrcpy_{scid:08x}"
        self._port: int | None = None
        self._process = None
        self._video: socket.socket | None = None
        self._control: socket.socket | None = None
        #: Every connection made, so that a stop that comes while one is still being made can end it.
        self._sockets: list[socket.socket] = []
        # what the server says back on the control socket: the clipboard it was asked for, and the acknowledgements of
        # the ones it was given
        self._device_cv = threading.Condition()
        self._clipboard: str | None = None
        self._clipboards = 0
        self._acked: set[int] = set()
        self._sequence = 0
        self._threads: list[threading.Thread] = []
        self._log_tail: collections.deque[str] = collections.deque(maxlen=30)
        self._config: bytes = b""
        self._ready = threading.Event()
        self._stop_lock = threading.Lock()
        self._started = False
        self._stopping = False
        self._stopped = threading.Event()
        self._timestamps = _TimestampMap()

    # ---- life -----------------------------------------------------------------------------------------

    def start(self, timeout: float) -> None:
        """Bring the server up and wait for its first picture description.

        Raises:
            AdbError: If the device, adb or the server fails (the message carries the server's own last lines).
        """
        deadline = time.monotonic() + timeout
        try:
            self._not_stopped()
            self._started = True  # from here on there may be something on the device to undo
            self._push_server()
            self._not_stopped()
            self.adb.drop_stale_forwards(self.serial, self._abstract)
            self._not_stopped()
            self._port = self.adb.forward(self.serial, self._abstract)
            self._not_stopped()
            command = (
                f"CLASSPATH={self._remote} app_process / com.genymobile.scrcpy.Server {protocol.SERVER_VERSION} "
                + " ".join(self.settings.arguments(self.scid))
            )
            self._process = self.adb.spawn(self.serial, command)
            self._not_stopped()
            self._spawn(self._pump_log, "log")
            # The server accepts every socket (video, then control) before it says anything on any of them.
            self._video = self._connect_video(deadline)
            if self.settings.control:
                self._control = self._connect(deadline)
            self.device_name = protocol.parse_device_name(
                self._recv_exactly(self._video, protocol.DEVICE_NAME_LENGTH, deadline)
            )
            self._check_codec(protocol.parse_codec(self._recv_exactly(self._video, 4, deadline)))
            # Both are read by a thread that waits as long as the server is quiet (a still display sends nothing on the
            # control connection): no timeout, `stop` shuts them down to end the wait.
            self._video.settimeout(None)
            if self._control is not None:
                self._control.settimeout(None)
                self.writer = CommandWriter(
                    self._control.sendall, name=f"scrcpy-control-{self.scid:08x}", on_failure=self._writer_failed
                )
            self._spawn(self._read_video, "video")
            if self._control is not None:
                self._spawn(self._read_control, "control")
            if not self._ready.wait(max(0.1, deadline - time.monotonic())):
                raise AdbError("the scrcpy server gave no picture description in time" + self._tail())
            if self._stopping:
                raise AdbError("the scrcpy session was stopped before it was up")
            if self.error:
                raise AdbError(self.error)
            self.started_at = time.monotonic()
        except BaseException:
            if not self._stop(STOP_DEADLINE_S) and self._started:
                # Another thread's stop passed (or is passing) while this start was still making things: a forward, a
                # process, a pushed file that stop did not know of. They are this start's to undo.
                self._sweep(time.monotonic() + STOP_DEADLINE_S)
            raise

    def _check_codec(self, codec_id: int) -> None:
        """The stream must be the codec that was asked for (a server that made another is not the one this module speaks
        to)."""
        asked = self.settings.codec
        if codec_id != protocol.CODEC_IDS[asked]:
            sent = protocol.codec_of(codec_id)
            raise AdbError(
                f"the device's encoder sent codec id {codec_id:#x}{f' ({sent})' if sent else ''}, "
                f"not the {asked} that was asked for"
            )

    def _not_stopped(self) -> None:
        """Give up the start when a stop has come in the meantime (nothing more is made)."""
        if self._stopping:
            raise AdbError("the scrcpy session was stopped before it was up")

    def _push_server(self) -> None:
        """Have the verified bytes on the device (not the file they came from, which may have changed since: a private
        copy is pushed).

        A cached file is used as it is when the device's own ``sha256sum`` of it is the pinned hash, and replaced
        otherwise.
        """
        if self.cached:
            self.pushed = ensure_device_file(self.adb, self.serial, self.server_bytes, self.server_sha256, self._remote)
            return
        handle, temporary = tempfile.mkstemp(prefix="scrcpy-server.", suffix=".jar")  # 0600, in a directory of ours
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(self.server_bytes)
            self.adb.push(self.serial, Path(temporary), self._remote)
            self.pushed = True
        finally:
            Path(temporary).unlink(missing_ok=True)

    def stop(self, deadline_s: float = 6.0) -> None:
        """End everything: the control writer, the sockets, the server, the forward and the file on the device. Safe to
        repeat, from any thread, and never longer than ``deadline_s`` seconds (what is not finished by then, such as a
        thread stuck in a socket call, is left to end by itself: its socket is closed).

        A caller that comes while another is stopping waits for it to finish (a start that follows must find the device
        clean), for no longer than its own deadline.
        """
        self._stop(deadline_s)

    def _stop(self, deadline_s: float) -> bool:
        """:meth:`stop`; whether this call did the teardown (``False``: another call did, or is doing it, and this one
        waited for it)."""
        deadline = time.monotonic() + deadline_s
        with self._stop_lock:
            waiting = self._stopping
            self._stopping = True
        if waiting:
            if threading.current_thread() not in self._threads:
                self._stopped.wait(max(0.0, deadline - time.monotonic()))
            return False
        try:
            self._teardown(deadline)
        finally:
            self.ended.set()
            self._ready.set()  # whoever is waiting for the first picture finds out that there will not be one
            self._stopped.set()
            with self._device_cv:
                self._device_cv.notify_all()
        return True

    def _teardown(self, deadline: float) -> None:
        self._release(deadline)
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=max(0.0, min(PROCESS_WAIT_S, deadline - time.monotonic())))

    def _sweep(self, deadline: float) -> None:
        """Undo again what exists now: what a start made after a stop had passed. Repeating is harmless."""
        self._release(deadline)

    def _release(self, deadline: float) -> None:
        """The writer, the sockets, the process and what is on the device (each of them may be there or not)."""
        writer = self.writer
        if writer is not None:
            # what is queued (a finger's lift, a key's release) is written if the device reads; a writer stuck in a
            # write is given up on, and the shutdown of the socket below ends its write
            writer.close(min(WRITER_GRACE_S, max(0.0, deadline - time.monotonic())))
        with self._stop_lock:
            links = list(self._sockets)
        for link in links:
            with contextlib.suppress(OSError):
                link.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(OSError):
                link.close()
        self._end_process(deadline)
        self._clean_device(deadline)

    def _end_process(self, deadline: float) -> None:
        process = self._process
        if process is None:
            return
        with contextlib.suppress(Exception):
            process.terminate()
        try:
            process.wait(timeout=max(0.0, min(PROCESS_WAIT_S, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                process.kill()

    def _clean_device(self, deadline: float) -> None:
        """Remove the forward, whatever the shell's exit left running, and (without the server's own cleanup) the file.

        Best effort, bounded by ``deadline``, and never starting an ADB server: if the parent already took its server
        down, a command here would start one that nobody owns (these commands use an environment that never does).
        """

        def budget() -> float:
            return min(10.0, deadline - time.monotonic())

        marker = f"scid={self.scid:08x}"
        steps = [
            (self._port is not None, lambda t: self.adb.forward_remove(self.serial, cast(int, self._port), timeout=t)),
            (True, lambda t: self.adb.kill_server_process(self.serial, marker, timeout=t)),
            (not self.cached, lambda t: self.adb.remove(self.serial, self._remote, timeout=t)),
        ]
        for wanted, step in steps:
            if wanted and budget() > 0:
                with contextlib.suppress(Exception):
                    step(budget())

    @property
    def alive(self) -> bool:
        """Whether the server is up and being read."""
        return self._ready.is_set() and not self.ended.is_set() and not self._stopping

    # ---- connections ------------------------------------------------------------------------------------

    def _connect(self, deadline: float) -> socket.socket:
        assert self._port is not None
        sock = socket.create_connection(
            ("127.0.0.1", self._port), timeout=max(0.5, min(5.0, deadline - time.monotonic()))
        )
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._stop_lock:
            stopping = self._stopping
            if not stopping:
                self._sockets.append(sock)
        if stopping:  # a stop came while the connection was being made
            sock.close()
            raise AdbError("the scrcpy session was stopped before it was up")
        return sock

    def _connect_video(self, deadline: float) -> socket.socket:
        """Connect to the forward and wait for the server's dummy byte: the forward answers before anything listens
        behind it."""
        last: Exception | None = None
        while time.monotonic() < deadline:
            if self._stopping:
                raise AdbError("the scrcpy session was stopped before it was up")
            if self._process is not None and self._process.poll() is not None:
                raise AdbError(
                    f"scrcpy-server exited with {self._process.returncode} before it connected" + self._tail()
                )
            sock = None
            try:
                sock = self._connect(deadline)
                # wait as long as the deadline allows for the byte: a forward with nothing behind it answers with a
                # close at once, and a server that has accepted this connection must not be given up on (the next
                # connection would be taken as its control)
                if self._recv_some(sock, 1, deadline):
                    return sock
                sock.close()
            except OSError as e:
                last = e
                if sock is not None:
                    sock.close()
            time.sleep(0.1)
        raise AdbError(f"scrcpy-server did not accept a connection ({last})" + self._tail())

    def _recv_some(self, sock: socket.socket, count: int, deadline: float) -> bytes:
        """Up to ``count`` bytes (``b""``: the peer closed), waiting for them until ``deadline``.

        The wait is made of short ones, so that a stop is noticed within one of them: a ``shutdown`` does not wake a
        read that has a timeout on every platform.

        Raises:
            TimeoutError: If nothing came by the deadline.
            AdbError: If the session was stopped while it waited.
        """
        sock.settimeout(HANDSHAKE_SLICE_S)
        while True:
            self._not_stopped()
            try:
                return sock.recv(count)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise

    def _recv_exactly(self, sock: socket.socket, count: int, deadline: float) -> bytes:
        """Exactly ``count`` bytes, within the deadline (the same waiting as :meth:`_recv_some`)."""
        data = b""
        while len(data) < count:
            try:
                chunk = self._recv_some(sock, count - len(data), deadline)
            except TimeoutError:
                raise AdbError("the scrcpy server did not answer in time" + self._tail()) from None
            if not chunk:
                raise EOFError("the scrcpy server closed the video socket")
            data += chunk
        return data

    # ---- threads ------------------------------------------------------------------------------------------

    def _spawn(self, target, name: str) -> None:
        thread = threading.Thread(target=target, name=f"scrcpy-{name}-{self.scid:08x}", daemon=True)
        self._threads.append(thread)
        thread.start()

    def _tail(self) -> str:
        lines = list(self._log_tail)[-8:]
        return " | server said: " + " / ".join(lines) if lines else ""

    def _pump_log(self) -> None:
        """The server's output (on the adb shell's pipe) into our log, and a tail for error messages."""
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            text = line.strip()
            if text:
                self._log_tail.append(text)
                log.debug("scrcpy-server[%s]: %s", self.serial, text)
                match = _ENCODER.search(text)
                if match:
                    self.encoder = match.group(1)
        if not self._stopping:
            self._end(f"the scrcpy server process ended ({process.poll()})" + self._tail())

    def _read_video(self) -> None:
        sock = self._video
        assert sock is not None
        try:
            while True:
                packet = protocol.read_packet(sock)
                self.last_packet_at = time.monotonic()
                if isinstance(packet, protocol.Session):
                    self.size = (packet.width, packet.height)
                elif isinstance(packet, protocol.Config):
                    self._config = packet.data
                    self._publish_line()
                else:
                    self._publish_media(packet)
        except Exception as e:  # noqa: BLE001 (whatever ends the reader ends the session: viewers must not wait on it)
            if not self._stopping:
                self._end(f"the video stream ended: {type(e).__name__}: {e}" + self._tail())

    def _publish_line(self) -> None:
        codec = codec_string_of(self._config, self.settings.codec)  # the config packet's SPS names the codec in use
        if codec is None or self.size is None:
            return
        self.codec = codec
        self.sink.config(
            CodecLine(
                self.codec,
                self.size[0],
                self.size[1],
                self.settings.max_fps,
                input="stream" if self.settings.control else None,
            )
        )
        self._ready.set()

    def _publish_media(self, packet: protocol.Media) -> None:
        data = packet.data
        if packet.key and self._config and not has_sps(data, self.settings.codec):
            data = self._config + data  # scrcpy sends the parameter sets once; a viewer that joins later needs them
        if not self._ready.is_set():
            return  # a picture before its description cannot be played
        self.sink.media(self._timestamps(packet.pts_us), data)

    def _read_control(self) -> None:
        """Read the server's device messages (the clipboard it was asked for, and its acknowledgements); the end of this
        stream means the server is gone."""
        sock = self._control
        assert sock is not None
        messages = protocol.DeviceMessages()
        try:
            while data := sock.recv(4096):
                for message in messages.feed(data):
                    with self._device_cv:
                        if isinstance(message, protocol.DeviceClipboard):
                            self._clipboard = message.text
                            self._clipboards += 1
                        else:
                            self._acked.add(message.sequence)
                        self._device_cv.notify_all()
        except (OSError, protocol.ProtocolError) as e:
            if not self._stopping:
                log.warning("scrcpy: the control socket: %s", e)
        if not self._stopping:
            self._end("the control connection ended" + self._tail())

    def fail(self, reason: str) -> None:
        """End the session because something outside it found it broken."""
        self._end(reason)

    def _writer_failed(self, error: BaseException) -> None:
        """A write to the control socket failed (on the writer's thread): the server is gone or does not read."""
        if not self._stopping:
            self._end(f"writing to the scrcpy server failed: {error}" + self._tail())

    def _end(self, reason: str) -> None:
        """The server is gone on its own: say why, and let everything waiting find out."""
        if self.error is None:
            self.error = reason
        self.ended.set()
        self._ready.set()
        with self._device_cv:
            self._device_cv.notify_all()
        with contextlib.suppress(Exception):
            self.sink.ended(self.error)
        threading.Thread(target=self.stop, name=f"scrcpy-end-{self.scid:08x}", daemon=True).start()

    # ---- control --------------------------------------------------------------------------------------------

    def send(self, message: bytes, *, coalesce: str | None = None) -> bool:
        """Queue one control message for the writer (never blocks, does no I/O).

        Returns:
            ``True`` if it is queued (or replaced a queued one with the same ``coalesce`` key), ``False`` if it was
            dropped for a full queue (only a message with a key is ever dropped).

        Raises:
            AdbError: If there is no control connection (``control: false``, or the server is gone).
            InputError: If the queue is full (a message with no key is refused) or the writer has stopped.
        """
        writer = self.writer
        if writer is None or self.ended.is_set():
            raise AdbError("the scrcpy control connection is not open")
        return writer.send(message, coalesce=coalesce)

    def get_clipboard(self, timeout: float | None = None) -> str:
        """The device's clipboard text; empty when there is none (the server says nothing then, so the wait ends at
        ``timeout``)."""
        timeout = self.CLIPBOARD_TIMEOUT_S if timeout is None else timeout
        with self._device_cv:
            before = self._clipboards
            self.send(protocol.get_clipboard())
            self._device_cv.wait_for(lambda: self._clipboards != before or self.ended.is_set(), timeout)
            return self._clipboard if self._clipboards != before and self._clipboard is not None else ""

    def set_clipboard(self, text: str, paste: bool = False, timeout: float | None = None) -> bool:
        """Put ``text`` on the device's clipboard (and paste it into the focused field if ``paste``); whether the server
        said it was done in time."""
        timeout = self.CLIPBOARD_TIMEOUT_S if timeout is None else timeout
        with self._device_cv:
            self._sequence += 1
            sequence = self._sequence
            self.send(protocol.set_clipboard(sequence, text, paste))
            done = self._device_cv.wait_for(lambda: sequence in self._acked or self.ended.is_set(), timeout)
            self._acked.discard(sequence)
            return done and not self.ended.is_set()

    def start_app(self, name: str) -> None:
        """Start an app by its package name (``+name``: force-stop it first; ``?name``: search the installed apps by
        name)."""
        self.send(protocol.start_app(name))
