"""A stand-in for ``scrios stream --format framed``, for tests: it speaks framed protocol 1 with synthetic H.264, or
with the HMI package's real HEVC test pattern.

Run through :func:`jumpstarter_driver_iosdevice.fake_scrios_testing.write_fake_scrios`. It takes scrios's own arguments
(and records them), writes a ``config`` event and then access units at a controllable rate: the first one is a key unit
with its parameter sets in front, the others are deltas, and another key unit follows only when the driver writes
``keyframe`` to its stdin (the device has no periodic key units either). ``stop`` or the end of its stdin is answered
with a ``stopped`` event and a clean exit. It checks the touch, key and keyboard commands the way scrios does (a bad one
is answered with a ``hid`` event with ``ok: false``) and records every line it was given.

``--video-codec h264`` (the default) or ``--video-codec h265`` chooses the codec, as scrios's own option does, and
anything else exits 2 like scrios's argument parser. H.264 units are synthetic (an SPS of the picture's size, a
counter for a picture). HEVC units are the access units of ``jumpstarter_driver_hmi.testing.pattern`` (a real 240x400
test chart with its VPS, SPS and PPS in front of every key unit), so the ``config`` names the codec string of that
stream (``hev1.1.6.L...``), and its size is the pattern's unless ``FAKE_SCRIOS_SIZE`` says otherwise (then only the
``config`` changes).

``kb down USAGE`` and ``kb up USAGE`` take a usage in hex (``04``, ``0x04``: one to four digits, ``01`` to ``e7``); a
key that is down and is pressed again stays down, and one that is not down is let go of without a word. ``kb type``
takes at most 4096 characters of base64 of UTF-8 and answers a ``warning`` for the ones that are not in the US layout.
When the keyboard is not ready (``FAKE_SCRIOS_HID_KEYBOARD=false``, or a ``hid`` operation that says so) every valid
``kb`` command is answered ``ok: false``. Keys still down at a ``stop`` are recorded (``{"event": "keys_released"}``).

Environment (all optional):

``FAKE_SCRIOS_RECORD``        file that gets one JSON line per start, command and end
                              (``{"event": "start", "argv": [...]}``, ``{"event": "command", "line": "keyframe"}``)
``FAKE_SCRIOS_CONTROL``       file the test appends JSON operations to while the fake runs (see ``OPERATIONS``); only
                              what is appended after it started is read
``FAKE_SCRIOS_VERSION``       what ``--version`` prints (default ``scrios 0.0.0-fake``); ``FAKE_SCRIOS_VERSION_EXIT``:
                              its exit status; ``FAKE_SCRIOS_VERSION_DELAY``: seconds it takes to answer
``FAKE_SCRIOS_FPS``           access units a second (default 60)
``FAKE_SCRIOS_SIZE``          the picture's size, ``WxH`` (default 1206x2622)
``FAKE_SCRIOS_KEY_BYTES``     size of a key unit's picture, bytes (default 2000); ``FAKE_SCRIOS_DELTA_BYTES``: a
                              delta's (default 200)
``FAKE_SCRIOS_CHUNK``         write every frame in pieces of this many bytes (splits headers and payloads across reads)
``FAKE_SCRIOS_STATS_EVERY``   send a ``stats`` event every this many units (default 25)
``FAKE_SCRIOS_START_DELAY``   seconds to wait before the ``config``
``FAKE_SCRIOS_FAIL_START``    an error message: send it as an ``error`` event and exit 2 before streaming
``FAKE_SCRIOS_SILENT``        say nothing at all and wait to be stopped
``FAKE_SCRIOS_NO_CONFIG``     stream access units and never say a ``config``
``FAKE_SCRIOS_NO_UNITS``      say the ``config`` and then no access unit at all (``pause`` from the start);
                              ``FAKE_SCRIOS_SILENT_AFTER_RUNS``: only the runs after this many (of the record file)
``FAKE_SCRIOS_PROTOCOL``      the ``protocol`` of the ``config``: a number (default 1) or ``absent``
``FAKE_SCRIOS_CODEC``         the codec string of the ``config`` (default: the one of the stream: ``avc1.…`` from the
                              SPS, or ``hev1.…`` from the HEVC pattern)
``FAKE_SCRIOS_BARE_KEYS``     key units without SPS and PPS in front, and without the PARAMS flag, after the first one
``FAKE_SCRIOS_IGNORE_STDIN``  never read stdin (the pipe fills up and the driver's writes block)
``FAKE_SCRIOS_IGNORE_STOP``   ignore ``stop`` and the end of stdin; ``FAKE_SCRIOS_IGNORE_TERM``: ignore SIGTERM as well
``FAKE_SCRIOS_STDERR_FLOOD``  write lines to stderr as fast as it can
``FAKE_SCRIOS_KB_DELAY``      seconds to spend on every ``kb`` command before the next line is read
``FAKE_SCRIOS_HID``           with control: ``ready`` (the default: say ``{"event": "hid", "ready": true}`` after the
                              ``config``), ``failed`` (``ready: false`` with a message) or ``never`` (say nothing);
                              ``FAKE_SCRIOS_HID_DELAY``: seconds to wait before saying it
``FAKE_SCRIOS_HID_KEYBOARD``  what the ready event says of the keyboard: ``true`` (the default), ``false`` or ``absent``
                              (``false``: ``kb`` commands fail until a ``hid`` operation says ``keyboard: true``)
``FAKE_SCRIOS_EXIT_AFTER_UNITS``  die after this many units, with ``FAKE_SCRIOS_EXIT_CODE`` (default 1) after the
                              ``error`` event ``FAKE_SCRIOS_EXIT_ERROR`` (if set); ``FAKE_SCRIOS_FAIL_RUNS``: only the
                              first this many runs of the record file do it
``FAKE_SCRIOS_ROTATION``      put ``rotation`` (0, 90, 180 or 270) in the ``config``; ``FAKE_SCRIOS_POINT_SCALE``,
                              ``FAKE_SCRIOS_CURRENT_ORIENTATION``, ``FAKE_SCRIOS_NATIVE_ORIENTATION`` and
                              ``FAKE_SCRIOS_CLOCK_SOURCE`` likewise (none of them is in it unless set)
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import json
import os
import re
import signal
import struct
import sys
import threading
import time
import traceback

PRESSES = ("short", "long")
#: The values of scrios's ``--video-codec`` (not ``hevc``).
VIDEO_CODECS = ("h264", "h265")
#: What scrios's argument parser exits with.
EXIT_USAGE = 2
#: The usages ``kb`` takes (the keyboard page's), and how it writes one: one to four hex digits, ``0x`` optional.
MAX_USAGE = 0xE7
USAGE_TEXT = re.compile(r"(0[xX])?[0-9a-fA-F]{1,4}")
MAX_TYPE_CHARS = 4096
KEY_NAMES = ("home", "lock", "power", "sleep", "volume-up", "volume-down", "mute", "siri", "app-switcher", "switcher")
FLAG_KEY, FLAG_PARAMS, FLAG_CONTROL = 0x01, 0x02, 0x80
#: The optional fields of a ``config``, and the environment variable that sets each one at the start.
EXTRAS = {
    "rotation": ("FAKE_SCRIOS_ROTATION", int),
    "point_scale": ("FAKE_SCRIOS_POINT_SCALE", float),
    "current_orientation": ("FAKE_SCRIOS_CURRENT_ORIENTATION", str),
    "native_orientation": ("FAKE_SCRIOS_NATIVE_ORIENTATION", str),
    "clock_source": ("FAKE_SCRIOS_CLOCK_SOURCE", str),
}

# What the control file may ask for, one JSON object a line:
#   {"op": "exit", "code": 3, "error": "boom"}       send the error (if any) and exit with this status
#   {"op": "signal", "signal": 9}                    die from this signal, saying nothing
#   {"op": "config", "width": 1000, "height": 2000}  a new ``config`` event, then a key unit. ``width`` and ``height``
#                                                    default to the current ones; ``rotation``, ``point_scale``,
#                                                    ``current_orientation``, ``native_orientation`` and
#                                                    ``clock_source`` set (or, as null, remove) that field;
#                                                    ``"key": false`` sends no key unit (the SPS did not change)
#   {"op": "hid", "ready": true, "keyboard": false}  a ``hid`` event with these fields
#   {"op": "hid_error", "message": "..."}            a ``hid`` event with ``ok: false`` and that message
#   {"op": "event", "event": {...}}                  any control event as it is
#   {"op": "fps", "fps": 500}                        change the rate
#   {"op": "key"}                                    the next unit is a key unit, asked or not
#   {"op": "bare_key"}                               the next unit is a key unit without parameter sets (only that one)
#   {"op": "stats", "dropped_units": 2}              a ``stats`` event that says units were lost
#   {"op": "oversize"}                               a header that claims 2 GiB
#   {"op": "bad_control"}                            a control frame that is not JSON
#   {"op": "garbage"}                                bytes that are no frame at all
#   {"op": "pause"} / {"op": "resume"}               stop and resume the units
OPERATIONS = (
    "exit",
    "signal",
    "config",
    "fps",
    "key",
    "bare_key",
    "hid",
    "hid_error",
    "event",
    "stats",
    "oversize",
    "bad_control",
    "garbage",
    "pause",
    "resume",
)


def frame(payload: bytes, pts_us: int = 0, flags: int = 0) -> bytes:
    """One frame as scrios writes it (written out here: the fake shares no framing code with the driver)."""
    return struct.pack(">IQB", len(payload), pts_us, flags) + payload


def control(**message) -> bytes:
    """One control frame."""
    return frame(json.dumps(message).encode(), 0, FLAG_CONTROL)


def hmi_package_helpers():
    """``PPS``, ``START_CODE``, ``codec_string`` and ``make_sps`` of the HMI package's fake source (pictures that
    the hub and the MP4 muxer can work on).

    The package ``jumpstarter_driver_hmi.testing`` imports the conformance suite, and with it pytest and the client:
    a third of a second for every scrios the tests start. Its ``fake`` module needs none of that, so it is imported
    under a bare stand-in of the package.
    """
    import types

    import jumpstarter_driver_hmi

    stand_in = types.ModuleType("jumpstarter_driver_hmi.testing")
    stand_in.__path__ = [os.path.join(os.path.dirname(jumpstarter_driver_hmi.__file__), "testing")]
    sys.modules["jumpstarter_driver_hmi.testing"] = stand_in
    from jumpstarter_driver_hmi.testing.fake import PPS, START_CODE, codec_string, make_sps

    return PPS, START_CODE, codec_string, make_sps


def hevc_pattern():
    """What the HEVC stream is made of: ``(codec string, (width, height), key units, other units, strip)``, from the
    HMI package's test pattern (``strip`` takes the parameter sets off a key unit).
    """
    hmi_package_helpers()  # the stand-in package, so that ``pattern`` can be imported without pytest
    from jumpstarter_driver_hmi.hub.codecs import first_sps, has_idr, hevc_codec_string, iter_nals, nal_begin
    from jumpstarter_driver_hmi.testing.pattern import PATTERN_SIZE, pattern_units

    units = pattern_units("hevc")
    sps = first_sps(units[0], "hevc")
    assert sps is not None  # the first unit is a key picture with its parameter sets

    def strip(unit: bytes) -> bytes:
        """The key unit without its parameter sets: from the start code of its first slice."""
        for kind, start, _ in iter_nals(unit, "hevc"):
            if kind < 32:
                return unit[nal_begin(unit, start) :]
        return unit

    keys = [u for u in units if has_idr(u, "hevc")]
    others = [u for u in units if not has_idr(u, "hevc")]
    return hevc_codec_string(sps), PATTERN_SIZE, keys, others, strip


def option(args: list[str], name: str, default: str) -> str:
    """The value of ``--name VALUE`` or ``--name=VALUE`` in ``args`` (``default`` when it is not there)."""
    for at, argument in enumerate(args):
        if argument == name and at + 1 < len(args):
            return args[at + 1]
        if argument.startswith(name + "="):
            return argument.partition("=")[2]
    return default


class Fake:
    """The fake's state: one instance per run."""

    def __init__(self) -> None:
        """Read the environment and the arguments."""
        PPS, START_CODE, codec_string, make_sps = hmi_package_helpers()

        self.make_sps, self.codec_string, self.pps, self.start_code = make_sps, codec_string, PPS, START_CODE
        self.env = os.environ
        self.hevc = option(sys.argv[1:], "--video-codec", "h264") == "h265"
        self.record_path = self.env.get("FAKE_SCRIOS_RECORD")
        self.control_path = self.env.get("FAKE_SCRIOS_CONTROL")
        self.fps = float(self.env.get("FAKE_SCRIOS_FPS", "60"))
        width, _, height = self.env.get("FAKE_SCRIOS_SIZE", "1206x2622").partition("x")
        self.width, self.height = int(width), int(height)
        self.hevc_codec = ""
        self.key_units: list[bytes] = []
        self.other_units: list[bytes] = []
        self.strip = lambda unit: unit
        self.key_index = self.other_index = 0
        if self.hevc:
            self.hevc_codec, size, self.key_units, self.other_units, self.strip = hevc_pattern()
            if not self.env.get("FAKE_SCRIOS_SIZE"):
                self.width, self.height = size
        self.control_on = "--no-control" not in sys.argv[1:] and "-n" not in sys.argv[1:]
        self.hid_mode = self.env.get("FAKE_SCRIOS_HID", "ready")
        self.hid_keyboard = self.env.get("FAKE_SCRIOS_HID_KEYBOARD", "true")
        #: whether the virtual keyboard works (the ``kb`` commands fail while it does not)
        self.keyboard_works = self.hid_keyboard != "false"
        #: the usages that are down, as scrios's virtual keyboard has them (the reader thread only)
        self.pressed: set[int] = set()
        self.hid_at: float | None = None
        #: frames other threads want sent: only the main thread writes to stdout
        self.pending: list[bytes] = []
        self.pending_lock = threading.Lock()
        self.extra = {name: kind(self.env[var]) for name, (var, kind) in EXTRAS.items() if self.env.get(var)}
        self.key_bytes = int(self.env.get("FAKE_SCRIOS_KEY_BYTES", "2000"))
        self.delta_bytes = int(self.env.get("FAKE_SCRIOS_DELTA_BYTES", "200"))
        self.chunk = int(self.env.get("FAKE_SCRIOS_CHUNK", "0"))
        self.stats_every = int(self.env.get("FAKE_SCRIOS_STATS_EVERY", "25"))
        self.bare_keys = bool(self.env.get("FAKE_SCRIOS_BARE_KEYS"))
        self.bare_next = False
        self.ignore_stop = bool(self.env.get("FAKE_SCRIOS_IGNORE_STOP"))
        self.want_key = True
        self.paused = False
        self.stop = threading.Event()
        self.stop_reason = ""
        self.out = sys.stdout.buffer.fileno()
        self.lock = threading.Lock()
        self.seq = self.keys = self.plis = self.dropped = self.sent_bytes = 0
        self.sps = self.make_sps(self.width, self.height)
        self.control_offset = (
            os.path.getsize(self.control_path) if self.control_path and os.path.exists(self.control_path) else 0
        )

    # -- output --------------------------------------------------------------------------------------------------------

    def record(self, **event) -> None:
        """Append one line to the record file."""
        if self.record_path:
            line = json.dumps({"pid": os.getpid(), "t": time.time(), **event}) + "\n"
            fd = os.open(self.record_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode())
            finally:
                os.close(fd)

    def runs_so_far(self) -> int:
        """How many runs the record file has seen, this one included."""
        if not self.record_path or not os.path.exists(self.record_path):
            return 1
        with open(self.record_path, encoding="utf-8") as record:
            return sum(1 for line in record if '"event": "start"' in line)

    def write(self, data: bytes) -> None:
        """Write to stdout, all of it, in pieces if asked to."""
        with self.lock:
            if self.chunk:
                for at in range(0, len(data), self.chunk):
                    os.write(self.out, data[at : at + self.chunk])
                    time.sleep(0.0005)
            else:
                view = memoryview(data)
                while view:
                    view = view[os.write(self.out, view) :]

    def config(self) -> None:
        """Say what the picture is."""
        coded_w, coded_h = -(-self.width // 16) * 16, -(-self.height // 16) * 16
        fields = {
            "codec": self.env.get("FAKE_SCRIOS_CODEC") or self.hevc_codec or self.codec_string(self.sps),
            "width": self.width,
            "height": self.height,
            "coded_width": coded_w,
            "coded_height": coded_h,
            "fps": 60,
            "clock_hz": 24000,
            "clock_source": "default",
            "ssrc": 123,
            "device_ssrc": 456,
            **self.extra,
        }
        protocol = self.env.get("FAKE_SCRIOS_PROTOCOL", "1")
        if protocol != "absent":
            fields["protocol"] = int(protocol)
        self.write(control(event="config", **fields))

    def stats(self) -> None:
        """Say how the stream is doing."""
        self.write(
            control(
                event="stats",
                packets=self.seq * 3,
                access_units=self.seq,
                bytes=self.sent_bytes,
                lost=0,
                dropped_units=self.dropped,
                keyframes=self.keys,
                plis=self.plis,
                rtcp=0,
                b_slices=0,
                edge_downs=0,
            )
        )

    def unit(self) -> None:
        """One access unit: a key unit when one was asked for (and at the start), else a delta."""
        key = self.want_key
        self.want_key = False
        self.seq += 1
        pts = int(self.seq * 1_000_000 / 60)
        if key:
            self.keys += 1
            bare = (self.bare_keys and self.keys > 1) or self.bare_next
            self.bare_next = False
            payload, flags = self.key_unit(bare)
        else:
            payload, flags = self.delta_unit(), 0
        self.sent_bytes += len(payload)
        self.write(frame(payload, pts, flags))

    def key_unit(self, bare: bool) -> tuple[bytes, int]:
        """A key unit and its flags: with its parameter sets in front, or (``bare``) without them and the flag."""
        if self.hevc:
            picture = self.key_units[self.key_index % len(self.key_units)]
            self.key_index += 1
            return (self.strip(picture), FLAG_KEY) if bare else (picture, FLAG_KEY | FLAG_PARAMS)
        start = self.start_code
        picture = start + b"\x65\x88\x84" + f"{self.seq:08x}".encode() + b"\xaf" * self.key_bytes
        if bare:
            return picture, FLAG_KEY
        return start + self.sps + start + self.pps + picture, FLAG_KEY | FLAG_PARAMS

    def delta_unit(self) -> bytes:
        """A unit that is not a key picture."""
        if self.hevc:
            self.other_index += 1
            return self.other_units[(self.other_index - 1) % len(self.other_units)]
        return self.start_code + b"\x41\x9a" + f"{self.seq:08x}".encode() + b"\x11" * self.delta_bytes

    # -- input ---------------------------------------------------------------------------------------------------------

    def read_stdin(self) -> None:
        """Read the driver's commands from the raw descriptor, a line at a time.

        Not through ``sys.stdin``: a thread blocked in its buffered reader holds that buffer's lock, and an interpreter
        that finalizes while it does so aborts; this thread never touches a buffered stream, and the process ends with
        ``os._exit``.
        """
        pending = b""
        while True:
            try:
                data = os.read(0, 65536)
            except OSError:
                data = b""
            if not data:
                break
            pending += data
            while (end := pending.find(b"\n")) >= 0:
                line, pending = pending[:end].decode(errors="replace").strip(), pending[end + 1 :]
                self.record(event="command", line=line[:30000])
                if (problem := self.check(line)) is not None:
                    with self.pending_lock:
                        self.pending.append(control(event="hid", ok=False, message=problem))
                if line.startswith("kb ") and (delay := float(self.env.get("FAKE_SCRIOS_KB_DELAY", "0"))):
                    time.sleep(delay)
                if line == "keyframe":
                    self.plis += 1
                    self.want_key = True
                elif line == "stop" and not self.ignore_stop:
                    self.stop_reason = "stop"
                    self.release_keys()
                    self.stop.set()
                    return
        self.record(event="stdin_closed")
        if not self.ignore_stop:
            self.stop_reason = "eof"
            self.release_keys()
            self.stop.set()

    def release_keys(self) -> None:
        """Stopping lets go of every key that is down (and the record says which were)."""
        if self.pressed:
            self.record(event="keys_released", keys=[f"{usage:02x}" for usage in sorted(self.pressed)])
            self.pressed.clear()

    def check(self, line: str) -> str | None:
        """What is wrong with a ``touch``, ``key`` or ``kb`` command, or ``None``."""
        parts = line.split()
        if not parts or parts[0] not in ("touch", "key", "kb"):
            return None
        if not self.control_on:
            return "control is off (--no-control)"
        if parts[0] == "kb":
            return self.check_keyboard(parts)
        if parts[0] == "touch":
            if len(parts) != 5 or parts[1] not in ("down", "move", "up") or not all(_number(n) for n in parts[2:]):
                return f"bad touch command {line!r}"
            if parts[4] != "0":
                return f"touch finger {parts[4]}: only finger 0 is supported"
        elif len(parts) not in (2, 3) or parts[1] not in KEY_NAMES or (len(parts) == 3 and (parts[2] not in PRESSES)):
            return f"unknown key {' '.join(parts[1:])!r}"
        if self.hid_mode == "failed":
            return "touch and keys are not available"
        return None

    def check_keyboard(self, parts: list[str]) -> str | None:
        """What is wrong with a ``kb down|up <usage hex>`` or ``kb type <base64>`` command, or ``None``. A command that
        is right is done unless the keyboard does not work.
        """
        if len(parts) == 3 and parts[1] in ("down", "up"):
            problem = self.check_usage(parts[1], parts[2])
        elif len(parts) == 3 and parts[1] == "type":
            problem = self.check_text(parts[2])
        else:
            return "usage: kb down <usage>, kb up <usage> (hex), or kb type <base64>"
        if problem is None and not self.keyboard_works:
            return "keyboard: mainKeyboard transport is unavailable"
        return problem

    def check_usage(self, direction: str, text: str) -> str | None:
        """A ``kb down`` or ``kb up`` is right when the usage is a hex number of the keyboard page; a key that is down
        stays down when pressed again, and one that is not down is let go of without a word.
        """
        if not USAGE_TEXT.fullmatch(text):
            return f"usage {text[:20]!r} is not a hex number"
        usage = int(text, 16)
        if not 0x01 <= usage <= MAX_USAGE:
            return f"usage {text!r} is out of range (01 to {MAX_USAGE:02x})"
        if self.keyboard_works:
            if direction == "down":
                self.pressed.add(usage)
            else:
                self.pressed.discard(usage)
        return None

    def check_text(self, payload: str) -> str | None:
        """A ``kb type`` is right when it is base64 of at most 4096 characters of UTF-8; what the US layout has no key
        for is skipped, and said so in a warning.
        """
        try:
            text = base64.b64decode(payload, validate=True).decode("utf-8")
        except binascii.Error:
            return "kb type: the text is not valid base64"
        except UnicodeDecodeError:
            return "kb type: the text is not valid UTF-8"
        if len(text) > MAX_TYPE_CHARS:
            return f"kb type: {len(text)} characters, at most {MAX_TYPE_CHARS}"
        skipped = sum(1 for c in text if not (c.isascii() and (c.isprintable() or c in "\n\t")))
        if skipped and self.keyboard_works:
            message = f"kb type: {skipped} characters are not in the US layout table and were skipped"
            with self.pending_lock:
                self.pending.append(control(event="hid", warning=message))
        return None

    def flush_pending(self) -> None:
        """Write what other threads asked to have said."""
        with self.pending_lock:
            frames, self.pending = self.pending, []
        for data in frames:
            self.write(data)

    def maybe_hid(self) -> None:
        """Say whether touch and keys are ready, once, when its time has come."""
        if self.hid_at is None or time.monotonic() < self.hid_at:
            return
        self.hid_at = None
        if self.hid_mode == "ready":
            says = {} if self.hid_keyboard == "absent" else {"keyboard": self.hid_keyboard == "true"}
            self.write(control(event="hid", ready=True, mapping="auto", **says))
        elif self.hid_mode == "failed":
            self.write(control(event="hid", ready=False, message="the device refused the HID service"))

    def read_control(self) -> None:
        """Do what the test appended to the control file."""
        if not self.control_path or not os.path.exists(self.control_path):
            return
        with open(self.control_path, "rb") as source:
            source.seek(self.control_offset)
            data = source.read()
        complete = data[: data.rfind(b"\n") + 1]
        self.control_offset += len(complete)
        for line in complete.splitlines():
            if line.strip():
                self.apply(json.loads(line))

    def apply(self, op: dict) -> None:  # noqa: C901 (one branch per operation)
        """One operation of the control file."""
        name = op["op"]
        assert name in OPERATIONS, name
        self.record(event="op", op=op)
        if name == "exit":
            if op.get("error"):
                self.write(control(event="error", message=op["error"]))
            os._exit(int(op.get("code", 1)))
        elif name == "signal":
            os.kill(os.getpid(), int(op["signal"]))
            time.sleep(10)
        elif name == "config":
            self.width, self.height = int(op.get("width", self.width)), int(op.get("height", self.height))
            self.sps = self.make_sps(self.width, self.height)
            for field in EXTRAS:
                if field in op:
                    if op[field] is None:
                        self.extra.pop(field, None)
                    else:
                        self.extra[field] = op[field]
            self.config()
            self.want_key = bool(op.get("key", True))
        elif name == "fps":
            self.fps = float(op["fps"])
        elif name == "key":
            self.want_key = True
        elif name == "hid":
            if isinstance(op.get("keyboard"), bool):
                self.keyboard_works = op["keyboard"]
            self.write(control(event="hid", **{k: v for k, v in op.items() if k != "op"}))
        elif name == "hid_error":
            self.write(control(event="hid", ok=False, message=op.get("message", "a touch could not be made")))
        elif name == "event":
            self.write(control(**op["event"]))
        elif name == "bare_key":
            self.want_key = self.bare_next = True
        elif name == "stats":
            self.dropped = int(op.get("dropped_units", self.dropped))
            self.stats()
        elif name == "oversize":
            self.write(struct.pack(">IQB", 0x7FFFFFFF, 0, 0))
        elif name == "bad_control":
            self.write(frame(b"this is not json", 0, FLAG_CONTROL))
        elif name == "garbage":
            self.write(b"\xff" * 64)
        elif name == "pause":
            self.paused = True
        elif name == "resume":
            self.paused = False

    # -- main ----------------------------------------------------------------------------------------------------------

    def run(self) -> int:
        """Run until stopped; the exit status."""
        self.record(event="start", argv=sys.argv[1:], cwd=os.getcwd())
        if self.env.get("FAKE_SCRIOS_IGNORE_TERM"):
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if self.env.get("FAKE_SCRIOS_STDERR_FLOOD"):
            threading.Thread(target=flood_stderr, daemon=True).start()
        if not self.env.get("FAKE_SCRIOS_IGNORE_STDIN"):
            threading.Thread(target=self.read_stdin, daemon=True).start()
        if self.env.get("FAKE_SCRIOS_SILENT"):
            while not self.stop.wait(0.05):
                pass
            return 0
        if message := self.env.get("FAKE_SCRIOS_FAIL_START"):
            self.write(control(event="error", message=message))
            return 2
        if delay := float(self.env.get("FAKE_SCRIOS_START_DELAY", "0")):
            time.sleep(delay)
        if not self.env.get("FAKE_SCRIOS_NO_CONFIG"):
            self.config()
        if self.control_on:
            self.hid_at = time.monotonic() + float(self.env.get("FAKE_SCRIOS_HID_DELAY", "0"))
        after = self.env.get("FAKE_SCRIOS_SILENT_AFTER_RUNS")
        self.paused = bool(self.env.get("FAKE_SCRIOS_NO_UNITS")) or (
            after is not None and self.runs_so_far() > int(after)
        )
        return self.stream()

    def crashes_now(self) -> bool:
        """Whether this run is one of those that die (``FAKE_SCRIOS_FAIL_RUNS``)."""
        runs = self.env.get("FAKE_SCRIOS_FAIL_RUNS")
        return runs is None or self.runs_so_far() <= int(runs)

    def stream(self) -> int:
        """Access units at the rate, until told to stop; the control file, the news of the touch input and the answers
        to the driver's commands go out between them (this thread is the only one that writes).
        """
        started = time.monotonic()
        emitted = 0
        die_after = int(self.env.get("FAKE_SCRIOS_EXIT_AFTER_UNITS", "0")) if self.crashes_now() else 0
        while not self.stop.is_set():
            self.read_control()
            self.maybe_hid()
            self.flush_pending()
            if self.paused:
                self.stop.wait(0.01)
                continue
            self.unit()
            emitted += 1
            if die_after and emitted >= die_after:
                if message := self.env.get("FAKE_SCRIOS_EXIT_ERROR"):
                    self.write(control(event="error", message=message))
                return int(self.env.get("FAKE_SCRIOS_EXIT_CODE", "1"))
            if self.stats_every and emitted % self.stats_every == 0:
                self.stats()
            wait = started + emitted / self.fps - time.monotonic()
            if wait > 0:
                self.stop.wait(wait)
            else:
                started = time.monotonic() - emitted / self.fps  # not able to keep the rate: do not try to catch up
        self.flush_pending()  # what the last commands were answered
        self.write(control(event="stopped"))
        self.record(event="stopped", reason=self.stop_reason)
        return 0


def _number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def flood_stderr() -> None:
    """Write lines to stderr until the process ends (or nobody reads them and the pipe is full)."""
    line = (b"scrios flood " + b"x" * 200 + b"\n") * 20
    with contextlib.suppress(OSError):
        while True:
            os.write(2, line)
            time.sleep(0.0005)


def main() -> None:
    """Run, say what went wrong if anything did, and leave without finalizing the interpreter.

    Everything this process writes goes out with ``os.write`` (nothing is buffered), and the only other threads only
    read stdin from the raw descriptor or write to stderr, so ``os._exit`` loses nothing and cannot race a thread that
    holds a lock.
    """
    args = sys.argv[1:]
    if "--version" in args or "-V" in args:
        time.sleep(float(os.environ.get("FAKE_SCRIOS_VERSION_DELAY", "0")))
        print(os.environ.get("FAKE_SCRIOS_VERSION", "scrios 0.0.0-fake"))
        sys.stdout.flush()
        os._exit(int(os.environ.get("FAKE_SCRIOS_VERSION_EXIT", "0")))
    framed = "--format" in args and args[args.index("--format") + 1 : args.index("--format") + 2] == ["framed"]
    if args[:1] != ["stream"] or not framed:
        # a bare ``scrios`` (or one without the framed format) is not what the driver may run
        os.write(2, b"fake scrios: expected `stream --format framed`\n")
        os._exit(EXIT_USAGE)
    if (codec := option(args, "--video-codec", "h264")) not in VIDEO_CODECS:
        message = f"error: invalid value {codec!r} for '--video-codec <VIDEO_CODEC>' [possible values: h264, h265]\n"
        os.write(2, message.encode())
        os._exit(EXIT_USAGE)
    code = 1
    try:
        code = Fake().run()
    except BaseException:  # noqa: BLE001 - whatever it was, report it and leave cleanly
        with contextlib.suppress(OSError):
            os.write(2, traceback.format_exc().encode())
    finally:
        with contextlib.suppress(Exception):
            sys.stdout.flush()
            sys.stderr.flush()
        os._exit(code)


if __name__ == "__main__":
    main()
