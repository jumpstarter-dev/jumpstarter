"""The scrcpy source of the HMI interface: an Android device's picture and input, from scrcpy's server.

:class:`ScrcpySource` is the device-specific half of :class:`~jumpstarter_driver_adb.hmi.driver.AdbDeviceHmi`
(everything about viewers, queues, framing, recording and quality adaptation is the HMI package's). It pushes the
pinned ``scrcpy-server`` (see :mod:`jumpstarter_driver_adb.hmi.server_binary`), starts it as the ``shell`` user (no
root, no APK, no accessibility service), reads the H.264 or HEVC stream the device's own encoder makes into the hub,
and takes touch, keyboard, keys and text through scrcpy's control socket. It declares the subset of the HMI interface
that scrcpy genuinely has: the codecs H.264 and HEVC (there is no MJPEG), one finger, the keyboard keys that have an
Android key code, the named hard keys and text.

A key picture is not cheap here: the one lever scrcpy has (``RESET_VIDEO``) restarts the capture for **every**
viewer. So the source declares ``keyframe_cost="restart"``, never asks, and the hub relies on the encoder's periodic
key picture (``idr_interval_s``) and on ``join_wait_s``. A change of quality restarts the capture (``apply_rung`` is
``False``); the viewers stay attached and get the new codec line.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from jumpstarter_driver_hmi.common import (
    DEFAULT_CODEC,
    PRESSES,
    Capabilities,
    HardKey,
    KeyboardCaps,
    PointerCaps,
    QualityCaps,
)
from jumpstarter_driver_hmi.hub.quality import Rung
from jumpstarter_driver_hmi.proc.writer import CommandWriter
from jumpstarter_driver_hmi.source import HmiSource, HubSink, InputSink

from . import protocol, server_binary
from .adb import Adb, AdbError, parse_wm_size
from .input import KEY_CODES, KEYBOARD_CODES, ScrcpyInputSink
from .session import ScrcpySession, Settings, list_encoders
from jumpstarter.common.exceptions import ConfigurationError

log = logging.getLogger(__name__)

#: The codecs scrcpy's server makes that the interface has, the default first (it also makes AV1, which the interface
#: does not have, and no MJPEG).
CODECS = ("h264", "hevc")
#: The pointer scrcpy takes: one finger, in the pixels of the picture. No mouse, pen, buttons or wheel.
POINTER = PointerCaps.touch(1)
#: The keyboard scrcpy takes: the keys that have an Android key code, and text typed as a US layout types it.
KEYBOARD = KeyboardCaps(codes=tuple(KEYBOARD_CODES), text=True, layout="us")
#: The buttons a UI draws unless the exporter says otherwise.
DEFAULT_HARD_KEYS = (
    HardKey("BACK", "Back", PRESSES),  # scrcpy sends a key down and up as it is told, so a key can be held
    HardKey("HOME", "Home", PRESSES),
    HardKey("APP_SWITCH", "Recents", PRESSES),
    HardKey("VOLUME_UP", "Volume up", PRESSES),
    HardKey("VOLUME_DOWN", "Volume down", PRESSES),
)
#: Keys it can send and does not offer unless the exporter lists them in ``hard_keys``: the power and sleep buttons can
#: lock the user out of a device whose only way in is its screen.
OPT_IN_KEYS = frozenset({"POWER", "LOCK", "SLEEP"})
#: How long a session that is ending on its own is given when a new one is to take its place.
REPLACE_DEADLINE_S = 6.0
#: What is wrong with a start that is not already a ``RuntimeError``.
_START_ERRORS = (ConfigurationError, OSError, EOFError, protocol.ProtocolError, ValueError)


class ScrcpySource(HmiSource):
    """An Android device's picture and input through scrcpy's server. See the module documentation."""

    def __init__(
        self,
        *,
        adb: Adb,
        serial_of: Callable[[], str],
        settings: Settings,
        scid: int,
        server_path: str | None = None,
        server_sha256: str = server_binary.PINNED_SHA256,
        encoder_policy: str = "hardware",
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Touch nothing: the device is found, and the server pushed, when the capture starts.

        Args:
            adb: The adb commands, against the parent device's ADB server.
            serial_of: The device's serial now (the parent's ``current_serial``); it raises when the device is not
                ready.
            settings: What the stream is, but for the quality and the codec, which :meth:`start` is given
                (``settings.control`` says whether there is a control channel).
            scid: The id the server's socket is named by: the same on every start for one device, so that a forward an
                earlier run left behind can be found and removed.
            server_path: The scrcpy-server file (default: ``$SCRCPY_SERVER``, then the usual install locations).
            server_sha256: Its SHA-256; the file is refused if it differs.
            encoder_policy: ``hardware`` (name the device's hardware encoder of the codec in use) or ``default`` (the
                server's choice).
            sleep: How the stall probe waits between two questions (a test's does not).
        """
        self._adb = adb
        self._serial_of = serial_of
        self._settings = settings
        self._scid = scid
        self._server_path = server_path
        self._server_sha256 = server_sha256
        self._encoder_policy = encoder_policy
        self._sleep = sleep
        self._lock = threading.RLock()
        self._session: ScrcpySession | None = None
        #: Set by ``stop`` and cleared by ``start``: a stop that comes while a start is under way ends that start.
        self._stop_requested = False
        self._last_error: str | None = None
        self._in_use = settings
        self._device_info: dict[str, str] = {}
        self._encoder_info: dict[str, Any] = {}
        self._encoders: dict[str, list[dict]] = {}  # per device serial, what the server says it has (asked once)
        self._server_file: Path | None = None
        #: Touch, keys and text into the running session (``None``: view-only). One for every run of this source.
        self.input: ScrcpyInputSink | None = ScrcpyInputSink(self._control_writer) if settings.control else None

    @property
    def adb(self) -> Adb:
        """The adb commands this source uses (the extras of the driver use them too)."""
        return self._adb

    @property
    def control(self) -> bool:
        """Whether there is a control channel (false: a picture and nothing else)."""
        return self._settings.control

    def serial(self) -> str:
        """The device's serial now (the parent's: it raises when the device is not ready)."""
        return self._serial_of()

    def _control_writer(self) -> CommandWriter | None:
        session = self._session
        return session.writer if session is not None and session.alive else None

    # -- the source's contract -----------------------------------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        """H.264 and HEVC, one finger, the keyboard keys that have an Android key code, the scrcpy key names, text,
        quality levels and ``auto``; a key picture costs a restart. A view-only source declares no input."""
        control = self._settings.control
        return Capabilities(
            codecs=CODECS,
            pointer=POINTER if control else PointerCaps(),
            keyboard=KEYBOARD if control else KeyboardCaps(),
            keys=tuple(sorted(set(KEY_CODES) - OPT_IN_KEYS)) if control else (),
            optional_keys=tuple(sorted(OPT_IN_KEYS & set(KEY_CODES))) if control else (),
            hard_keys=DEFAULT_HARD_KEYS if control else (),
            quality=QualityCaps(),
            keyframe_cost="restart",
        )

    def start(self, sink: HubSink, rung: Rung | None, timeout_s: float, *, codec: str = DEFAULT_CODEC) -> None:
        """Push the server, start it for ``codec`` and connect; return once its first picture is described.

        Raises:
            RuntimeError: If ``codec`` is not one scrcpy makes (``h264`` or ``hevc``), the server file is missing or
                altered, the device is not ready, or the server does not come up (the message says what to look at).
        """
        if codec not in CODECS:
            raise RuntimeError(f"scrcpy makes {' and '.join(CODECS)}, not {codec}")
        settings = (
            self._settings
            if rung is None
            else replace(self._settings, max_size=rung.max_size, bit_rate=rung.bit_rate, max_fps=rung.max_fps)
        )
        settings = replace(settings, codec=codec)
        self._last_error = None
        with self._lock:
            self._stop_requested = False
        previous = self._take_session()
        if previous is not None:  # a run that is over, or one that was never stopped: the device must be clean first
            previous.stop(REPLACE_DEADLINE_S)
        try:
            data = self._server_bytes()
            self._not_stopped()
            serial = self._serial_of()
            self._not_stopped()
            self._start_session(sink, serial, data, settings, timeout_s)
        except _START_ERRORS as e:
            raise RuntimeError(str(e) or type(e).__name__) from e

    def stop(self, deadline_s: float) -> None:
        """End the capture within ``deadline_s`` (idempotent): the writer, the sockets, the server, the forward."""
        with self._lock:
            self._stop_requested = True
        session = self._take_session()
        if self.input is not None:
            self.input.reset()
        if session is not None:
            with contextlib.suppress(Exception):
                session.stop(deadline_s)
            self._last_error = session.error

    @property
    def alive(self) -> bool:
        """Whether the server is up and being read."""
        session = self._session
        return session is not None and session.alive

    @property
    def error(self) -> str | None:
        """Why the capture ended by itself, once it has."""
        session = self._session
        return session.error if session is not None and session.error else self._last_error

    def input_sink(self) -> InputSink | None:
        """Where input goes, or ``None`` for a view-only source."""
        return self.input

    def request_keyframe(self) -> bool:
        """Never asks: the only way to ask is ``RESET_VIDEO``, which restarts the capture for every viewer. The hub
        waits for the encoder's periodic key picture instead."""
        return False

    def apply_rung(self, rung: Rung) -> bool:
        """A change of quality restarts the capture (``False``): the driver stops it and starts it again with
        ``rung``."""
        return False

    def native_size(self) -> tuple[int, int] | None:
        """The display's own size (``wm size``), or ``None`` when the device does not say."""
        try:
            display = self._settings.display_id
            command = "wm size" if not display else f"wm size -d {display}"
            return parse_wm_size(self._adb.shell(self._serial_of(), command, timeout=10))
        except (RuntimeError, OSError) as e:
            log.debug("scrcpy: could not read the display's size (%s): the bit rate is made for 1920x1080", e)
            return None

    def describe(self) -> dict[str, Any]:
        """The device, the encoder and the server file, for ``status()["device"]``."""
        session = self._session
        settings = self._in_use
        return {
            **self._device_info,
            "name": session.device_name if session is not None else None,
            "encoder": self._encoder_info or None,
            "server": {
                "version": protocol.SERVER_VERSION,
                "sha256": self._server_sha256,
                "path": str(self._server_file) if self._server_file else self._server_path,
                "cached": session.cached if session is not None else None,
                "pushed": session.pushed if session is not None else None,
            },
            "settings": {
                "max_size": settings.max_size,
                "bit_rate": settings.bit_rate,
                "max_fps": settings.max_fps,
                "idr_interval_s": settings.idr_interval_s,
                "display_id": settings.display_id,
                "control": settings.control,
                "temporal_layers": settings.temporal_layers,
                "encoder_policy": self._encoder_policy,
            },
        }

    def on_stall(self, silent_s: float, attempt: int) -> str:
        """No picture for a while. A still display is silent (a phone's encoder sends nothing for a display that does
        not change), so silence alone is nothing to act on: ask the device whether the server is still running. A server
        that is there has nothing to say (``wait``); one that is gone, or a device that does not answer twice in a row,
        ends the stream (the viewers reconnect and the next join starts a new one)."""
        session = self._session
        if session is None or session.ended.is_set():
            return "end"
        marker = f"scid={session.scid:08x}"
        for asked in range(2):
            alive = self._adb.server_alive(session.serial, marker)
            if alive:
                return "wait"
            if alive is False:
                break  # a clear answer: not there
            if asked == 0:
                self._sleep(1.0)  # no answer: once more before believing it
        return "end"

    # -- what the driver's extras use ----------------------------------------------------------------------------------

    def reset_lease(self) -> None:
        """A new lease: what was learned about the last run is forgotten."""
        self._device_info, self._encoder_info, self._last_error = {}, {}, None
        self._in_use = self._settings

    def running_session(self) -> ScrcpySession | None:
        """The session, if it is up and has a control channel."""
        session = self._session
        return session if session is not None and session.alive and session.writer is not None else None

    def list_encoders(self) -> list[dict]:
        """The device's video encoders as scrcpy's server lists them: ``{"codec", "name", "hardware", "alias_of"}``, in
        the server's order. Uses the cached server file (pushed if it is not there) and starts no stream.

        Raises:
            RuntimeError: If the server file is missing or altered, or the device is not ready.
        """
        try:
            data = self._server_bytes()
            serial = self._serial_of()
            self._encoders[serial] = list_encoders(self._adb, serial, data, self._server_sha256)
        except _START_ERRORS as e:
            raise RuntimeError(str(e) or type(e).__name__) from e
        return self._encoders[serial]

    # -- starting ------------------------------------------------------------------------------------------------------

    def _not_stopped(self) -> None:
        """Give up the start when a stop has come in the meantime: nothing more is made."""
        if self._stop_requested:
            raise AdbError("the capture was stopped while it was starting")

    def _take_session(self) -> ScrcpySession | None:
        with self._lock:
            session, self._session = self._session, None
            return session

    def _server_bytes(self) -> bytes:
        """The server file's bytes, read and checked against the pin **now**: what is pushed is what was checked, not a
        path that something could have written to since (the exporter outlives many leases)."""
        path = server_binary.locate(self._server_path)
        data = server_binary.read_verified(path, self._server_sha256)
        self._server_file = path
        return data

    def _pick_encoder(self, serial: str, data: bytes, settings: Settings) -> tuple[str | None, str]:
        """``(encoder to name, where it came from)``: the operator's, the device's hardware one for the codec in use, or
        none (the server's own choice)."""
        if settings.video_encoder:
            return settings.video_encoder, "configured"
        if self._encoder_policy != "hardware":
            return None, "server default"
        try:
            if serial not in self._encoders:
                self._encoders[serial] = list_encoders(self._adb, serial, data, self._server_sha256)
        except (AdbError, OSError) as e:
            log.warning("scrcpy: could not list the device's encoders (%s): the server's default is used", e)
            return None, "server default"
        codec = protocol.SERVER_CODECS[settings.codec]  # the server's name for it: the encoder list says h265
        hardware = [
            e["name"] for e in self._encoders[serial] if e["codec"] == codec and e["hardware"] and not e["alias_of"]
        ]
        return (hardware[0], "hardware") if hardware else (None, "server default")

    def _start_session(self, sink: HubSink, serial: str, data: bytes, settings: Settings, timeout_s: float) -> None:
        name, source = self._pick_encoder(serial, data, settings)
        self._not_stopped()
        try:
            session = self._new_session(sink, serial, data, replace(settings, video_encoder=name), timeout_s)
        except AdbError as e:
            if source != "hardware" or self._stop_requested:
                raise
            # the hardware encoder we named would not start: the server's own choice, once, and say so
            log.warning("scrcpy: the encoder %s did not start (%s): trying the server's default", name, e)
            name, source = None, "server default (the hardware encoder failed)"
            session = self._new_session(sink, serial, data, replace(settings, video_encoder=None), timeout_s)
        self._not_stopped()
        used = session.encoder or name
        hardware = next((e["hardware"] for e in self._encoders.get(serial, []) if e["name"] == used), None)
        self._encoder_info = {"name": used, "hardware": hardware, "chosen_by": source}
        self._in_use = settings
        with contextlib.suppress(AdbError):
            self._device_info = self._read_device_info(serial)
        kind = {True: "hardware", False: "software", None: "kind unknown"}[hardware]
        log.info(
            "scrcpy: %s (%s) streaming %s at %dx%d via %s (%s, %s)",
            serial,
            session.device_name,
            session.codec,
            *(session.size or (0, 0)),
            used or "the server's default encoder",
            kind,
            source,
        )

    def _new_session(
        self, sink: HubSink, serial: str, data: bytes, settings: Settings, timeout_s: float
    ) -> ScrcpySession:
        session = ScrcpySession(
            adb=self._adb,
            serial=serial,
            server_bytes=data,
            server_sha256=self._server_sha256,
            settings=settings,
            sink=sink,
            scid=self._scid,
        )
        with self._lock:
            self._not_stopped()
            self._session = session  # known before it is up, so that a stop that comes during the start reaches it
        try:
            session.start(timeout_s)
        except BaseException:
            with self._lock:
                if self._session is session:
                    self._session = None
            raise
        return session

    def _read_device_info(self, serial: str) -> dict[str, str]:
        command = "getprop ro.product.model; getprop ro.build.version.release; getprop ro.build.version.sdk"
        out = self._adb.shell(serial, command)
        model, release, sdk = [*out.splitlines(), "", "", ""][:3]
        return {"serial": serial, "model": model.strip(), "android": release.strip(), "sdk": sdk.strip()}
