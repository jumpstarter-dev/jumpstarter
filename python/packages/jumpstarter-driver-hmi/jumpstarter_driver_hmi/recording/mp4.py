"""A fragmented MP4 (fMP4) muxer for one H.264 or HEVC video track, standard library only.

H.264 (``avc1`` sample entry, ``avcC``) and HEVC (``hev1``, ``hvcC``): the codec families the interface's video can be
that a video file can hold as they are. Motion JPEG is not one of them (it is not recorded); ``family`` names the one
the muxer writes (``h264`` by default) and everything below is said for H.264, with the HEVC differences in the last
section.

Input is what the stream carries: Annex-B access units with a timestamp in microseconds. Output is bytes to append to a
file (or a socket): ``ftyp`` + ``moov`` once, the first time a key picture with its parameter sets arrives, then
``moof`` + ``mdat`` fragments. Nothing is rewritten at the end, so a file that is cut off (a crash, a full disk, a
killed exporter) plays up to the last whole fragment; ``finish`` only flushes the last picture, and
:meth:`Fmp4Muxer.duration_patches` says where to write the total length into a seekable file (a player that reads the
header then knows how long the file is; one that never gets the patch finds the length by reading the fragments).

What it writes::

    ftyp(isom, iso5, avc1)
    moov( mvhd,
          trak( tkhd, mdia( mdhd, hdlr, minf( vmhd, dinf, stbl( stsd( avc1( avcC ) ), empty stts/stsc/stsz/stco ) ) ) ),
          mvex( mehd, trex ) )
    ( moof( mfhd, traf( tfhd, tfdt, trun ) ) mdat )*

* **Samples** are AVCC: each NAL unit behind a 4-byte big-endian length (the Annex-B start codes are removed). Access
  unit delimiters and filler data are dropped. **SPS and PPS are kept out of the samples when they are the ones in
  ``avcC``** (the sample entry is the one place the ``avc1`` brand allows them), and kept in the sample when they are
  different ones (see the size change below).
* **Timing** is on a 90 kHz track clock, computed from the absolute timestamps (no rounding drift): a sample lasts until
  the next one starts, so the muxer holds the newest sample back until the next arrives (``finish`` gives the last one
  the length of the one before it). A timestamp that goes backwards (the source restarted) is rebased to continue one
  nominal frame after the last picture; two pictures closer than a clock tick are kept one tick apart. There are no
  B-frames (the interface's encoders are set to make none), so decode time equals presentation time and there is no
  ``ctts``.
* **Key pictures** are the access units that carry an IDR slice (or the ``keyframe`` flag the caller passes); the others
  depend on earlier ones. A fragment ends after ``fragment_ms`` of video and before every key picture, so each key
  picture starts a fragment. The first access unit must be a key picture with an SPS and a PPS; the muxer skips what
  comes before.
* **A change of picture size** (the screen rotated; the encoder restarted with a new SPS) does **not** end the file and
  does **not** add a sample entry: a ``moov`` cannot get a second one once it is written. The picture that carries the
  new SPS keeps it, and its PPS, *in the sample* (an ``avc1`` stream with in-band parameter sets; ffmpeg and Chrome both
  follow it and show the later pictures at their own size, which was checked). The container's header still names the
  **first** size: ``Fmp4Muxer.width`` / ``height`` are the header's and ``Fmp4Muxer.size_changes`` counts the changes. A
  player that trusts the header over the stream scales or crops the later pictures. A caller that needs one size per
  file starts a new muxer (and file) at the change: with ``on_size_change="raise"``, :meth:`Fmp4Muxer.add` raises
  :class:`PictureSizeChanged` and takes nothing.

HEVC
----

``Fmp4Muxer(family="hevc")`` writes the same file with these differences. The sample entry is ``hev1`` with an ``hvcC``
(ISO/IEC 14496-15): the profile space, tier, profile, compatibility flags, constraint flags and level of the SPS's
``profile_tier_level``, its chroma format and bit depths, the number of temporal layers, 4-byte NAL lengths, and arrays
of the VPS, SPS and PPS (not "complete": ``hev1`` lets later parameter sets come in the samples, which is how a size
change is carried). The picture size is the SPS's, less its conformance window. A sample is the access unit's NAL units
behind 4-byte lengths, without the delimiter (and the filler data) and without the VPS, SPS and PPS when they are the
ones in ``hvcC``. The key pictures are the IRAP pictures (types 16 to 21), which the sync sample flag marks, and the
first access unit must be one that carries a VPS, an SPS and a PPS. A change of picture size is carried as for H.264:
the new VPS, SPS and PPS stay in the sample.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, replace
from typing import NamedTuple

from ..hub.codecs import (
    HEVC_AUD,
    HEVC_IRAP_TYPES,
    HEVC_PPS,
    HEVC_SPS,
    HEVC_VPS,
    NAL_AUD,
    NAL_IDR,
    NAL_PPS,
    NAL_SPS,
    hevc_codec_string,
    iter_nals,
)

#: The track's clock (the usual one for video).
TIMESCALE = 90_000
#: The movie's clock (``mvhd``), in milliseconds.
MOVIE_TIMESCALE = 1000
_FILLER = 12
_HEVC_FILLER = 38
#: The codec families the muxer writes.
FAMILIES = ("h264", "hevc")
_US = 1_000_000

# ``sample_flags`` (ISO/IEC 14496-12 8.8.3.1): depends-on-others bits and the non-sync bit.
SAMPLE_FLAGS_SYNC = 0x02000000  # sample_depends_on = 2 (depends on no other), is_non_sync = 0
SAMPLE_FLAGS_DELTA = 0x01010000  # sample_depends_on = 1, is_non_sync = 1

_HIGH_PROFILES = frozenset({100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135})
_AVCC_PROFILES = frozenset({100, 110, 122, 144})


class PictureSizeChanged(ValueError):
    """The next picture has another size than the file's header says (only with ``on_size_change="raise"``)."""

    def __init__(self, old: tuple[int, int], new: tuple[int, int]) -> None:
        """Remember both sizes."""
        super().__init__(f"the picture size changed from {old[0]}x{old[1]} to {new[0]}x{new[1]}")
        self.old = old
        self.new = new


# ---- SPS -------------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SpsInfo:
    """What the muxer needs from a sequence parameter set."""

    profile_idc: int
    constraint_flags: int
    level_idc: int
    chroma_format_idc: int
    bit_depth_luma: int
    bit_depth_chroma: int
    width: int
    height: int


class _Bits:
    """An RBSP bit reader (emulation prevention bytes removed first)."""

    def __init__(self, data: bytes) -> None:
        out = bytearray()
        zeros = 0
        for byte in data:
            if zeros >= 2 and byte == 3:
                zeros = 0
                continue
            zeros = zeros + 1 if byte == 0 else 0
            out.append(byte)
        self._data = bytes(out)
        self._pos = 0

    def bit(self) -> int:
        byte, offset = divmod(self._pos, 8)
        if byte >= len(self._data):
            raise ValueError("the SPS ends too soon")
        self._pos += 1
        return (self._data[byte] >> (7 - offset)) & 1

    def bits(self, count: int) -> int:
        value = 0
        for _ in range(count):
            value = (value << 1) | self.bit()
        return value

    def ue(self) -> int:
        zeros = 0
        while self.bit() == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("bad Exp-Golomb code in the SPS")
        return (1 << zeros) - 1 + self.bits(zeros)

    def se(self) -> int:
        value = self.ue()
        return (value + 1) // 2 if value % 2 else -(value // 2)


def _skip_scaling_list(bits: _Bits, size: int) -> None:
    last = nxt = 8
    for index in range(size):
        if nxt != 0:
            nxt = (last + bits.se() + 256) % 256
            if index == 0 and nxt == 0:
                break  # useDefaultScalingMatrixFlag: the rest is not coded
        last = last if nxt == 0 else nxt


def parse_sps(nal: bytes) -> SpsInfo:  # noqa: C901 (one pass over the syntax of ISO/IEC 14496-10 7.3.2.1.1)
    """Profile, level, chroma format, bit depth and the **cropped picture size** of an SPS NAL unit (with header byte).

    Raises:
        ValueError: If the unit is not an SPS or is cut short.
    """
    if len(nal) < 5 or nal[0] & 0x1F != NAL_SPS:
        raise ValueError("not an SPS NAL unit")
    profile, constraints, level = nal[1], nal[2], nal[3]
    bits = _Bits(nal[4:])
    bits.ue()  # seq_parameter_set_id
    chroma, depth_luma, depth_chroma, separate = 1, 8, 8, 0
    if profile in _HIGH_PROFILES:
        chroma = bits.ue()
        if chroma == 3:
            separate = bits.bit()
        depth_luma = 8 + bits.ue()
        depth_chroma = 8 + bits.ue()
        bits.bit()  # qpprime_y_zero_transform_bypass_flag
        if bits.bit():  # seq_scaling_matrix_present_flag
            for index in range(8 if chroma != 3 else 12):
                if bits.bit():
                    _skip_scaling_list(bits, 16 if index < 6 else 64)
    bits.ue()  # log2_max_frame_num_minus4
    poc_type = bits.ue()
    if poc_type == 0:
        bits.ue()
    elif poc_type == 1:
        bits.bit()
        bits.se()
        bits.se()
        for _ in range(bits.ue()):
            bits.se()
    bits.ue()  # max_num_ref_frames
    bits.bit()  # gaps_in_frame_num_value_allowed_flag
    width_mbs = bits.ue() + 1
    height_units = bits.ue() + 1
    frame_mbs_only = bits.bit()
    if not frame_mbs_only:
        bits.bit()  # mb_adaptive_frame_field_flag
    bits.bit()  # direct_8x8_inference_flag
    left = right = top = bottom = 0
    if bits.bit():  # frame_cropping_flag
        left, right, top, bottom = bits.ue(), bits.ue(), bits.ue(), bits.ue()
    chroma_array = 0 if separate else chroma
    crop_x = 1 if chroma_array == 0 else (2 if chroma_array in (1, 2) else 1)
    crop_y = (1 if chroma_array in (0, 2, 3) else 2) * (2 - frame_mbs_only)
    width = width_mbs * 16 - crop_x * (left + right)
    height = (2 - frame_mbs_only) * height_units * 16 - crop_y * (top + bottom)
    if width <= 0 or height <= 0:
        raise ValueError("the SPS describes an empty picture")
    return SpsInfo(profile, constraints, level, chroma, depth_luma, depth_chroma, width, height)


@dataclass(frozen=True)
class HevcSpsInfo:
    """What the muxer needs from an HEVC sequence parameter set (``hvcC`` carries all of it)."""

    #: ``general_profile_space`` (2 bits), ``general_tier_flag`` (1 bit) and ``general_profile_idc`` (5 bits)
    profile_space: int
    tier_flag: int
    profile_idc: int
    #: ``general_profile_compatibility_flag[0..31]`` as a 32-bit number (flag 0 is its top bit), and
    #: ``general_constraint_indicator_flags`` as a 48-bit number
    compatibility_flags: int
    constraint_flags: int
    level_idc: int
    chroma_format_idc: int
    bit_depth_luma: int
    bit_depth_chroma: int
    #: the **cropped** picture size (the conformance window is taken off)
    width: int
    height: int
    #: ``sps_max_sub_layers_minus1`` + 1, and ``sps_temporal_id_nesting_flag``
    sub_layers: int
    temporal_id_nesting: int


def _read_hevc_profile(nal: bytes) -> tuple[HevcSpsInfo, _Bits]:
    """The SPS's header and ``profile_tier_level`` (ISO/IEC 23008-2 7.3.2.2.1 and 7.3.3), with what is not read yet left
    at its default, and the bit reader positioned after them."""
    if len(nal) < 15 or (nal[0] >> 1) & 0x3F != HEVC_SPS:
        raise ValueError("not an HEVC SPS NAL unit")
    bits = _Bits(nal[2:])
    bits.bits(4)  # sps_video_parameter_set_id
    sub_layers = bits.bits(3) + 1
    nesting = bits.bit()
    space, tier, profile = bits.bits(2), bits.bit(), bits.bits(5)
    compatibility, constraints, level = bits.bits(32), bits.bits(48), bits.bits(8)
    present = [(bits.bit(), bits.bit()) for _ in range(sub_layers - 1)]  # (profile, level) of each lower sub-layer
    if sub_layers > 1:
        for _ in range(sub_layers - 1, 8):
            bits.bits(2)  # reserved_zero_2bits
    for profile_present, level_present in present:
        if profile_present:
            bits.bits(88)  # the sub-layer's profile: space, tier, idc, compatibility, constraint flags
        if level_present:
            bits.bits(8)
    info = HevcSpsInfo(space, tier, profile, compatibility, constraints, level, 1, 8, 8, 0, 0, sub_layers, nesting)
    return info, bits


def parse_hevc_sps(nal: bytes) -> HevcSpsInfo:
    """Profile, tier and level, chroma format, bit depth, sub-layers and the **cropped picture size** of an HEVC SPS NAL
    unit (with its two header bytes).

    Raises:
        ValueError: If the unit is not an SPS or is cut short.
    """
    info, bits = _read_hevc_profile(nal)
    bits.ue()  # sps_seq_parameter_set_id
    chroma = bits.ue()
    separate = bits.bit() if chroma == 3 else 0
    coded_width, coded_height = bits.ue(), bits.ue()
    left = right = top = bottom = 0
    if bits.bit():  # conformance_window_flag
        left, right, top, bottom = bits.ue(), bits.ue(), bits.ue(), bits.ue()
    depth_luma, depth_chroma = 8 + bits.ue(), 8 + bits.ue()
    chroma_array = 0 if separate else chroma
    unit_x = 2 if chroma_array in (1, 2) else 1  # SubWidthC and SubHeightC
    unit_y = 2 if chroma_array == 1 else 1
    width = coded_width - unit_x * (left + right)
    height = coded_height - unit_y * (top + bottom)
    if width <= 0 or height <= 0:
        raise ValueError("the SPS describes an empty picture")
    return replace(
        info,
        chroma_format_idc=chroma,
        bit_depth_luma=depth_luma,
        bit_depth_chroma=depth_chroma,
        width=width,
        height=height,
    )


# ---- boxes -----------------------------------------------------------------------------------------------------------


def box(kind: bytes, *parts: bytes) -> bytes:
    """An ISO BMFF box: ``u32 size`` + 4-byte type + payload."""
    payload = b"".join(parts)
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def full_box(kind: bytes, version: int, flags: int, *parts: bytes) -> bytes:
    """A box with ``u8 version`` and ``u24 flags`` in front of its payload."""
    return box(kind, struct.pack(">I", (version << 24) | flags), *parts)


_MATRIX = struct.pack(">9I", 0x00010000, 0, 0, 0, 0x00010000, 0, 0, 0, 0x40000000)


def _ftyp(brand: bytes = b"avc1") -> bytes:
    return box(b"ftyp", b"isom", struct.pack(">I", 512), b"isom", b"iso5", b"iso6", brand, b"mp41")


def _avcc(sps_list: list[bytes], pps_list: list[bytes], info: SpsInfo) -> bytes:
    body = bytes([1, info.profile_idc, info.constraint_flags, info.level_idc, 0xFC | 3, 0xE0 | len(sps_list)])
    body += b"".join(struct.pack(">H", len(s)) + s for s in sps_list)
    body += bytes([len(pps_list)]) + b"".join(struct.pack(">H", len(p)) + p for p in pps_list)
    if info.profile_idc in _AVCC_PROFILES:
        body += bytes(
            [0xFC | info.chroma_format_idc, 0xF8 | (info.bit_depth_luma - 8), 0xF8 | (info.bit_depth_chroma - 8), 0]
        )
    return box(b"avcC", body)


def _visual_entry(kind: bytes, width: int, height: int, config: bytes) -> bytes:
    """A video sample entry (``VisualSampleEntry``, ISO/IEC 14496-12 12.1.3) with its codec configuration box."""
    entry = bytes(6) + struct.pack(">H", 1)  # reserved, data_reference_index
    entry += bytes(16)  # pre_defined, reserved, pre_defined[3]
    entry += struct.pack(">HH", width, height)
    entry += struct.pack(">II", 0x00480000, 0x00480000)  # 72 dpi both ways
    entry += bytes(4) + struct.pack(">H", 1)  # reserved, frame_count
    entry += bytes(32)  # compressorname
    entry += struct.pack(">Hh", 0x0018, -1)  # depth, pre_defined
    return box(kind, entry, config)


def _avc1(info: SpsInfo, avcc: bytes) -> bytes:
    return _visual_entry(b"avc1", info.width, info.height, avcc)


def _hvcc(vps_list: list[bytes], sps_list: list[bytes], pps_list: list[bytes], info: HevcSpsInfo) -> bytes:
    """The ``hvcC`` box (ISO/IEC 14496-15 8.3.3.1): the decoder configuration of the track."""
    body = bytes([1, (info.profile_space << 6) | (info.tier_flag << 5) | info.profile_idc])
    body += struct.pack(">I", info.compatibility_flags) + info.constraint_flags.to_bytes(6, "big")
    body += bytes([info.level_idc])
    body += struct.pack(">H", 0xF000)  # reserved, min_spatial_segmentation_idc (0: not said)
    body += bytes([0xFC])  # reserved, parallelismType (0: not said)
    body += bytes([0xFC | info.chroma_format_idc, 0xF8 | (info.bit_depth_luma - 8), 0xF8 | (info.bit_depth_chroma - 8)])
    body += struct.pack(">H", 0)  # avgFrameRate (0: not said)
    # constantFrameRate (0: not said), numTemporalLayers, temporalIdNested and lengthSizeMinusOne (4-byte lengths)
    body += bytes([(info.sub_layers << 3) | (info.temporal_id_nesting << 2) | 3])
    arrays = [(kind, nals) for kind, nals in ((HEVC_VPS, vps_list), (HEVC_SPS, sps_list), (HEVC_PPS, pps_list)) if nals]
    body += bytes([len(arrays)])
    for kind, nals in arrays:  # array_completeness 0: ``hev1`` lets the stream carry others
        body += bytes([kind]) + struct.pack(">H", len(nals)) + b"".join(struct.pack(">H", len(n)) + n for n in nals)
    return box(b"hvcC", body)


def _hev1(info: HevcSpsInfo, hvcc: bytes) -> bytes:
    return _visual_entry(b"hev1", info.width, info.height, hvcc)


def _moov(info: SpsInfo | HevcSpsInfo, entry: bytes) -> tuple[bytes, int]:
    """The ``moov`` box for a track with this sample entry, and the offset inside it of the ``mehd`` payload's duration
    (for the patch)."""
    mvhd = full_box(
        b"mvhd",
        0,
        0,
        struct.pack(">IIII", 0, 0, MOVIE_TIMESCALE, 0),  # creation, modification, timescale, duration (unknown)
        struct.pack(">IH", 0x00010000, 0x0100),
        bytes(10),
        _MATRIX,
        bytes(24),
        struct.pack(">I", 2),
    )
    tkhd = full_box(
        b"tkhd",
        0,
        0x000003,  # enabled, in movie
        struct.pack(">IIII", 0, 0, 1, 0),
        struct.pack(">I", 0),  # creation, modification, track_ID, reserved, duration
        bytes(8),
        struct.pack(">hhhH", 0, 0, 0, 0),
        _MATRIX,
        struct.pack(">II", info.width << 16, info.height << 16),
    )
    mdhd = full_box(b"mdhd", 0, 0, struct.pack(">IIIIHH", 0, 0, TIMESCALE, 0, 0x55C4, 0))  # language "und"
    hdlr = full_box(b"hdlr", 0, 0, struct.pack(">I", 0), b"vide", bytes(12), b"VideoHandler\x00")
    dinf = box(b"dinf", full_box(b"dref", 0, 0, struct.pack(">I", 1), full_box(b"url ", 0, 1)))
    stsd = full_box(b"stsd", 0, 0, struct.pack(">I", 1), entry)
    empty = struct.pack(">I", 0)
    stbl = box(
        b"stbl",
        stsd,
        full_box(b"stts", 0, 0, empty),
        full_box(b"stsc", 0, 0, empty),
        full_box(b"stsz", 0, 0, struct.pack(">II", 0, 0)),
        full_box(b"stco", 0, 0, empty),
    )
    minf = box(b"minf", full_box(b"vmhd", 0, 1, bytes(8)), dinf, stbl)
    trak = box(b"trak", tkhd, box(b"mdia", mdhd, hdlr, minf))
    mehd = full_box(b"mehd", 1, 0, struct.pack(">Q", 0))  # fragment_duration, patched when the file is closed
    trex = full_box(b"trex", 0, 0, struct.pack(">IIIII", 1, 1, 0, 0, SAMPLE_FLAGS_DELTA))
    mvex = box(b"mvex", mehd, trex)
    moov = box(b"moov", mvhd, trak, mvex)
    return moov, len(moov) - len(
        mvex
    ) + 8 + 8 + 4  # moov offset of the mehd's u64 (mvex header, mehd header, version+flags)


def _fragment(sequence: int, base_time: int, samples: list[tuple[int, bytes, bool]]) -> bytes:
    """``moof`` + ``mdat`` for ``samples``: ``(duration in ticks, AVCC sample, is_key)``."""
    trun_flags = 0x000001 | 0x000100 | 0x000200 | 0x000400  # data offset, duration, size, flags per sample
    runs = b"".join(
        struct.pack(">III", duration, len(data), SAMPLE_FLAGS_SYNC if key else SAMPLE_FLAGS_DELTA)
        for duration, data, key in samples
    )

    def moof(data_offset: int) -> bytes:
        mfhd = full_box(b"mfhd", 0, 0, struct.pack(">I", sequence))
        tfhd = full_box(b"tfhd", 0, 0x020000, struct.pack(">I", 1))  # default-base-is-moof
        tfdt = full_box(b"tfdt", 1, 0, struct.pack(">Q", base_time))
        trun = full_box(b"trun", 0, trun_flags, struct.pack(">Ii", len(samples), data_offset), runs)
        return box(b"moof", mfhd, box(b"traf", tfhd, tfdt, trun))

    size = len(moof(0))
    payload = b"".join(data for _, data, _ in samples)
    if len(payload) + 8 > 0xFFFFFFFF:
        raise ValueError("a fragment of more than 4 GB")
    return moof(size + 8) + box(b"mdat", payload)


# ---- the muxer -------------------------------------------------------------------------------------------------------


def to_avcc(
    access_unit: bytes, *, drop: frozenset[int] = frozenset({NAL_AUD, _FILLER})
) -> tuple[bytes, bool, list[bytes], list[bytes]]:
    """Convert an Annex-B access unit: ``(AVCC sample, has an IDR slice, SPS units, PPS units)``.

    The sample keeps every NAL unit except the types in ``drop`` (default: delimiters and filler data), each behind a
    4-byte length; the parameter sets are returned as well (header byte included) and are also in the sample unless
    ``drop`` names 7 and 8.
    """
    out: list[bytes] = []
    sps: list[bytes] = []
    pps: list[bytes] = []
    key = False
    for kind, start, end in iter_nals(access_unit):
        unit = access_unit[start:end]
        if kind == NAL_SPS:
            sps.append(unit)
        elif kind == NAL_PPS:
            pps.append(unit)
        elif kind == NAL_IDR:
            key = True
        if kind not in drop:
            out.append(struct.pack(">I", len(unit)) + unit)
    return b"".join(out), key, sps, pps


def to_hvcc(
    access_unit: bytes, *, drop: frozenset[int] = frozenset({HEVC_AUD, _HEVC_FILLER})
) -> tuple[bytes, bool, list[bytes], list[bytes], list[bytes]]:
    """Convert an Annex-B HEVC access unit: ``(sample, has an IRAP picture, VPS units, SPS units, PPS units)``.

    The sample keeps every NAL unit except the types in ``drop`` (default: delimiters and filler data), each behind a
    4-byte length; the parameter sets are returned as well (two header bytes included) and are also in the sample unless
    ``drop`` names 32 to 34.
    """
    out: list[bytes] = []
    vps: list[bytes] = []
    sps: list[bytes] = []
    pps: list[bytes] = []
    key = False
    for kind, start, end in iter_nals(access_unit, "hevc"):
        unit = access_unit[start:end]
        if kind == HEVC_VPS:
            vps.append(unit)
        elif kind == HEVC_SPS:
            sps.append(unit)
        elif kind == HEVC_PPS:
            pps.append(unit)
        elif kind in HEVC_IRAP_TYPES:
            key = True
        if kind not in drop:
            out.append(struct.pack(">I", len(unit)) + unit)
    return b"".join(out), key, vps, sps, pps


class _Sets(NamedTuple):
    """The parameter sets of an access unit (each in the order it carried them): VPS (HEVC only), SPS and PPS."""

    vps: list[bytes]
    sps: list[bytes]
    pps: list[bytes]

    def unique(self) -> _Sets:
        """The same, each set once."""
        return _Sets(*(list(dict.fromkeys(group)) for group in self))


class Fmp4Muxer:
    """Annex-B access units in, fragmented MP4 bytes out.

    Not thread safe: one thread calls :meth:`add` and :meth:`finish`.
    """

    def __init__(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        fragment_ms: int = 500,
        default_frame_us: int = 33_333,
        on_size_change: str = "continue",
        family: str = "h264",
    ) -> None:
        """``width`` / ``height`` are used only if an SPS cannot be read; ``fragment_ms`` bounds one fragment's video.

        ``default_frame_us`` is the length of the last picture when it is also the only one. ``on_size_change`` is
        ``"continue"`` (the stream goes on; see the module docstring) or ``"raise"`` (:class:`PictureSizeChanged` from
        :meth:`add`, for a caller that starts a new file). ``family`` is the codec of the access units: ``h264`` or
        ``hevc``.

        Raises:
            ValueError: If ``on_size_change`` is not one of the two, or ``family`` is not one a file can hold (a Motion
                JPEG stream is not recorded).
        """
        if on_size_change not in ("continue", "raise"):
            raise ValueError("on_size_change is 'continue' or 'raise'")
        if family not in FAMILIES:
            raise ValueError(f"a file holds {' or '.join(FAMILIES)} video, not {family}")
        self._family = family
        self._fallback = (width, height) if width and height else None
        self._fragment_ticks = max(1, fragment_ms) * TIMESCALE // 1000
        self._default_ticks = max(1, default_frame_us * TIMESCALE // _US)
        self._on_size_change = on_size_change
        self._info: SpsInfo | HevcSpsInfo | None = None
        self._header = _Sets([], [], [])  # the parameter sets of the header's sample entry
        self._size: tuple[int, int] | None = None  # the picture size of the stream now
        self._mehd_at = 0  # file offset of the mehd's duration
        self._moov_at = 0
        self._written = 0  # bytes handed out so far
        self._sequence = 0
        self._base_us: int | None = None  # the timestamp that is tick 0 (moved when a timestamp goes backwards)
        self._last_us = 0
        self._dts = 0  # decode time of the pending sample
        self._pending: tuple[bytes, bool] | None = None
        self._fragment: list[tuple[int, bytes, bool]] = []
        self._fragment_start = 0  # decode time of the fragment's first sample
        self._fragment_ticks_now = 0
        self._total_ticks = 0  # what has been given a duration so far
        self._previous_duration = 0
        self.frames = 0
        self.skipped = 0
        self.size_changes = 0
        self.finished = False

    # ---- what the file says -------------------------------------------------------------------------------------

    @property
    def started(self) -> bool:
        """Whether the header has been produced (the first key picture with parameter sets came)."""
        return self._info is not None

    @property
    def width(self) -> int:
        """The width the file's header names (the first picture's), 0 before the header."""
        return self._info.width if self._info else 0

    @property
    def height(self) -> int:
        """The height the file's header names (the first picture's), 0 before the header."""
        return self._info.height if self._info else 0

    @property
    def family(self) -> str:
        """The codec of the access units this muxer takes: ``h264`` or ``hevc``."""
        return self._family

    @property
    def codec(self) -> str:
        """The RFC 6381 codec string of the header's SPS (``avc1.64001F``, ``hev1.1.6.L93.B0``), empty before the
        header."""
        if self._info is None:
            return ""
        if isinstance(self._info, HevcSpsInfo):
            return hevc_codec_string(self._header.sps[0])
        return f"avc1.{self._info.profile_idc:02X}{self._info.constraint_flags:02X}{self._info.level_idc:02X}"

    @property
    def duration_us(self) -> int:
        """Length of the video that has a duration so far (the pending sample is not counted until ``finish``)."""
        return self._total_ticks * _US // TIMESCALE

    @property
    def written(self) -> int:
        """Bytes handed out so far."""
        return self._written

    # ---- input --------------------------------------------------------------------------------------------------

    def add(self, ts_us: int, access_unit: bytes, keyframe: bool | None = None) -> bytes:
        """One access unit; returns the bytes to append to the file (possibly none: a sample waits for the next time).

        ``keyframe`` overrides the NAL-unit check for "can a decoder start here".

        Raises:
            PictureSizeChanged: With ``on_size_change="raise"``, if the picture size is not the header's. Nothing was
                added.
            ValueError: If an SPS cannot be read and no size was given.
        """
        if self.finished:
            raise ValueError("the muxer is finished")
        sample, idr, sets = self._split(access_unit)
        key = idr if keyframe is None else keyframe
        out = b""
        if self._info is None:
            if not (key and sets.sps and sets.pps and (sets.vps or self._family == "h264")):
                self.skipped += 1
                return b""  # a decoder cannot start here
            out = self._start(sets)
        if sets.sps:
            sample = self._parameter_sets(sample, access_unit, sets)
        if not sample:
            self.skipped += 1
            return out
        out += self._take(ts_us, sample, key)
        self.frames += 1
        return out

    def _split(self, access_unit: bytes, *, keep_sets: bool = True) -> tuple[bytes, bool, _Sets]:
        """``(the sample, whether it has a key picture, its parameter sets)``; ``keep_sets`` False leaves the sets out
        of the sample."""
        if self._family == "hevc":
            drop = frozenset({HEVC_AUD, _HEVC_FILLER} | (set() if keep_sets else {HEVC_VPS, HEVC_SPS, HEVC_PPS}))
            sample, key, vps, sps, pps = to_hvcc(access_unit, drop=drop)
            return sample, key, _Sets(vps, sps, pps)
        drop = frozenset({NAL_AUD, _FILLER} | (set() if keep_sets else {NAL_SPS, NAL_PPS}))
        sample, key, sps, pps = to_avcc(access_unit, drop=drop)
        return sample, key, _Sets([], sps, pps)

    def _read_sps(self, sps: bytes) -> SpsInfo | HevcSpsInfo:
        """What an SPS says (``ValueError`` if it cannot be read)."""
        return parse_hevc_sps(sps) if self._family == "hevc" else parse_sps(sps)

    def _start(self, sets: _Sets) -> bytes:
        first = sets.sps[0]
        try:
            info = self._read_sps(first)
        except ValueError:
            if self._fallback is None:
                raise
            if self._family == "hevc":
                info = replace(_read_hevc_profile(first)[0], width=self._fallback[0], height=self._fallback[1])
            else:
                info = SpsInfo(first[1], first[2], first[3], 1, 8, 8, *self._fallback)
        self._info = info
        self._size = (info.width, info.height)
        self._header = sets.unique()
        if isinstance(info, HevcSpsInfo):
            config = _hvcc(self._header.vps, self._header.sps, self._header.pps, info)
            entry, brand = _hev1(info, config), b"hev1"
        else:
            entry, brand = _avc1(info, _avcc(self._header.sps, self._header.pps, info)), b"avc1"
        moov, mehd = _moov(info, entry)
        head = _ftyp(brand)
        self._moov_at = len(head)
        self._mehd_at = len(head) + mehd
        return self._emit(head + moov)

    def _parameter_sets(self, sample: bytes, access_unit: bytes, sets: _Sets) -> bytes:
        """What this access unit's parameter sets mean. Returns the sample: without them if the header has them."""
        assert self._info is not None
        try:
            new = self._read_sps(sets.sps[0])
        except ValueError:
            new = None
        if new is not None and (new.width, new.height) != self._size:
            if self._on_size_change == "raise":
                raise PictureSizeChanged(self._size or (0, 0), (new.width, new.height))
            self.size_changes += 1
            self._size = (new.width, new.height)
            return sample  # the sample keeps its parameter sets: the decoder takes the new size from there
        unique, header = sets.unique(), self._header
        if unique.sps == header.sps and unique.pps in ([], header.pps) and unique.vps in ([], header.vps):
            return self._split(access_unit, keep_sets=False)[0]
        return sample  # other parameter sets of the same size: leave them where they are

    def _take(self, ts_us: int, sample: bytes, key: bool) -> bytes:
        """Give the pending sample its duration (now that the next one's time is known) and queue the new one."""
        if self._base_us is None:
            self._base_us = ts_us
        tick = (ts_us - self._base_us) * TIMESCALE // _US
        out = b""
        if self._pending is not None:
            if tick <= self._dts:  # a timestamp that did not advance, or went back: continue one nominal picture on
                if ts_us < self._last_us - _US // 100:
                    self._base_us = ts_us - (self._dts + self._default_ticks) * _US // TIMESCALE
                    tick = (ts_us - self._base_us) * TIMESCALE // _US
                tick = max(tick, self._dts + 1)
            out += self._complete(tick - self._dts)
            if key:
                out += self._flush_fragment()  # a key picture starts a fragment
        self._pending = (sample, key)
        self._dts = tick
        self._last_us = ts_us
        return out

    def _complete(self, duration: int) -> bytes:
        assert self._pending is not None
        sample, key = self._pending
        if not self._fragment:
            self._fragment_start = self._dts
            self._fragment_ticks_now = 0
        self._fragment.append((duration, sample, key))
        self._previous_duration = duration
        self._fragment_ticks_now += duration
        self._total_ticks += duration
        self._pending = None
        if self._fragment_ticks_now >= self._fragment_ticks:
            return self._flush_fragment()
        return b""

    def _flush_fragment(self) -> bytes:
        if not self._fragment:
            return b""
        self._sequence += 1
        data = _fragment(self._sequence, self._fragment_start, self._fragment)
        self._fragment = []
        return self._emit(data)

    def _emit(self, data: bytes) -> bytes:
        self._written += len(data)
        return data

    def finish(self) -> bytes:
        """Flush the last picture (it lasts as long as the one before it) and the open fragment. Call once."""
        if self.finished:
            return b""
        self.finished = True
        out = b""
        if self._pending is not None:
            out += self._complete(self._previous_duration or self._default_ticks)
        out += self._flush_fragment()
        return out

    def duration_patches(self) -> list[tuple[int, bytes]]:
        """``(file offset, bytes)`` to write into a seekable file once :meth:`finish` was written: the total length.

        The header is written before the length is known, so it says 0; these patches set ``mvhd.duration``
        (milliseconds) and ``mehd.fragment_duration`` (the same, in the movie clock), which is what a player that reads
        only the header (or streams the file) shows as the length. A file that never got them is still valid: ffmpeg and
        Chrome find the length by reading the fragments.
        """
        if not self.started:
            return []
        millis = self._total_ticks * MOVIE_TIMESCALE // TIMESCALE
        # moov and mvhd headers, version/flags, creation, modification, timescale
        mvhd_duration = self._moov_at + 8 + 8 + 16
        return [(mvhd_duration, struct.pack(">I", millis)), (self._mehd_at, struct.pack(">Q", millis))]


__all__ = [
    "FAMILIES",
    "MOVIE_TIMESCALE",
    "TIMESCALE",
    "Fmp4Muxer",
    "HevcSpsInfo",
    "PictureSizeChanged",
    "SpsInfo",
    "box",
    "full_box",
    "parse_hevc_sps",
    "parse_sps",
    "to_avcc",
    "to_hvcc",
]
