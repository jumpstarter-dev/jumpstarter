"""What the MP4 and recorder tests share: real H.264 and HEVC from ffmpeg and from the interface's test patterns, a
pure-Python box reader, synthetic parameter sets, ffprobe and ffmpeg runners.

Not a test module (its name does not end in ``_test``): the tests import from it."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..hub.codecs import HEVC_AUD, NAL_PPS, NAL_SPS, has_idr, iter_nals
from ..testing.pattern import PATTERN_FPS, pattern_units

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
HAVE_FFMPEG = bool(FFMPEG and FFPROBE)
NO_FFMPEG = "ffmpeg and ffprobe are not installed: the decode checks need them"
HAVE_X265 = bool(
    HAVE_FFMPEG
    and FFMPEG
    and "libx265"
    in subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True, check=False).stdout
)
NO_X265 = "ffmpeg has no libx265 encoder: the check that encodes HEVC needs it"

START = b"\x00\x00\x00\x01"

# Real parameter sets, as x264 wrote them.
SPS_320x240 = bytes.fromhex("6742c00cd90141fb0110000003001000000301e0f142a480")
SPS_240x320 = bytes.fromhex("6742c00cd903c29b0110000003001000000301e0f142a480")
PPS_BASELINE = bytes.fromhex("68cb83cb20")
SPS_HIGH_1920x1080 = bytes.fromhex("67640028acd940780227e5c044000003000400000300783c60c658")
PPS_HIGH = bytes.fromhex("68ebe3cb22c0")
SPS_MAIN_322x242 = bytes.fromhex("674d400ceca0a843c44460220000030002000003003c1e28532c")
SPS_MAIN_640x480 = bytes.fromhex("674d4016eca0501ed80880000003008000000f078b16cb")


def au(*nals: bytes) -> bytes:
    return b"".join(START + nal for nal in nals)


def stream(count=12, gop=4, fps=25, sps=SPS_320x240, pps=PPS_BASELINE, first_us=1_000_000_000, first=0):
    """``[(timestamp, access unit, is_key)]`` of fake pictures (real parameter sets, made-up slices)."""
    items = []
    for i in range(first, first + count):
        key = i % gop == 0
        slice_nal = (b"\x65" + b"\xaa" * (60 + i)) if key else (b"\x41" + b"\xbb" * (20 + i))
        items.append((first_us + (i - first) * 1_000_000 // fps, au(*([sps, pps] if key else []), slice_nal), key))
    return items


# The real parameter sets of the interface's HEVC test pattern (240x400, main profile, level 2: hev1.1.6.L60.90).
_PATTERN_SETS = {t: bytes(u[s:e]) for u in pattern_units("hevc")[:1] for t, s, e in iter_nals(u, "hevc")}
HEVC_VPS, HEVC_SPS_240x400, HEVC_PPS = _PATTERN_SETS[32], _PATTERN_SETS[33], _PATTERN_SETS[34]
HEVC_AUD_NAL = bytes([0x46, 0x01, 0x50])
#: The first byte of an HEVC NAL header, which is its type << 1 (the second says layer 0, temporal layer 0).
IDR_N_LP, TRAIL_R = 0x28, 0x02


def hevc_stream(
    count=12, gop=4, fps=25, sps=HEVC_SPS_240x400, vps=HEVC_VPS, pps=HEVC_PPS, first_us=1_000_000_000, first=0
):
    """``[(timestamp, access unit, is_key)]`` of fake HEVC pictures (the real VPS and PPS, an SPS of the test's choice,
    made-up slices): IDR pictures and TRAIL_R ones, each unit behind a delimiter, a key picture with its sets."""
    items = []
    for i in range(first, first + count):
        key = i % gop == 0
        slice_nal = (
            (bytes([IDR_N_LP, 0x01]) + b"\xaa" * (60 + i)) if key else (bytes([TRAIL_R, 0x01]) + b"\xbb" * (20 + i))
        )
        sets = [vps, sps, pps] if key else []
        items.append((first_us + (i - first) * 1_000_000 // fps, au(HEVC_AUD_NAL, *sets, slice_nal), key))
    return items


def pattern_stream(family="hevc", first_us=1_000_000_000):
    """``[(timestamp, access unit, is_key)]`` of the interface's real test pattern in ``family`` (60 pictures at 15 a
    second; a key picture every 15 with its parameter sets)."""
    return [
        (first_us + i * 1_000_000 // PATTERN_FPS, unit, has_idr(unit, family))
        for i, unit in enumerate(pattern_units(family))
    ]


# ---- real H.264 ------------------------------------------------------------------------------------------------------


def split_access_units(annexb: bytes) -> list[bytes]:
    """Split an Annex-B stream of one slice per picture into access units (each NAL behind a 4-byte start code)."""
    units: list[list[bytes]] = []
    current: list[bytes] = []
    seen_slice = False
    for kind, start, end in iter_nals(annexb):
        nal = annexb[start:end]
        first_slice_of_picture = kind in (1, 5) and bool(nal[1] & 0x80)  # first_mb_in_slice == 0
        if current and seen_slice and (kind in (6, 7, 8, 9) or first_slice_of_picture):
            units.append(current)
            current, seen_slice = [], False
        current.append(nal)
        seen_slice = seen_slice or kind in (1, 5)
    if current:
        units.append(current)
    return [b"".join(START + nal for nal in unit) for unit in units]


def encode(
    path: Path, *, size: str = "320x240", seconds: float = 3, fps: int = 15, profile: str = "baseline", gop: int = 15
) -> list[bytes]:
    """Encode ``testsrc`` with x264 into ``path`` (Annex-B) and return its access units. Needs ffmpeg."""
    assert FFMPEG
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}", "-t", str(seconds)]
    command += ["-pix_fmt", "yuv420p", "-c:v", "libx264", "-profile:v", profile, "-g", str(gop)]
    command += ["-bsf:v", "h264_mp4toannexb", "-f", "h264", str(path)]
    subprocess.run(command, check=True)
    return split_access_units(path.read_bytes())


def split_hevc_access_units(annexb: bytes) -> list[bytes]:
    """Split an Annex-B HEVC stream whose every picture starts with a delimiter into access units."""
    starts = []
    for kind, start, _ in iter_nals(annexb, "hevc"):
        if kind == HEVC_AUD:
            begin = start - 3
            starts.append(begin - 1 if begin > 0 and annexb[begin - 1] == 0 else begin)
    return [annexb[a:b] for a, b in zip(starts, [*starts[1:], len(annexb)], strict=True)]


def encode_hevc(path: Path, *, size: str = "320x240", seconds: float = 2, fps: int = 15, gop: int = 15) -> list[bytes]:
    """Encode ``testsrc`` with x265 (main profile, no B-frames, a delimiter before every picture, the parameter sets
    before every key picture) into ``path`` and return its access units. Needs ffmpeg with libx265."""
    assert FFMPEG
    command = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate={fps}", "-t", str(seconds)]
    command += ["-pix_fmt", "yuv420p", "-c:v", "libx265", "-profile:v", "main", "-x265-params"]
    command += [
        f"keyint={gop}:min-keyint={gop}:scenecut=0:repeat-headers=1:aud=1:bframes=0:info=0:open-gop=0:log-level=error"
    ]
    command += ["-f", "hevc", str(path)]
    subprocess.run(command, check=True)
    return split_hevc_access_units(path.read_bytes())


def probe(path: Path) -> dict:
    """``ffprobe`` on the file: the first video stream (with ``nb_read_frames`` counted by decoding) and the format."""
    assert FFPROBE
    run = subprocess.run(
        [FFPROBE, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(run.stdout)
    return {"stream": data["streams"][0], "format": data["format"]}


def frame_sizes(path: Path) -> list[tuple[int, int]]:
    """The size of every decoded picture, in order."""
    assert FFPROBE
    run = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "frame=width,height",
            "-of",
            "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return [tuple(int(v) for v in line.strip(",").split(",")) for line in run.stdout.split() if line]  # ty: ignore[invalid-return-type]


def decode(path: Path) -> subprocess.CompletedProcess:
    """Decode the whole file with ffmpeg and throw the pictures away."""
    assert FFMPEG
    return subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(path), "-f", "null", "-"], check=False, capture_output=True, text=True
    )


# ---- boxes -----------------------------------------------------------------------------------------------------------


@dataclass
class Box:
    kind: str
    offset: int
    size: int
    header: int  # bytes before the payload (8, or 16 with a 64-bit size)
    data: bytes
    children: list[Box] = field(default_factory=list)

    @property
    def payload(self) -> bytes:
        return self.data[self.offset + self.header : self.offset + self.size]

    def find(self, *path: str) -> Box:
        box = self
        for kind in path:
            matches = [c for c in box.children if c.kind == kind]
            assert matches, f"no {kind!r} in {box.kind!r}: {[c.kind for c in box.children]}"
            box = matches[0]
        return box

    def all(self, kind: str) -> list[Box]:
        return [c for c in self.children if c.kind == kind]


_CONTAINERS = {"moov", "trak", "mdia", "minf", "stbl", "mvex", "moof", "traf", "dinf"}
#: boxes that begin with a fixed part before their child boxes: (bytes to skip)
_WITH_PREFIX = {"stsd": 8, "dref": 8, "avc1": 78, "hev1": 78}


def parse_boxes(data: bytes, start: int = 0, end: int | None = None) -> list[Box]:
    """Parse the boxes in ``data[start:end]``; the sizes must tile it exactly (an error otherwise)."""
    end = len(data) if end is None else end
    boxes: list[Box] = []
    pos = start
    while pos < end:
        assert end - pos >= 8, f"{end - pos} bytes left, not a box header"
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
            header = 16
        assert size >= header and pos + size <= end, f"box {kind!r} of {size} bytes at {pos} does not fit in {end}"
        box = Box(kind.decode("latin-1"), pos, size, header, data)
        if box.kind in _CONTAINERS:
            box.children = parse_boxes(data, pos + header, pos + size)
        elif box.kind in _WITH_PREFIX:
            box.children = parse_boxes(data, pos + header + _WITH_PREFIX[box.kind], pos + size)
        boxes.append(box)
        pos += size
    assert pos == end
    return boxes


@dataclass
class Fragment:
    sequence: int
    base_time: int
    data_offset: int
    moof_offset: int
    mdat: Box
    samples: list[tuple[int, int, int]]  # (duration, size, flags)

    def sample_bytes(self) -> list[bytes]:
        out, pos = [], self.mdat.offset + self.mdat.header
        for _, size, _ in self.samples:
            out.append(self.mdat.data[pos : pos + size])
            pos += size
        assert pos == self.mdat.offset + self.mdat.size
        return out


def fragments(data: bytes) -> list[Fragment]:
    """Every ``moof`` + ``mdat`` of the file, with its ``trun`` samples, checking the data offset."""
    top = parse_boxes(data)
    out = []
    for index, box in enumerate(top):
        if box.kind != "moof":
            continue
        mdat = top[index + 1]
        assert mdat.kind == "mdat"
        mfhd = box.find("mfhd").payload
        sequence = struct.unpack_from(">I", mfhd, 4)[0]
        traf = box.find("traf")
        tfhd = traf.find("tfhd").payload
        assert struct.unpack_from(">I", tfhd, 0)[0] & 0xFFFFFF == 0x020000  # default-base-is-moof, nothing else
        assert struct.unpack_from(">I", tfhd, 4)[0] == 1  # track 1
        tfdt = traf.find("tfdt").payload
        assert tfdt[0] == 1
        base_time = struct.unpack_from(">Q", tfdt, 4)[0]
        trun = traf.find("trun").payload
        flags = struct.unpack_from(">I", trun, 0)[0] & 0xFFFFFF
        assert flags == 0x000701
        count, data_offset = struct.unpack_from(">Ii", trun, 4)
        samples = [struct.unpack_from(">III", trun, 12 + 12 * i) for i in range(count)]
        assert len(trun) == 12 + 12 * count
        assert box.offset + data_offset == mdat.offset + mdat.header, "data_offset must lead to the mdat payload"
        assert sum(s[1] for s in samples) == mdat.size - mdat.header
        out.append(Fragment(sequence, base_time, data_offset, box.offset, mdat, samples))
    return out


def avcc_nals(sample: bytes) -> list[bytes]:
    """The NAL units of an AVCC sample (4-byte lengths), checking the lengths add up exactly."""
    out, pos = [], 0
    while pos < len(sample):
        (length,) = struct.unpack_from(">I", sample, pos)
        assert length > 0 and pos + 4 + length <= len(sample), "a NAL length that does not fit the sample"
        out.append(sample[pos + 4 : pos + 4 + length])
        pos += 4 + length
    return out


def parameter_sets(access_unit: bytes) -> tuple[list[bytes], list[bytes]]:
    """``(SPS units, PPS units)`` of an Annex-B access unit."""
    sps = [access_unit[s:e] for t, s, e in iter_nals(access_unit) if t == NAL_SPS]
    pps = [access_unit[s:e] for t, s, e in iter_nals(access_unit) if t == NAL_PPS]
    return sps, pps


# ---- synthetic HEVC parameter sets -----------------------------------------------------------------------------------


class BitWriter:
    """Writes bits (MSB first) and Exp-Golomb codes, for building a parameter set of any shape."""

    def __init__(self):
        self.bits: list[int] = []

    def u(self, count, value):
        self.bits += [(value >> (count - 1 - i)) & 1 for i in range(count)]

    def ue(self, value):
        value += 1
        self.u(value.bit_length() - 1, 0)
        self.u(value.bit_length(), value)

    def se(self, value):
        self.ue(2 * value - 1 if value > 0 else -2 * value)

    def rbsp(self) -> bytes:
        bits = self.bits + [1]
        bits += [0] * (-len(bits) % 8)
        return bytes(int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8))


def escaped(rbsp: bytes) -> bytes:
    """``rbsp`` with the emulation-prevention bytes a NAL unit needs (``00 00 0x`` becomes ``00 00 03 0x``)."""
    out = bytearray()
    zeros = 0
    for byte in rbsp:
        if zeros >= 2 and byte <= 3:
            out.append(3)
            zeros = 0
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def build_hevc_sps(
    width,
    height,
    *,
    crop=(0, 0, 0, 0),
    chroma=1,
    separate=False,
    depth=8,
    sub_layers=1,
    profile=1,
    tier=0,
    space=0,
    compatibility=0x60000000,
    constraints=0x900000000000,
    level=93,
    nesting=1,
):
    """An HEVC SPS NAL unit (two header bytes, emulation prevention applied) of this shape."""
    bits = BitWriter()
    bits.u(4, 0), bits.u(3, sub_layers - 1), bits.u(1, nesting)
    bits.u(2, space), bits.u(1, tier), bits.u(5, profile), bits.u(32, compatibility), bits.u(48, constraints)
    bits.u(8, level)
    for index in range(sub_layers - 1):  # alternately a lower sub-layer with a profile and a level, with a level only
        bits.u(1, 1 - index % 2), bits.u(1, 1)
    if sub_layers > 1:
        for _ in range(sub_layers - 1, 8):
            bits.u(2, 0)
    for index in range(sub_layers - 1):
        if index % 2 == 0:
            bits.u(88, 0x5555555555555555555555)  # a sub-layer's profile (not read, only skipped)
        bits.u(8, 90 + index)
    bits.ue(0)  # sps_seq_parameter_set_id
    bits.ue(chroma)
    if chroma == 3:
        bits.u(1, int(separate))
    bits.ue(width), bits.ue(height)
    bits.u(1, int(any(crop)))
    if any(crop):
        for offset in crop:
            bits.ue(offset)
    bits.ue(depth - 8), bits.ue(depth - 8)
    bits.ue(4), bits.u(1, 1), bits.ue(1), bits.ue(0), bits.ue(0)  # what follows, which the muxer does not read
    return bytes([0x42, 0x01]) + escaped(bits.rbsp())
