"""``AdbDeviceHmi``: an Android device's picture and input as an HMI of the HMI interface, with nothing installed
on the device.

It is built by the ``hmi:`` option of :class:`~jumpstarter_driver_adb.driver.AdbDevice` and uses that device's
serial, ADB server and adb::

    export:
      phone:
        type: jumpstarter_driver_adb.driver.AdbDevice
        config:
          usb_port: "1-4.2"
          hmi: {}

Everything an HMI is (``video()``, ``input()``, ``hmi_info()``, ``status()``, ``start`` / ``stop`` /
``set_quality``, the recording calls, the hub, the adaptation to the network) is
:class:`~jumpstarter_driver_hmi.driver.HmiDriver`'s; the scrcpy part is
:class:`~jumpstarter_driver_adb.hmi.source.ScrcpySource`, which takes the subset of the interface that scrcpy has: H.264
(the default) or HEVC, one finger, the keyboard keys that have an Android key code, hard keys and text. This class
adds the scrcpy options and the Android extras a generic HMI does not have: ``press_key`` (also without a stream),
``type_text``, ``start_app``, the clipboard, ``rotate``, ``panel`` and ``list_encoders``.

The server starts on first use (a viewer, an input stream, ``start``) and ends when the lease ends, on ``stop``, or
after ``idle_stop_s`` seconds with nobody watching; ending it kills the process, removes the forward and, with the
server's own cleanup on, deletes the file it pushed.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from jumpstarter_driver_hmi.driver import HmiDriver
from jumpstarter_driver_hmi.hub.quality import top_rung
from jumpstarter_driver_hmi.source import HmiSource

from . import protocol, server_binary
from .adb import Adb
from .session import ScrcpySession, Settings
from .source import CODECS as OFFERED_CODECS
from .source import ScrcpySource
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import export

if TYPE_CHECKING:
    from jumpstarter_driver_adb.driver import AdbDevice

_PANELS = {
    "notifications": protocol.TYPE_EXPAND_NOTIFICATION_PANEL,
    "settings": protocol.TYPE_EXPAND_SETTINGS_PANEL,
    "collapse": protocol.TYPE_COLLAPSE_PANELS,
}
#: What the ``hmi:`` option of a device may not set: the device itself, and what the framework fills in.
_NOT_OPTIONS = frozenset({"device", "children", "uuid"})
_PRESSES = ("short", "long", "down", "up")


@dataclass(kw_only=True)
class AdbDeviceHmi(HmiDriver):
    """An Android device's picture and input, streamed by scrcpy's server. See the module documentation."""

    #: The device this is the HMI of: its serial, ADB server and adb are used as they are.
    device: AdbDevice = field(repr=False)

    # -- the pinned scrcpy-server ---------------------------------------------------------------------------------
    #: The file (default: ``$SCRCPY_SERVER``, then the usual install locations).
    server_path: str | None = None
    #: Its SHA-256. The default is the pin for scrcpy ``v4.1``; the file is refused if it differs.
    server_sha256: str = server_binary.PINNED_SHA256

    # -- the stream ---------------------------------------------------------------------------------------------------
    #: Seconds between key pictures: a viewer that lost a picture waits for the next one.
    idr_interval_s: int = 2
    #: Which display to mirror (a device may have several; 0 is the default one).
    display_id: int = 0
    #: A ``MediaCodec`` encoder by name (``c2.android.avc.encoder``), one that makes the codec in use (``codec``, or the
    #: one ``start(codec=...)`` chose). Unset: see ``encoder_policy``.
    video_encoder: str | None = None
    #: ``hardware`` (default): when no ``video_encoder`` is named, the device's hardware encoder of the codec in use is
    #: picked explicitly (from the server's own list, asked once per device) so that a software one never encodes a
    #: phone's display by accident; ``default``: whatever the server chooses. Either way ``status()`` says which encoder
    #: was used and whether it is hardware.
    encoder_policy: str = "hardware"
    #: Temporal layers to ask the encoder for (0: none). With 2, half the pictures are non-reference ones that a
    #: congested viewer can drop without breaking its picture; devices differ in whether their encoder honors it.
    temporal_layers: int = 0
    #: Wake the device when the server starts (scrcpy's own default).
    power_on: bool = True
    #: Keep the display on while plugged in (restored when the server ends).
    stay_awake: bool = False
    #: Draw the touch points on the device's display.
    show_touches: bool = False
    #: More ``key: value`` options for the server (the values are restricted to what needs no shell quoting).
    server_options: dict[str, str] | None = None

    @classmethod
    def client(cls) -> str:
        """Import path of the matching client class."""
        return "jumpstarter_driver_adb.hmi.client.AdbDeviceHmiClient"

    def __post_init__(self) -> None:
        """Check the configuration; touches no device, no server and no server file."""
        super().__post_init__()
        self._check_options()
        self._adb: Adb | None = None
        self._rotation: int | None = None
        self._scid = self._stable_scid()

    def _check_options(self) -> None:
        for name in ("idr_interval_s", "display_id", "temporal_layers"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ConfigurationError(f"{name} must be a whole number")
        for name in ("power_on", "stay_awake", "show_touches"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigurationError(f"{name} must be true or false")
        for name, kind in (("video_encoder", "an encoder's name"), ("server_path", "a path")):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ConfigurationError(f"{name} must be {kind}")
        self._check_server()
        if self.codec is not None and self.codec not in OFFERED_CODECS:
            raise ConfigurationError(f"codec must be one of {', '.join(OFFERED_CODECS)}: scrcpy makes no {self.codec}")
        try:
            self._settings().validate()
        except ValueError as e:
            raise ConfigurationError(str(e)) from e

    def _check_server(self) -> None:
        if self.encoder_policy not in ("hardware", "default"):
            raise ConfigurationError("encoder_policy must be 'hardware' or 'default'")
        if not isinstance(self.server_sha256, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", self.server_sha256):
            raise ConfigurationError("server_sha256 must be a hex SHA-256")
        options = self.server_options
        if options is not None and (not isinstance(options, dict) or not all(isinstance(k, str) for k in options)):
            raise ConfigurationError("server_options must be a mapping of option names to values")

    def _settings(self) -> Settings:
        """What the stream starts with: the top rung (the device's own size, the bit rate that suits it, 30 a second, or
        the operator's ceilings); the capture is started with the rung the quality says."""
        top = top_rung(None, self.max_size, self.bit_rate, self.max_fps)
        return Settings(
            max_size=top.max_size,
            bit_rate=top.bit_rate,
            max_fps=top.max_fps,
            idr_interval_s=self.idr_interval_s,
            display_id=self.display_id,
            video_encoder=self.video_encoder,
            temporal_layers=self.temporal_layers,
            control=self.control,
            power_on=self.power_on,
            stay_awake=self.stay_awake,
            show_touches=self.show_touches,
            server_options=tuple((str(k), str(v)) for k, v in (self.server_options or {}).items()),
        )

    def _stable_scid(self) -> int:
        """A socket-name id that is the same on every start for this device and display, so that a forward an earlier
        run left behind (the exporter was killed) can be found and removed by its name. 31 bits, as scrcpy's own."""
        device = self.device
        who = "|".join(
            str(part) for part in (device.transport, device.usb_port, device.serial, device.address, self.display_id)
        )
        return int.from_bytes(hashlib.sha256(who.encode()).digest()[:4], "big") & 0x7FFFFFFF

    # -- what the HMI driver asks for ---------------------------------------------------------------------------

    def _the_adb(self) -> Adb:
        """The adb commands of the parent device (built on first use: the device's adb is resolved by then)."""
        if self._adb is None:
            self._adb = Adb.of(self.device)
        return self._adb

    def make_source(self) -> HmiSource:
        """The scrcpy source, for this device's adb and serial."""
        return ScrcpySource(
            adb=self._the_adb(),
            serial_of=self.device.current_serial,
            settings=self._settings(),
            scid=self._scid,
            server_path=self.server_path,
            server_sha256=self.server_sha256.lower(),
            encoder_policy=self.encoder_policy,
        )

    def _scrcpy(self) -> ScrcpySource:
        return cast(ScrcpySource, self._the_source())

    def reset(self) -> None:
        """A new lease: what the last one learned about the device is forgotten (see the HMI driver's own)."""
        super().reset()
        self._rotation = None
        if self._source is not None:
            cast(ScrcpySource, self._source).reset_lease()

    # -- the Android extras ----------------------------------------------------------------------------------------

    def _require_control(self) -> None:
        if not self.control:
            raise RuntimeError("this stream is view only")

    def _control_session(self) -> ScrcpySession:
        """The running session with its control channel (the stream is started if it is not running)."""
        self._require_control()
        running = self._ensure_started().source
        session = cast(ScrcpySource, running).running_session()
        if session is None:
            raise RuntimeError("the HMI stream is not running")
        return session

    @export
    def press_key(self, key: str | int, press: str = "short") -> None:
        """Press an Android key (``HOME``, ``BACK``, ``APP_SWITCH``/``RECENTS``, ``POWER``, ``VOLUME_UP``... or a key
        code): ``press`` is ``short``, ``long``, ``down`` or ``up``.

        Works with the stream stopped: then it is ``input keyevent`` over adb (``short`` or ``long`` only).
        """
        self._require_control()
        code = protocol.keycode_of(key)
        if press not in _PRESSES:
            raise ValueError(f"press must be short, long, down or up: {press!r}")
        source = self._scrcpy()
        if source.running_session() is not None and source.input is not None:
            source.input.press_code(code, press)
            return
        if press not in ("short", "long"):
            raise ValueError("without a running stream, press must be 'short' or 'long'")
        source.adb.keyevent(source.serial(), code, long=press == "long")

    @export
    def type_text(self, text: str) -> None:
        """Type text into the focused field of the device (starts the stream if it is not running)."""
        self._control_session()
        source = self._scrcpy()
        assert source.input is not None
        source.input.text(text)

    @export
    def start_app(self, package: str, force_stop: bool = False) -> None:
        """Start an app by its package name, as the launcher does (the server finds its launcher activity);
        ``force_stop`` stops it first.

        A name that begins with ``?`` is searched for among the installed apps by its label. Over scrcpy's control
        channel: no shell, no Appium session (starts the stream if it is not running).
        """
        name = package.strip()
        if force_stop and not name.startswith(("+", "?")):
            name = "+" + name
        self._control_session().start_app(name)

    @export
    def set_clipboard(self, text: str, paste: bool = False) -> None:
        """Put ``text`` on the device's clipboard (and paste it into the focused field if ``paste``); returns when the
        device says it is done.

        Raises:
            RuntimeError: If the device did not say so within a few seconds.
            ValueError: If the text is longer than scrcpy takes (about 256 KiB).
        """
        if not self._control_session().set_clipboard(text, paste):
            raise RuntimeError("the device did not confirm the clipboard")

    @export
    def get_clipboard(self) -> str:
        """The device's clipboard text; empty when it has none (starts the stream if it is not running)."""
        return self._control_session().get_clipboard()

    @export
    def rotate(self, rotation: int | str | None = None) -> None:
        """Rotate the display and keep it there. No argument: between portrait and landscape; ``0``-``3``: to that
        rotation (quarter turns, 0 is the device's natural one); ``"auto"``: give the rotation back to the sensor.

        The display is *locked* at the rotation (auto-rotate is switched off, as ``wm user-rotation lock`` does),
        because scrcpy's own rotate message hands the rotation back to the sensor straight away, which on a device that
        sits flat turns it back. The stream's picture changes size with it (viewers get a new codec line). Works with
        the stream stopped, over adb.
        """
        self._require_control()
        source = self._scrcpy()
        serial = source.serial()
        if isinstance(rotation, str) and rotation.lower() in ("auto", "free"):
            source.adb.free_rotation(serial, self.display_id)
            self._rotation = None
            return
        if rotation is None:
            auto, locked = source.adb.rotation_state(serial)
            current = self._rotation if self._rotation is not None else (0 if auto else locked)
            target = 0 if current in (1, 3) else 1
        else:
            if (
                isinstance(rotation, bool)
                or not isinstance(rotation, int | float)
                or int(rotation) != rotation
                or not 0 <= rotation <= 3
            ):
                raise ValueError('rotation must be 0, 1, 2, 3 or "auto"')
            target = int(rotation)
        source.adb.lock_rotation(serial, target, self.display_id)
        self._rotation = target

    @export
    def panel(self, name: str) -> None:
        """Pull down the ``notifications`` or ``settings`` panel, or ``collapse`` what is open."""
        try:
            kind = _PANELS[name]
        except KeyError:
            raise ValueError(f"panel must be one of {', '.join(_PANELS)}") from None
        self._control_session().send(protocol.simple(kind))

    @export
    def list_encoders(self) -> list:
        """The device's video encoders as scrcpy's server lists them: ``{"codec", "name", "hardware", "alias_of"}``, in
        the server's order.

        Uses the cached server file (pushed if it is not there) and starts no stream.
        """
        return self._scrcpy().list_encoders()


def validate_hmi_config(config: Any) -> None:
    """Check a device's ``hmi`` option before its child is built: a mapping of options this HMI has.

    Raises:
        ConfigurationError: If it is not a mapping, or names an option that is not one (never a ``TypeError``).
    """
    if config is None:
        return
    allowed = {f.name for f in dataclasses.fields(AdbDeviceHmi) if f.init} - _NOT_OPTIONS
    if isinstance(config, dict) and not set(config) - allowed:
        return
    unknown = sorted(map(str, set(config) - allowed)) if isinstance(config, dict) else []
    hint = f" (not options: {', '.join(unknown)})" if unknown else ""
    raise ConfigurationError(f"hmi accepts only {', '.join(sorted(allowed))}{hint}")
