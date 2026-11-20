"""A source of synthetic pictures, for tests, demos and ``MockHmi``: no device, no network.

It makes any of the interface's codecs, and declares either of two kinds of input (``mode``): a single-finger touch
screen with hard keys and typed text (the default), or a mouse and keyboard; either may also have rotary knobs
(``rotaries``).

The access units are shaped like an encoder's, with valid parameter sets for the picture's size, so everything
downstream (the hub, the framing, the MP4 muxer) works on them; the slices carry a counter instead of a picture, so they
do not decode to anything:

* **H.264**: an SPS, a PPS and an IDR slice at every key picture, a P slice between.
* **HEVC**: a VPS, an SPS (with its profile, tier and level and the picture's size) and a PPS in front of an IDR slice
  (``IDR_W_RADL``) at every key picture, a ``TRAIL_R`` slice between.
* **MJPEG**: every picture is a key picture and a minimal JPEG: SOI, a JFIF header, a frame header that has the size,
  filler in comment segments, EOI.

With ``pattern=True`` the pictures are the real ones of :mod:`~jumpstarter_driver_hmi.testing.pattern` in the codec
chosen, which a decoder can play.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from typing import Any

from ..common import (
    ALL_CODES,
    CODECS,
    DEFAULT_CODEC,
    Capabilities,
    CodecLine,
    HardKey,
    InputError,
    KeyboardCaps,
    PointerCaps,
    PointerEvent,
    QualityCaps,
    RotaryCaps,
)
from ..hub.codecs import codec_string_of, has_idr, hevc_codec_string
from ..hub.quality import Rung
from ..source import HmiSource, HubSink, InputSink
from .pattern import PATTERN_GOP, PATTERN_SIZE, pattern_units

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


# -- HEVC --------------------------------------------------------------------------------------------------------------

#: HEVC ``nal_unit_type`` values of what is made here.
HEVC_TRAIL_R = 1
HEVC_IDR_W_RADL = 19
#: The Main profile's levels (``general_level_idc`` = 30 x the level) and the most luma samples a picture may have at
#: each: a picture is given the lowest level it fits, as an encoder would.
_HEVC_LEVELS = ((93, 983_040), (123, 2_228_224), (153, 8_912_896), (186, 35_651_584))


def _hevc_nal(kind: int, rbsp: bytes) -> bytes:
    """An HEVC NAL unit (its two header bytes, no start code): layer 0, temporal id 0."""
    return bytes([kind << 1, 1]) + _escaped(rbsp)


def _profile_tier_level(bits: _Bits, width: int, height: int) -> None:
    """``profile_tier_level(1, 0)`` of the Main profile (Main and Main 10 compatible, progressive, frame only: what
    ``hev1.1.6.L93.B0`` says), at the lowest level the picture fits.
    """
    level = next((idc for idc, samples in _HEVC_LEVELS if width * height <= samples), _HEVC_LEVELS[-1][0])
    bits.u(2, 0)  # general_profile_space
    bits.u(1, 0)  # general_tier_flag: Main tier
    bits.u(5, 1)  # general_profile_idc: Main
    bits.u(32, 0x60000000)  # general_profile_compatibility_flag[1] and [2]: Main and Main 10
    bits.u(8, 0xB0)  # progressive_source, not interlaced, non_packed_constraint and frame_only_constraint flags...
    bits.u(40, 0)  # ... and the 43 reserved bits and general_inbld_flag/reserved bit that follow them
    bits.u(8, level)  # general_level_idc


def make_hevc_vps() -> bytes:
    """A VPS NAL unit (with its header, no start code) for the single-layer, single-sub-layer streams made here."""
    bits = _Bits()
    bits.u(4, 0)  # vps_video_parameter_set_id
    bits.u(1, 1)  # vps_base_layer_internal_flag
    bits.u(1, 1)  # vps_base_layer_available_flag
    bits.u(6, 0)  # vps_max_layers_minus1
    bits.u(3, 0)  # vps_max_sub_layers_minus1
    bits.u(1, 1)  # vps_temporal_id_nesting_flag
    bits.u(16, 0xFFFF)  # vps_reserved_0xffff_16bits
    _profile_tier_level(bits, 0, 0)
    bits.u(1, 1)  # vps_sub_layer_ordering_info_present_flag
    bits.exp_golomb(1)  # vps_max_dec_pic_buffering_minus1
    bits.exp_golomb(0)  # vps_max_num_reorder_pics
    bits.exp_golomb(0)  # vps_max_latency_increase_plus1
    bits.u(6, 0)  # vps_max_layer_id
    bits.exp_golomb(0)  # vps_num_layer_sets_minus1
    bits.u(1, 0)  # vps_timing_info_present_flag
    bits.u(1, 0)  # vps_extension_flag
    return _hevc_nal(32, bits.rbsp())


def make_hevc_sps(width: int, height: int) -> bytes:
    """An HEVC SPS NAL unit (with its header, no start code) for a picture of ``width`` x ``height``: Main profile,
    4:2:0, 8 bits, with the level the size needs. The coded size is a multiple of 8 (the smallest coding block) and the
    visible size is cropped from it, in 2-sample steps (an odd size is made even).
    """
    width, height = width - width % 2, height - height % 2
    coded_w, coded_h = -(-width // 8) * 8, -(-height // 8) * 8
    bits = _Bits()
    bits.u(4, 0)  # sps_video_parameter_set_id
    bits.u(3, 0)  # sps_max_sub_layers_minus1
    bits.u(1, 1)  # sps_temporal_id_nesting_flag
    _profile_tier_level(bits, coded_w, coded_h)
    bits.exp_golomb(0)  # sps_seq_parameter_set_id
    bits.exp_golomb(1)  # chroma_format_idc: 4:2:0
    bits.exp_golomb(coded_w)  # pic_width_in_luma_samples
    bits.exp_golomb(coded_h)  # pic_height_in_luma_samples
    if (coded_w, coded_h) != (width, height):
        bits.u(1, 1)  # conformance_window_flag (offsets are in chroma units: 2 luma samples)
        bits.exp_golomb(0)
        bits.exp_golomb((coded_w - width) // 2)
        bits.exp_golomb(0)
        bits.exp_golomb((coded_h - height) // 2)
    else:
        bits.u(1, 0)
    bits.exp_golomb(0)  # bit_depth_luma_minus8
    bits.exp_golomb(0)  # bit_depth_chroma_minus8
    bits.exp_golomb(4)  # log2_max_pic_order_cnt_lsb_minus4
    bits.u(1, 1)  # sps_sub_layer_ordering_info_present_flag
    bits.exp_golomb(1)  # sps_max_dec_pic_buffering_minus1
    bits.exp_golomb(0)  # sps_max_num_reorder_pics
    bits.exp_golomb(0)  # sps_max_latency_increase_plus1
    bits.exp_golomb(0)  # log2_min_luma_coding_block_size_minus3: 8
    bits.exp_golomb(3)  # log2_diff_max_min_luma_coding_block_size: 64
    bits.exp_golomb(0)  # log2_min_luma_transform_block_size_minus2: 4
    bits.exp_golomb(3)  # log2_diff_max_min_luma_transform_block_size: 32
    bits.exp_golomb(1)  # max_transform_hierarchy_depth_inter
    bits.exp_golomb(1)  # max_transform_hierarchy_depth_intra
    bits.u(1, 0)  # scaling_list_enabled_flag
    bits.u(1, 0)  # amp_enabled_flag
    bits.u(1, 0)  # sample_adaptive_offset_enabled_flag
    bits.u(1, 0)  # pcm_enabled_flag
    bits.exp_golomb(0)  # num_short_term_ref_pic_sets
    bits.u(1, 0)  # long_term_ref_pics_present_flag
    bits.u(1, 0)  # sps_temporal_mvp_enabled_flag
    bits.u(1, 0)  # strong_intra_smoothing_enabled_flag
    bits.u(1, 0)  # vui_parameters_present_flag
    bits.u(1, 0)  # sps_extension_present_flag
    return _hevc_nal(33, bits.rbsp())


def make_hevc_pps() -> bytes:
    """A PPS NAL unit (with its header, no start code): id 0, no tiles, no weighted prediction."""
    bits = _Bits()
    bits.exp_golomb(0)  # pps_pic_parameter_set_id
    bits.exp_golomb(0)  # pps_seq_parameter_set_id
    bits.u(1, 0)  # dependent_slice_segments_enabled_flag
    bits.u(1, 0)  # output_flag_present_flag
    bits.u(3, 0)  # num_extra_slice_header_bits
    bits.u(1, 0)  # sign_data_hiding_enabled_flag
    bits.u(1, 0)  # cabac_init_present_flag
    bits.exp_golomb(0)  # num_ref_idx_l0_default_active_minus1
    bits.exp_golomb(0)  # num_ref_idx_l1_default_active_minus1
    bits.exp_golomb(0)  # init_qp_minus26 (se(v): 0)
    bits.u(1, 0)  # constrained_intra_pred_flag
    bits.u(1, 0)  # transform_skip_enabled_flag
    bits.u(1, 0)  # cu_qp_delta_enabled_flag
    bits.exp_golomb(0)  # pps_cb_qp_offset
    bits.exp_golomb(0)  # pps_cr_qp_offset
    bits.u(1, 0)  # pps_slice_chroma_qp_offsets_present_flag
    bits.u(1, 0)  # weighted_pred_flag
    bits.u(1, 0)  # weighted_bipred_flag
    bits.u(1, 0)  # transquant_bypass_enabled_flag
    bits.u(1, 0)  # tiles_enabled_flag
    bits.u(1, 0)  # entropy_coding_sync_enabled_flag
    bits.u(1, 0)  # pps_loop_filter_across_slices_enabled_flag
    bits.u(1, 0)  # deblocking_filter_control_present_flag
    bits.u(1, 0)  # pps_scaling_list_data_present_flag
    bits.u(1, 0)  # lists_modification_present_flag
    bits.exp_golomb(0)  # log2_parallel_merge_level_minus2
    bits.u(1, 0)  # slice_segment_header_extension_present_flag
    bits.u(1, 0)  # pps_extension_present_flag
    return _hevc_nal(34, bits.rbsp())


def hevc_key_unit(sps: bytes, counter: bytes, size: int) -> bytes:
    """A key access unit: VPS, SPS, PPS and an ``IDR_W_RADL`` slice of ``size`` filler bytes."""
    slice_header = b"\xac"  # first slice of the picture, PPS 0, an I slice
    return (
        START_CODE
        + make_hevc_vps()
        + START_CODE
        + sps
        + START_CODE
        + make_hevc_pps()
        + START_CODE
        + bytes([HEVC_IDR_W_RADL << 1, 1])
        + slice_header
        + counter
        + b"\xaa" * size
    )


def hevc_delta_unit(counter: bytes, size: int) -> bytes:
    """A delta access unit: one ``TRAIL_R`` slice of ``size`` filler bytes."""
    return START_CODE + bytes([HEVC_TRAIL_R << 1, 1]) + b"\xd0" + counter + b"\xaa" * size


# -- MJPEG -------------------------------------------------------------------------------------------------------------

#: The most filler one comment segment carries (its length field is 16 bits and counts itself).
_MAX_COMMENT = 65_000


def make_jpeg(width: int, height: int, counter: bytes = b"", size: int = 0) -> bytes:
    """A minimal JPEG of ``width`` x ``height`` (its frame header says so): SOI, a JFIF header, the frame header,
    ``counter`` and ``size`` bytes of filler in comment segments, EOI. It starts with ``ff d8`` and ends with ``ff d9``,
    and a marker scanner walks it; it does not decode to a picture.
    """
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = (
        bytes([8])
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + bytes([3, 1, 0x22, 0, 2, 0x11, 1, 3, 0x11, 1])
    )
    filler = counter + b"\xaa" * size
    comments = b"".join(
        b"\xff\xfe" + (len(chunk) + 2).to_bytes(2, "big") + chunk
        for chunk in (filler[i : i + _MAX_COMMENT] for i in range(0, len(filler), _MAX_COMMENT))
    )
    return (
        b"\xff\xd8"
        + b"\xff\xe0"
        + (len(jfif) + 2).to_bytes(2, "big")
        + jfif
        + b"\xff\xc0"
        + (len(frame) + 2).to_bytes(2, "big")
        + frame
        + comments
        + b"\xff\xd9"
    )


class FakeInputSink(InputSink):
    """Records what it is given, in order. ``refuse`` makes every call raise ``InputError`` (a device that is busy).

    What is recorded, one tuple per call:

    * ``("touch", type, x, y, size)``: finger 0 of a touch screen (``type`` is ``down``, ``move`` or ``up``; the
      single-touch subset that :meth:`~jumpstarter_driver_hmi.source.InputSink.pointer` hands on);
    * ``("pointer", type, kind, x, y, id, buttons, dx, dy, size)``: any other pointer event (a mouse, a pen, another
      finger), with the state of the buttons after it;
    * ``("keyboard", code, down)``, ``("key", name, press)``, ``("rotary", id, delta)``, ``("text", text)`` and, when
      the input ends, ``("release",)``.
    """

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

    def pointer(self, event: PointerEvent, size: tuple[int, int]) -> None:
        """Record a pointer event (a single touch is recorded as a touch, as the default sink hands it on)."""
        if event.kind == "touch" and event.type in ("down", "move", "up") and event.id == 0:
            super().pointer(event, size)
            return
        self._record(
            ("pointer", event.type, event.kind, event.x, event.y, event.id, event.buttons, event.dx, event.dy, size)
        )

    def keyboard(self, code: str, down: bool) -> None:
        """Record a key going down or up."""
        self._record(("keyboard", code, down))

    def key(self, key: str, press: str) -> None:
        """Record a key."""
        self._record(("key", key, press))

    def text(self, text: str) -> None:
        """Record typed text."""
        self._record(("text", text))

    def rotary(self, rotary_id: str, delta: int) -> None:
        """Record a rotary turned."""
        self._record(("rotary", rotary_id, delta))

    def release(self) -> None:
        """Record the end of the input."""
        with self._lock:
            self.events.append(("release",))

    def snapshot(self) -> list[tuple]:
        """What has been recorded so far."""
        with self._lock:
            return list(self.events)

    def of(self, *kinds: str) -> list[tuple]:
        """What has been recorded so far of these kinds (``touch``, ``pointer``, ``keyboard``, ``key``, ``rotary``,
        ``text``, ``release``), in order.
        """
        return [event for event in self.snapshot() if event[0] in kinds]

    def clear(self) -> None:
        """Forget what has been recorded."""
        with self._lock:
            self.events.clear()


class FakeSource(HmiSource):
    """Makes a picture every ``1 / fps`` seconds on a thread of its own: a key picture every ``gop`` pictures, a delta
    picture between, in any of the ``codecs`` it declares (the stream's codec is the one ``start`` is given).

    ``mode`` says what input it declares: ``touch`` (the default: one finger, the hard ``keys``, typed ``text``) or
    ``mouse`` (an absolute mouse with buttons and a wheel, and ``relative`` motion if asked, and a keyboard of every key
    that types ``text``; no hard keys). ``control=False`` declares nothing: a view-only HMI. A test that needs another
    pointer or keyboard sets :attr:`pointer_caps` or :attr:`keyboard_caps` (before the input stream opens).

    ``rotaries`` are the rotary knobs it has (:class:`~jumpstarter_driver_hmi.common.RotaryCaps`; none by default,
    and none when view-only); the hard key that is a knob's push button is added to the keys it takes, if it is not one
    already. The input sink records a turn as ``("rotary", id, delta)``.

    The knobs a test turns: :meth:`change_picture` (a new size or rotation), :meth:`die` (the capture ends by itself),
    :meth:`pause` (silence), ``fail_start`` (``start`` raises), ``start_gate`` (``start`` waits),
    ``on_keyframe_request``, ``config_delay_s`` (the first codec line comes late), ``start_keyframe_cost`` (what a key
    picture costs, said only once ``start`` has run, as a source that picks its encoder then does) and the input sink's
    ``events``.
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
        keys: tuple[str, ...] = ("HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"),
        optional_keys: tuple[str, ...] = ("POWER", "LOCK"),
        text: bool = True,
        frame_bytes: int = 600,
        key_bytes: int = 4000,
        pattern: bool = False,
        codecs: Sequence[str] = CODECS,
        mode: str = "touch",
        relative: bool = False,
        rotaries: Sequence[RotaryCaps] = (),
        start_keyframe_cost: str | None = None,
    ) -> None:
        if mode not in ("touch", "mouse"):
            raise ValueError(f"mode is touch or mouse: {mode!r}")
        if relative and mode != "mouse":
            raise ValueError("relative motion is a mouse's: use mode='mouse'")
        if start_keyframe_cost is not None and start_keyframe_cost not in ("cheap", "restart", "none"):
            raise ValueError(f"start_keyframe_cost is cheap, restart or none: {start_keyframe_cost!r}")
        if any(not isinstance(rotary, RotaryCaps) for rotary in rotaries):
            raise ValueError("rotaries are RotaryCaps")
        # refuses codecs that are not the interface's, a list that is empty, and rotaries that share an id
        Capabilities(codecs=tuple(codecs), keys=tuple(r.push for r in rotaries if r.push), rotaries=tuple(rotaries))
        self.width, self.height, self.fps, self.gop = width, height, fps, gop
        self.rotation: int | None = None
        self.codecs = tuple(codecs)
        #: the codec of the stream that is running (or ran last)
        self.codec = self.codecs[0]
        self.mode, self.relative = mode, relative
        self._control = control
        self._quality = quality
        self._keyframe_cost = keyframe_cost
        #: what a key picture costs from the moment ``start`` has run (``None``: what was declared up front)
        self.start_keyframe_cost = start_keyframe_cost
        #: the rotary knobs it has (a view-only source has none)
        self.rotaries = tuple(rotaries)
        self._keys = tuple(keys)
        self._optional_keys = tuple(optional_keys)
        self._text = text
        self._frame_bytes, self._key_bytes = frame_bytes, key_bytes
        #: play the real test pattern (``testing/pattern.*``, 240x400) in a loop instead of synthetic units
        self._pattern = pattern
        if pattern:
            self.width, self.height = PATTERN_SIZE
        #: what the pointer and the keyboard declare (``None``: what ``mode`` says)
        self.pointer_caps: PointerCaps | None = None
        self.keyboard_caps: KeyboardCaps | None = None
        self.sink_input = FakeInputSink()
        self.fail_start: str | None = None
        #: ``start`` waits for this to be set (a device that takes its time)
        self.start_gate: threading.Event | None = None
        #: the first codec line comes this long after ``start`` has returned (a device that connects, then says what it
        #: shows)
        self.config_delay_s = 0.0
        self.on_keyframe_request: Callable[[], None] | None = None
        self.rungs: list[Rung | None] = []
        #: the codec of every start, in order
        self.started_codecs: list[str] = []
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
        """The input ``mode`` and ``rotaries`` declare (nothing when view-only), the codecs, and quality levels (unless
        turned off).
        """
        rotaries = self.rotaries if self._control else ()
        if not self._control:
            pointer, keyboard, keys, optional_keys, hard_keys = PointerCaps(), KeyboardCaps(), (), (), ()
        elif self.mode == "mouse":
            pointer = PointerCaps.mouse(relative=self.relative)
            keyboard = KeyboardCaps(codes=ALL_CODES, text=self._text, layout="us")
            keys, optional_keys, hard_keys = (), (), ()
        else:
            pointer, keyboard = PointerCaps.touch(), KeyboardCaps(text=self._text)
            keys, optional_keys = self._keys, self._optional_keys
            hard_keys = tuple(HardKey(k, k.replace("_", " ").title()) for k in self._keys[:4])
        if self._control:
            pointer = self.pointer_caps or pointer
            keyboard = self.keyboard_caps or keyboard
        # the push button of a knob is a hard key the HMI takes
        keys = tuple(dict.fromkeys((*keys, *(r.push for r in rotaries if r.push is not None))))
        return Capabilities(
            codecs=self.codecs,
            pointer=pointer,
            keyboard=keyboard,
            keys=keys,
            optional_keys=optional_keys,
            hard_keys=hard_keys,
            rotaries=rotaries,
            quality=QualityCaps() if self._quality else None,
            keyframe_cost=self._keyframe_cost,
            rotation=True,
        )

    def start(self, sink: HubSink, rung: Rung | None, timeout_s: float, *, codec: str = DEFAULT_CODEC) -> None:
        """Begin making pictures in ``codec`` (one of :attr:`codecs`)."""
        if self.fail_start:
            raise RuntimeError(self.fail_start)
        if codec not in self.codecs:
            raise RuntimeError(f"this source makes {', '.join(self.codecs)}, not {codec}")
        if self.start_gate is not None:
            self.start_gate.wait(timeout_s)
        with self._lock:
            self.starts += 1
            self.rungs.append(rung)
            self.started_codecs.append(codec)
            self.codec = codec
            if self.start_keyframe_cost is not None:  # the encoder is picked now: so is what a key picture costs
                self._keyframe_cost = self.start_keyframe_cost
            self._sink, self._error = sink, None
            self._stop.clear()
            self._alive = True
            self._thread = threading.Thread(target=self._run, args=(sink, codec), name="fake-hmi-source", daemon=True)
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
            "codec": self.codec,
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

    def _line(self, codec_name: str) -> CodecLine:
        return CodecLine(
            codec=codec_name,
            width=self.width,
            height=self.height,
            fps=self.fps,
            rotation=self.rotation,
            input="stream" if self._control else None,
        )

    def _run_pattern(self, sink: HubSink, codec: str) -> None:
        """Loop the real test pattern of ``codec``: every unit is a picture a decoder can show."""
        units = pattern_units(codec)
        name = codec_string_of(units[0], codec)
        assert name is not None  # the first unit is a key picture with its parameter sets
        sink.config(self._line(name))
        gop = 1 if codec == "mjpeg" else PATTERN_GOP  # a JPEG is its own key picture
        interval = 1.0 / max(self.fps, 1)
        origin_us = time.monotonic_ns() // 1000
        index = 0
        next_at = time.monotonic()
        while not self._stop.is_set():
            if self._paused.is_set():
                self._stop.wait(0.01)
                next_at = time.monotonic()
                continue
            if self._force_idr.is_set():
                self._force_idr.clear()
                index = -(-index // gop) * gop  # the next key picture of the pattern
            index %= len(units)
            unit = units[index]
            idr = has_idr(unit, codec)
            self.idrs_made += idr
            sink.media(time.monotonic_ns() // 1000 - origin_us, unit, idr=idr, reference=True)
            self.frames_made += 1
            index += 1
            next_at += interval
            self._stop.wait(max(0.0, next_at - time.monotonic()))

    def _parameters(self, codec: str) -> tuple[str, bytes]:
        """``(the codec string, the SPS)`` of the picture's size now."""
        if codec == "hevc":
            sps = make_hevc_sps(self.width, self.height)
            return hevc_codec_string(sps), sps
        if codec == "h264":
            sps = make_sps(self.width, self.height)
            return codec_string(sps), sps
        return "mjpeg", b""

    def _unit(self, codec: str, sps: bytes, index: int, idr: bool) -> bytes:
        """One synthetic access unit: the ``index``-th picture since the last key picture."""
        counter = f"{index:08x}".encode()
        if codec == "mjpeg":  # every picture is a key picture: all are as big as one
            return make_jpeg(self.width, self.height, counter, self._key_bytes)
        if codec == "hevc":
            return hevc_key_unit(sps, counter, self._key_bytes) if idr else hevc_delta_unit(counter, self._frame_bytes)
        if idr:
            return (
                START_CODE + sps + START_CODE + PPS + START_CODE + b"\x65\x88\x84" + counter + b"\xaa" * self._key_bytes
            )
        return START_CODE + b"\x41\x9a" + counter + b"\xaa" * self._frame_bytes

    def _run(self, sink: HubSink, codec: str) -> None:
        if self.config_delay_s and self._stop.wait(self.config_delay_s):
            return
        if self._pattern:
            self._run_pattern(sink, codec)
            return
        name, sps = self._parameters(codec)
        sink.config(self._line(name))
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
                name, sps = self._parameters(codec)
                sink.config(self._line(name))
                index = 0
            idr = codec == "mjpeg" or index % max(self.gop, 1) == 0 or self._force_idr.is_set()
            self._force_idr.clear()
            unit = self._unit(codec, sps, index, idr)
            if idr:
                index = 0
                self.idrs_made += 1
            sink.media(time.monotonic_ns() // 1000 - origin_us, unit, idr=idr, reference=True)
            self.frames_made += 1
            index += 1
            next_at += interval
            self._stop.wait(max(0.0, next_at - time.monotonic()))
