"""A source of synthetic pictures, for tests, demos and ``MockHmi``: no device, no network.

The access units are shaped like an encoder's (an SPS, a PPS and an IDR slice at every key picture, a P slice between)
with a valid SPS for
the picture's size, so everything downstream (the hub, the framing, the MP4 muxer) works on them; the slices carry a
counter instead of a
picture, so they do not decode to anything.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from ..common import Capabilities, CodecLine, HardKey, InputError, QualityCaps
from ..hub.quality import Rung
from ..source import HubSink, InputSink, HmiSource

START_CODE = b"\x00\x00\x00\x01"
#: The PPS of a baseline-profile stream (id 0, CAVLC, no weighted prediction).
PPS = b"\x68\xce\x3c\x80"


class _Bits:
    """A bit writer for the few syntax elements an SPS has."""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def u(self, count: int, value: int) -> None:
        self.bits += [(value >> (count - 1 - i)) & 1 for i in range(count)]

    def exp_golomb(self, value: int) -> None:
        value += 1
        length = value.bit_length()
        self.u(length - 1, 0)
        self.u(length, value)

    def rbsp(self) -> bytes:
        bits = [*self.bits, 1]
        bits += [0] * (-len(bits) % 8)
        return bytes(int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8))


def _escaped(rbsp: bytes) -> bytes:
    """Insert the emulation-prevention bytes a NAL unit needs."""
    out = bytearray()
    zeros = 0
    for byte in rbsp:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def make_sps(width: int, height: int) -> bytes:
    """A baseline-profile SPS NAL unit (with its header byte, no start code) for a picture of ``width`` x ``height``
    (cropped from a
    multiple of 16 when it is not one)."""
    coded_w, coded_h = -(-width // 16) * 16, -(-height // 16) * 16
    bits = _Bits()
    bits.u(8, 66)  # profile_idc: baseline
    bits.u(8, 0xC0)  # constraint_set0 and 1
    bits.u(8, 31)  # level_idc 3.1
    bits.exp_golomb(0)  # seq_parameter_set_id
    bits.exp_golomb(0)  # log2_max_frame_num_minus4
    bits.exp_golomb(2)  # pic_order_cnt_type
    bits.exp_golomb(1)  # max_num_ref_frames
    bits.u(1, 0)  # gaps_in_frame_num_value_allowed_flag
    bits.exp_golomb(coded_w // 16 - 1)
    bits.exp_golomb(coded_h // 16 - 1)
    bits.u(1, 1)  # frame_mbs_only_flag
    bits.u(1, 1)  # direct_8x8_inference_flag
    if (coded_w, coded_h) != (width, height):
        bits.u(1, 1)  # frame_cropping_flag
        bits.exp_golomb(0)
        bits.exp_golomb((coded_w - width) // 2)
        bits.exp_golomb(0)
        bits.exp_golomb((coded_h - height) // 2)
    else:
        bits.u(1, 0)
    bits.u(1, 0)  # vui_parameters_present_flag
    return b"\x67" + _escaped(bits.rbsp())


def codec_string(sps: bytes) -> str:
    """The RFC 6381 string of an SPS NAL unit."""
    return "avc1." + sps[1:4].hex().upper()


class FakeInputSink(InputSink):
    """Records what it is given, in order. ``refuse`` makes every call raise ``InputError`` (a device that is busy)."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.refuse: str | None = None
        self._lock = threading.Lock()

    def _record(self, event: tuple) -> None:
        if self.refuse:
            raise InputError(self.refuse)
        with self._lock:
            self.events.append(event)

    def touch(self, kind: str, x: int, y: int, size: tuple[int, int]) -> None:
        """Record a touch."""
        self._record(("touch", kind, x, y, size))

    def key(self, key: str, press: str) -> None:
        """Record a key."""
        self._record(("key", key, press))

    def text(self, text: str) -> None:
        """Record typed text."""
        self._record(("text", text))

    def release(self) -> None:
        """Record the end of the input."""
        with self._lock:
            self.events.append(("release",))

    def snapshot(self) -> list[tuple]:
        """What has been recorded so far."""
        with self._lock:
            return list(self.events)


class FakeSource(HmiSource):
    """Makes a picture every ``1 / fps`` seconds on a thread of its own: a key picture every ``gop`` pictures, a delta
    picture between.

    The knobs a test turns: :meth:`change_picture` (a new size or rotation), :meth:`die` (the capture ends by itself),
    :meth:`pause`
    (silence), ``fail_start`` (``start`` raises), ``start_gate`` (``start`` waits), ``on_keyframe_request`` and the
    input sink's ``events``.
    """

    def __init__(
        self,
        *,
        width: int = 480,
        height: int = 800,
        fps: int = 30,
        gop: int = 30,
        control: bool = True,
        quality: bool = True,
        keyframe_cost: str = "cheap",
        keys: tuple[str, ...] = ("HOME", "BACK", "APP_SWITCH", "POWER", "VOLUME_UP", "VOLUME_DOWN"),
        text: bool = True,
        frame_bytes: int = 600,
        key_bytes: int = 4000,
    ) -> None:
        self.width, self.height, self.fps, self.gop = width, height, fps, gop
        self.rotation: int | None = None
        self._control = control
        self._quality = quality
        self._keyframe_cost = keyframe_cost
        self._keys = tuple(keys)
        self._text = text
        self._frame_bytes, self._key_bytes = frame_bytes, key_bytes
        self.sink_input = FakeInputSink()
        self.fail_start: str | None = None
        #: ``start`` waits for this to be set (a device that takes its time)
        self.start_gate: threading.Event | None = None
        self.on_keyframe_request: Callable[[], None] | None = None
        self.rungs: list[Rung | None] = []
        self.starts = 0
        self.stops = 0
        self.keyframe_requests = 0
        self.frames_made = 0
        self.idrs_made = 0
        self._sink: HubSink | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._force_idr = threading.Event()
        self._picture_changed = threading.Event()
        self._error: str | None = None
        self._alive = False
        self._lock = threading.Lock()

    # -- the source's contract -----------------------------------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        """Touch and keys, text, quality levels (unless turned off)."""
        return Capabilities(
            max_fingers=1 if self._control else 0,
            keys=self._keys if self._control else (),
            hard_keys=tuple(HardKey(k, k.replace("_", " ").title()) for k in self._keys[:4]) if self._control else (),
            text=self._text and self._control,
            quality=QualityCaps() if self._quality else None,
            keyframe_cost=self._keyframe_cost,
            rotation=True,
        )

    def start(self, sink: HubSink, rung: Rung | None, timeout_s: float) -> None:
        """Begin making pictures."""
        if self.fail_start:
            raise RuntimeError(self.fail_start)
        if self.start_gate is not None:
            self.start_gate.wait(timeout_s)
        with self._lock:
            self.starts += 1
            self.rungs.append(rung)
            self._sink, self._error = sink, None
            self._stop.clear()
            self._alive = True
            self._thread = threading.Thread(target=self._run, args=(sink,), name="fake-screen-source", daemon=True)
            self._thread.start()

    def stop(self, deadline_s: float) -> None:
        """End the thread."""
        with self._lock:
            thread, self._thread = self._thread, None
            if thread is not None:
                self.stops += 1
            self._stop.set()
            self._alive = False
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=deadline_s)

    @property
    def alive(self) -> bool:
        """Whether the thread is running."""
        return self._alive

    @property
    def error(self) -> str | None:
        """Why it died, when :meth:`die` made it."""
        return self._error

    def input_sink(self) -> InputSink | None:
        """The recording sink (``None`` when view-only)."""
        return self.sink_input if self._control else None

    def request_keyframe(self) -> bool:
        """The next picture is a key picture."""
        self.keyframe_requests += 1
        self._force_idr.set()
        if self.on_keyframe_request is not None:
            self.on_keyframe_request()
        return True

    def apply_rung(self, rung: Rung) -> bool:
        """Quality changes need a restart."""
        return False

    def native_size(self) -> tuple[int, int] | None:
        """The picture's own size."""
        return self.width, self.height

    def describe(self) -> dict[str, Any]:
        """What was made."""
        return {
            "fake": True,
            "frames": self.frames_made,
            "idrs": self.idrs_made,
            "starts": self.starts,
            "keyframe_requests": self.keyframe_requests,
        }

    # -- the knobs -----------------------------------------------------------------------------------------------------

    def change_picture(self, width: int, height: int, rotation: int | None = None) -> None:
        """A new size (and rotation): a new codec line and a key picture, from the next picture."""
        self.width, self.height, self.rotation = width, height, rotation
        self._picture_changed.set()

    def die(self, error: str | None = "the capture died") -> None:
        """The capture ends by itself."""
        self._error = error
        self._alive = False
        self._stop.set()
        if self._sink is not None:
            self._sink.ended(error)

    def pause(self, paused: bool = True) -> None:
        """Stop making pictures (a still screen is not silent for long, but a source can be)."""
        self._paused.set() if paused else self._paused.clear()

    def wait_frames(self, count: int, timeout: float = 10.0) -> bool:
        """Wait until ``count`` more pictures have been made."""
        target, deadline = self.frames_made + count, time.monotonic() + timeout
        while self.frames_made < target:
            if time.monotonic() > deadline:
                return False
            time.sleep(0.005)
        return True

    # -- the thread ----------------------------------------------------------------------------------------------------

    def _line(self, sps: bytes) -> CodecLine:
        return CodecLine(
            codec=codec_string(sps),
            width=self.width,
            height=self.height,
            fps=self.fps,
            rotation=self.rotation,
            input="stream" if self._control else None,
        )

    def _run(self, sink: HubSink) -> None:
        sps = make_sps(self.width, self.height)
        sink.config(self._line(sps))
        interval = 1.0 / max(self.fps, 1)
        origin_us = time.monotonic_ns() // 1000
        index = 0
        next_at = time.monotonic()
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.01)
                next_at = time.monotonic()
                continue
            if self._picture_changed.is_set():
                self._picture_changed.clear()
                sps = make_sps(self.width, self.height)
                sink.config(self._line(sps))
                index = 0
            idr = index % max(self.gop, 1) == 0 or self._force_idr.is_set()
            self._force_idr.clear()
            counter = f"{index:08x}".encode()
            if idr:
                unit = (
                    START_CODE
                    + sps
                    + START_CODE
                    + PPS
                    + START_CODE
                    + b"\x65\x88\x84"
                    + counter
                    + b"\xaa" * self._key_bytes
                )
                index = 0
                self.idrs_made += 1
            else:
                unit = START_CODE + b"\x41\x9a" + counter + b"\xaa" * self._frame_bytes
            sink.media(time.monotonic_ns() // 1000 - origin_us, unit, idr=idr, reference=True)
            self.frames_made += 1
            index += 1
            next_at += interval
            self._stop.wait(max(0.0, next_at - time.monotonic()))
