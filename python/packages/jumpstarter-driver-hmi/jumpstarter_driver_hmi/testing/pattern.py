"""Real test patterns, in each codec the interface has: 60 pictures (4 seconds at 15 a second) of a 240x400 moving test
chart, so that a browser or a decoder can play them, which the synthetic units of
:class:`~jumpstarter_driver_hmi.testing.fake.FakeSource` cannot be.

``pattern.h264`` is H.264 (baseline profile) and ``pattern.h265`` is HEVC (main profile), both Annex-B with an access
unit
delimiter in front of every picture and the parameter sets (SPS and PPS, plus VPS for HEVC) in front of every key
picture
(one every 15 pictures). They were made with::

    ffmpeg -f lavfi -i testsrc=size=240x400:rate=15:duration=4 -c:v libx264 -profile:v baseline -level 3.0 \
        -pix_fmt yuv420p -b:v 150k -x264-params keyint=15:min-keyint=15:scenecut=0:repeat-headers=1:aud=1:bframes=0 \
        -f h264 pattern.h264
    ffmpeg -f lavfi -i testsrc=size=240x400:rate=15:duration=4 -c:v libx265 -profile:v main -pix_fmt yuv420p -b:v 150k \
        -x265-params keyint=15:min-keyint=15:scenecut=0:repeat-headers=1:aud=1:bframes=0:info=0:open-gop=0 \
        -f hevc pattern.h265

with the H.264 encoder's own SEI (its version and settings) taken out. ``pattern.mjpeg`` is 15 JPEG pictures of the same
chart, one after the other (every picture is a key picture)::

    ffmpeg -f lavfi -i testsrc=size=240x400:rate=15:duration=1 -c:v mjpeg -q:v 31 -pix_fmt yuvj420p -f mjpeg
    pattern.mjpeg
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from ..hub.codecs import NAL_AUD, iter_nals

#: The pattern of each codec.
PATTERNS = {
    "h264": Path(__file__).with_name("pattern.h264"),
    "hevc": Path(__file__).with_name("pattern.h265"),
    "mjpeg": Path(__file__).with_name("pattern.mjpeg"),
}
PATTERN = PATTERNS["h264"]
#: What the pictures are: their size, how fast they were made and how many pictures a key picture covers (a JPEG
#: picture is its own key picture: the pattern's pictures all are).
PATTERN_SIZE = (240, 400)
PATTERN_FPS = 15
PATTERN_GOP = 15
#: HEVC's access unit delimiter has ``nal_unit_type`` 35 (two header bytes; the type is in bits 1 to 6 of the first).
_HEVC_AUD = 35
_START = b"\x00\x00\x01"


@cache
def pattern_units(codec: str = "h264") -> tuple[bytes, ...]:
    """The pattern's access units for ``codec`` (``h264``, ``hevc`` or ``mjpeg``), in order. Each H.264 and HEVC unit
    starts with its delimiter and the first is a key picture; each MJPEG unit is one complete JPEG.
    """
    data = PATTERNS[codec].read_bytes()
    if codec == "mjpeg":
        parts = data.split(b"\xff\xd9\xff\xd8")  # a JPEG's entropy-coded data never has FF D9 followed by FF D8
        return tuple(
            (b"" if i == 0 else b"\xff\xd8") + part + (b"" if i == len(parts) - 1 else b"\xff\xd9")
            for i, part in enumerate(parts)
        )
    if codec == "hevc":
        starts = []
        at = data.find(_START)
        while 0 <= at < len(data) - 3:
            if (data[at + 3] >> 1) & 0x3F == _HEVC_AUD:
                starts.append(at - 1 if at > 0 and data[at - 1] == 0 else at)
            at = data.find(_START, at + 3)
    else:
        starts = [start - 4 for kind, start, _ in iter_nals(data) if kind == NAL_AUD]  # the 4-byte start code before it
    return tuple(data[a:b] for a, b in zip(starts, [*starts[1:], len(data)], strict=True))
