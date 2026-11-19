"""The fragmented MP4 muxer: its boxes (read back in pure Python), its timing and (with ffmpeg) real decoders."""

from __future__ import annotations

import json
import os
import struct
import subprocess
from pathlib import Path

import pytest

from ..hub.codecs import hevc_codec_string, iter_nals
from ..testing.pattern import pattern_units
from .mp4 import Fmp4Muxer, PictureSizeChanged, parse_hevc_sps, parse_sps, to_avcc, to_hvcc
from .mp4_testing import (
    HAVE_FFMPEG,
    HAVE_X265,
    HEVC_AUD_NAL,
    HEVC_PPS,
    HEVC_VPS,
    IDR_N_LP,
    NO_FFMPEG,
    NO_X265,
    PPS_BASELINE,
    PPS_HIGH,
    START,
    TRAIL_R,
    BitWriter,
    HEVC_SPS_240x400,
    SPS_240x320,
    SPS_320x240,
    SPS_HIGH_1920x1080,
    SPS_MAIN_322x242,
    SPS_MAIN_640x480,
    au,
    avcc_nals,
    build_hevc_sps,
    decode,
    encode,
    encode_hevc,
    fragments,
    frame_sizes,
    hevc_stream,
    parse_boxes,
    pattern_stream,
    probe,
    stream,
)


def size_of(sps: bytes) -> tuple[int, int]:
    info = parse_sps(sps)
    return info.width, info.height


def mux(items, **kwargs) -> tuple[bytes, Fmp4Muxer]:
    """The whole file for ``items``, written the way the recording writes it (with the length patch)."""
    muxer = Fmp4Muxer(**kwargs)
    out = bytearray()
    for ts, unit, _ in items:
        out += muxer.add(ts, unit)
    out += muxer.finish()
    for offset, patch in muxer.duration_patches():
        out[offset : offset + len(patch)] = patch
    return bytes(out), muxer


# ---- SPS -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sps", "size", "profile"),
    [
        (SPS_320x240, (320, 240), 66),
        (SPS_240x320, (240, 320), 66),
        (SPS_HIGH_1920x1080, (1920, 1080), 100),  # 1088 coded rows, cropped
        (SPS_MAIN_322x242, (322, 242), 77),  # cropped on both sides
        (SPS_MAIN_640x480, (640, 480), 77),
    ],
)
def test_the_picture_size_comes_out_of_the_sps(sps, size, profile):
    info = parse_sps(sps)
    assert (info.width, info.height, info.profile_idc) == (*size, profile)
    assert (info.chroma_format_idc, info.bit_depth_luma, info.bit_depth_chroma) == (1, 8, 8)


def build_sps(  # noqa: C901 (writes the same syntax as ``parse_sps`` reads)
    *, profile=100, mbs=(40, 23), crop=(0, 0, 0, 0), scaling=False, frame_mbs_only=1, chroma=1
):
    bits = BitWriter()
    bits.u(8, profile), bits.u(8, 0), bits.u(8, 40), bits.ue(0)
    if profile in (100, 110, 122, 244):
        bits.ue(chroma)
        if chroma == 3:
            bits.u(1, 0)
        bits.ue(0), bits.ue(0), bits.u(1, 0)
        bits.u(1, int(scaling))
        if scaling:
            for index in range(8 if chroma != 3 else 12):
                if index == 0:  # a coded list: first delta 8 -> 16, then a few steps, one back to the same value
                    bits.u(1, 1)
                    for delta in [8] + [0] * 7 + [3, -3] + [0] * 6:
                        bits.se(delta)
                elif index == 1:  # a list that says "use the default" (the first scale is 0): nothing more is coded
                    bits.u(1, 1)
                    bits.se(-8)
                else:
                    bits.u(1, 0)
    bits.ue(0)  # log2_max_frame_num_minus4
    bits.ue(0), bits.ue(4)  # poc type 0, log2_max_poc_lsb_minus4
    bits.ue(2), bits.u(1, 0)  # max_num_ref_frames, gaps
    bits.ue(mbs[0] - 1), bits.ue(mbs[1] - 1)
    bits.u(1, frame_mbs_only)
    if not frame_mbs_only:
        bits.u(1, 0)
    bits.u(1, 1)  # direct_8x8_inference
    if any(crop):
        bits.u(1, 1)
        for value in crop:
            bits.ue(value)
    else:
        bits.u(1, 0)
    bits.u(1, 0)  # vui_parameters_present_flag
    return b"\x67" + bytes([profile, 0, 40]) + bits.rbsp()[3:]


def test_an_sps_with_scaling_lists_and_cropping_is_read_to_the_end():
    sps = build_sps(profile=100, mbs=(40, 23), crop=(0, 0, 0, 4), scaling=True)
    assert size_of(sps) == (640, 360)  # 23 macroblock rows are 368 pixels, 8 of them cropped (2 per unit, 4 units)


def test_interlaced_and_other_chroma_formats_are_sized_right():
    # frame_mbs_only = 0: a map unit is two macroblock rows; the crop unit is twice as tall
    assert size_of(build_sps(profile=77, mbs=(40, 8), frame_mbs_only=0)) == (640, 256)
    assert size_of(build_sps(profile=77, mbs=(40, 8), frame_mbs_only=0, crop=(0, 0, 0, 2))) == (640, 248)
    # 4:4:4: the crop unit is one pixel
    assert size_of(build_sps(profile=244, chroma=3, mbs=(10, 10), crop=(1, 2, 3, 4))) == (157, 153)
    # 4:2:2: two pixels wide, one pixel tall
    assert size_of(build_sps(profile=122, chroma=2, mbs=(10, 10), crop=(1, 2, 3, 4))) == (154, 153)


def test_a_unit_that_is_not_an_sps_is_refused():
    with pytest.raises(ValueError, match="not an SPS"):
        parse_sps(PPS_BASELINE)
    with pytest.raises(ValueError, match="ends too soon"):
        parse_sps(SPS_320x240[:6])


# ---- the boxes -------------------------------------------------------------------------------------------------------


def test_the_file_is_a_header_and_whole_fragments_that_tile_it():
    data, _ = mux(stream(count=13, gop=4))
    top = parse_boxes(data)  # asserts the sizes add up to the file exactly
    kinds = [b.kind for b in top]
    assert kinds[:2] == ["ftyp", "moov"]
    assert kinds[2:] == ["moof", "mdat"] * ((len(kinds) - 2) // 2)
    assert sum(b.size for b in top) == len(data)
    ftyp = top[0].payload
    assert ftyp[:4] == b"isom"
    assert b"avc1" in ftyp[8:] and b"iso5" in ftyp[8:]


def test_the_moov_describes_one_avc_video_track():
    data, muxer = mux(stream())
    moov = parse_boxes(data)[1]
    assert [c.kind for c in moov.children] == ["mvhd", "trak", "mvex"]
    mvhd = moov.find("mvhd").payload
    assert struct.unpack_from(">I", mvhd, 12)[0] == 1000  # movie timescale
    trak = moov.find("trak")
    tkhd = trak.find("tkhd").payload
    assert struct.unpack_from(">I", tkhd, 0)[0] & 0xFFFFFF == 3  # enabled, in the movie
    assert struct.unpack_from(">I", tkhd, 12)[0] == 1  # track 1
    assert struct.unpack_from(">II", tkhd, len(tkhd) - 8) == (320 << 16, 240 << 16)
    mdhd = trak.find("mdia", "mdhd").payload
    assert struct.unpack_from(">I", mdhd, 12)[0] == 90_000
    hdlr = trak.find("mdia", "hdlr").payload
    assert hdlr[8:12] == b"vide"
    stbl = trak.find("mdia", "minf", "stbl")
    assert [c.kind for c in stbl.children] == ["stsd", "stts", "stsc", "stsz", "stco"]
    for empty in ("stts", "stsc", "stco"):
        assert struct.unpack_from(">I", stbl.find(empty).payload, 4)[0] == 0  # the samples live in the fragments
    assert struct.unpack_from(">II", stbl.find("stsz").payload, 4) == (0, 0)
    stsd = stbl.find("stsd")
    assert struct.unpack_from(">I", stsd.payload, 4)[0] == 1
    avc1 = stsd.find("avc1")
    assert avc1.payload[24:28] == struct.pack(">HH", 320, 240)
    assert [c.kind for c in avc1.children] == ["avcC"]
    assert [c.kind for c in moov.find("mvex").children] == ["mehd", "trex"]
    assert (muxer.width, muxer.height, muxer.codec) == (320, 240, "avc1.42C00C")


def test_avcc_carries_the_first_sps_and_pps_whole():
    data, _ = mux(stream())
    avcc = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "avc1", "avcC").payload
    assert avcc[0] == 1  # configurationVersion
    assert avcc[1:4] == SPS_320x240[1:4]  # profile, compatibility, level: the SPS's own bytes
    assert avcc[4] == 0xFC | 3  # NAL lengths are 4 bytes
    assert avcc[5] == 0xE0 | 1
    (sps_len,) = struct.unpack_from(">H", avcc, 6)
    assert avcc[8 : 8 + sps_len] == SPS_320x240
    pos = 8 + sps_len
    assert avcc[pos] == 1
    (pps_len,) = struct.unpack_from(">H", avcc, pos + 1)
    assert avcc[pos + 3 : pos + 3 + pps_len] == PPS_BASELINE
    assert len(avcc) == pos + 3 + pps_len  # a baseline profile has no extension fields


def test_a_high_profile_avcc_has_its_chroma_format_and_bit_depths():
    items = stream(sps=SPS_HIGH_1920x1080, pps=PPS_HIGH)
    data, muxer = mux(items)
    avcc = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "avc1", "avcC").payload
    assert avcc[-4:] == bytes([0xFC | 1, 0xF8, 0xF8, 0])
    assert (muxer.width, muxer.height) == (1920, 1080)
    assert muxer.codec == "avc1.640028"


def test_every_fragment_points_at_its_own_samples_and_tells_which_are_key_pictures():
    items = stream(count=13, gop=4)
    data, _ = mux(items)
    frags = fragments(data)  # checks the trun flags, the data offsets and that the sample sizes fill each mdat
    assert [f.sequence for f in frags] == list(range(1, len(frags) + 1))
    samples = [pair for frag in frags for pair in zip(frag.samples, frag.sample_bytes(), strict=True)]
    assert len(samples) == 13
    for (duration, size, flags), body, (_, _, key) in zip(
        [s for s, _ in samples], [b for _, b in samples], items, strict=True
    ):
        assert len(body) == size
        assert duration == 3600  # 40 ms at 90 kHz
        assert flags == (0x02000000 if key else 0x01010000)
    # a fragment starts where the previous one ended
    clock = 0
    for frag in frags:
        assert frag.base_time == clock
        clock += sum(d for d, _, _ in frag.samples)


def test_samples_are_length_prefixed_and_the_parameter_sets_stay_in_avcc_only():
    items = stream(count=9, gop=4)
    data, _ = mux(items)
    bodies = [b for frag in fragments(data) for b in frag.sample_bytes()]
    for (_, unit, key), body in zip(items, bodies, strict=True):
        nals = avcc_nals(body)
        assert [n[0] & 0x1F for n in nals] == ([5] if key else [1])  # SPS and PPS are in the avcC, not in the sample
        assert nals[0] == unit[unit.rindex(START) + 4 :]


def test_access_unit_delimiters_and_filler_data_are_dropped_and_sei_is_kept():
    unit = au(
        b"\x09\xf0", SPS_320x240, PPS_BASELINE, b"\x06\x05\x04abcd\x80", b"\x65" + b"\xaa" * 30, b"\x0c" + b"\xff" * 4
    )
    sample, key, sps, pps = to_avcc(unit)
    assert key and sps == [SPS_320x240] and pps == [PPS_BASELINE]
    assert [n[0] & 0x1F for n in avcc_nals(sample)] == [
        7,
        8,
        6,
        5,
    ]  # as is: only the muxer decides what the sample entry replaces


def test_the_timestamps_make_the_durations_and_the_last_picture_lasts_as_long_as_the_one_before():
    ts = [10_000_000, 10_040_000, 10_080_000, 10_200_000, 10_220_000]  # a pause in the middle
    items = [(t, unit, key) for t, (_, unit, key) in zip(ts, stream(count=5, gop=5), strict=True)]
    data, muxer = mux(items)
    durations = [d for f in fragments(data) for d, _, _ in f.samples]
    assert durations == [3600, 3600, 10_800, 1800, 1800]  # the last one repeats the one before
    assert muxer.duration_us == sum(durations) * 1_000_000 // 90_000


def test_a_clock_that_is_not_a_multiple_of_a_tick_does_not_drift():
    items = stream(count=301, gop=30, fps=30)  # steps of 33_333 or 33_334 us: 2999.97 to 3000.06 ticks
    data, _ = mux(items)
    durations = [d for f in fragments(data) for d, _, _ in f.samples]
    # exact, from the absolute timestamps
    assert sum(durations[:-1]) == (items[-1][0] - items[0][0]) * 90_000 // 1_000_000
    assert set(durations) <= {2999, 3000, 3001}  # each within a tick of the step, no error builds up


def test_a_lone_picture_gets_the_default_length():
    data, muxer = mux(stream(count=1), default_frame_us=40_000)
    ((duration, _, _),) = [s for f in fragments(data) for s in f.samples]
    assert duration == 3600 and muxer.frames == 1


def test_a_timestamp_that_does_not_advance_is_kept_a_tick_apart():
    items = stream(count=4, gop=4)
    items = [
        (items[0][0], items[0][1], True),
        (items[0][0], items[1][1], False),
        (items[0][0], items[2][1], False),
        items[3],
    ]
    data, _ = mux(items)
    durations = [d for f in fragments(data) for d, _, _ in f.samples]
    assert durations[:2] == [1, 1] and all(d >= 1 for d in durations)


def test_a_timestamp_that_goes_back_continues_after_the_last_picture():
    first = stream(count=4, gop=4, first_us=9_000_000_000)
    second = stream(count=4, gop=4, first_us=5_000)  # the source restarted: its clock starts again
    data, _ = mux(first + second, default_frame_us=40_000)
    durations = [d for f in fragments(data) for d, _, _ in f.samples]
    assert len(durations) == 8
    assert durations[3] in (3599, 3600, 3601)  # one nominal picture, not minus eleven thousand seconds
    assert max(durations) < 4000


def test_nothing_is_written_until_a_key_picture_with_parameter_sets_arrives():
    muxer = Fmp4Muxer()
    delta = au(b"\x41" + b"\xbb" * 20)
    key_without_sets = au(b"\x65" + b"\xaa" * 20)
    key = au(SPS_320x240, PPS_BASELINE, b"\x65" + b"\xaa" * 20)
    assert muxer.add(0, delta) == b"" and muxer.add(40_000, key_without_sets) == b""
    assert not muxer.started and muxer.frames == 0 and muxer.skipped == 2 and muxer.duration_patches() == []
    first = muxer.add(80_000, key)
    assert first.startswith(b"\x00\x00\x00\x24ftyp") and muxer.started and muxer.frames == 1
    data = first + muxer.finish()
    assert [b.kind for b in parse_boxes(data)] == ["ftyp", "moov", "moof", "mdat"]


def test_later_key_pictures_with_the_same_parameter_sets_do_not_repeat_them():
    items = stream(count=9, gop=4)
    assert items[4][1].count(SPS_320x240) == 1  # the input does repeat them in front of every IDR
    data, _ = mux(items)
    assert all(not any(n[0] & 0x1F in (7, 8) for n in avcc_nals(b)) for f in fragments(data) for b in f.sample_bytes())


def test_a_fragment_ends_at_fragment_ms_and_before_every_key_picture():
    items = stream(count=17, gop=8, fps=25)
    data, _ = mux(items, fragment_ms=100)  # 9000 ticks: three 40 ms pictures make one
    counts = [len(f.samples) for f in fragments(data)]
    assert counts == [3, 3, 2, 3, 3, 2, 1]
    for frag in fragments(data):
        flags = [flag for _, _, flag in frag.samples]
        assert 0x02000000 not in flags[1:]  # a key picture only ever begins a fragment
    big, _ = mux(items, fragment_ms=60_000)  # a long limit: one fragment per key picture
    assert [len(f.samples) for f in fragments(big)] == [8, 8, 1]


def test_the_length_patch_fills_in_the_durations_and_leaves_a_valid_file():
    items = stream(count=26, gop=5, fps=25)
    data, muxer = mux(items)
    moov = parse_boxes(data)[1]
    millis = muxer.duration_us // 1000
    assert millis == 26 * 40
    assert struct.unpack_from(">I", moov.find("mvhd").payload, 16)[0] == millis
    assert struct.unpack_from(">Q", moov.find("mvex", "mehd").payload, 4)[0] == millis
    unpatched = bytearray(data)
    for offset, patch in muxer.duration_patches():
        unpatched[offset : offset + len(patch)] = bytes(len(patch))  # what a file that was never closed holds
    assert [b.kind for b in parse_boxes(bytes(unpatched))] == [b.kind for b in parse_boxes(data)]
    assert struct.unpack_from(">I", parse_boxes(bytes(unpatched))[1].find("mvhd").payload, 16)[0] == 0


def test_a_size_change_goes_on_in_the_same_file_with_the_new_parameter_sets_in_the_samples():
    before = stream(count=8, gop=4)
    after = stream(count=8, gop=4, sps=SPS_240x320, first_us=before[-1][0] + 40_000)
    data, muxer = mux(before + after)
    assert (muxer.width, muxer.height, muxer.size_changes) == (320, 240, 1)  # the header keeps the first picture's size
    avc1 = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "avc1")
    assert avc1.payload[24:28] == struct.pack(">HH", 320, 240)
    bodies = [avcc_nals(b) for f in fragments(data) for b in f.sample_bytes()]
    assert len(bodies) == 16
    assert not any(n[0] & 0x1F in (7, 8) for body in bodies[:8] for n in body)
    for index in (
        8,
        12,
    ):  # every key picture of the new size carries its SPS and PPS, for a decoder that comes in at that point
        assert [n[0] & 0x1F for n in bodies[index]] == [7, 8, 5]
        assert bodies[index][0] == SPS_240x320
    assert [n[0] & 0x1F for n in bodies[9]] == [1]


def test_going_back_to_the_first_size_puts_its_parameter_sets_back_in_the_stream():
    one = stream(count=4, gop=4)
    two = stream(count=4, gop=4, sps=SPS_240x320, first_us=one[-1][0] + 40_000)
    three = stream(count=8, gop=4, first_us=two[-1][0] + 40_000)
    data, muxer = mux(one + two + three)
    assert muxer.size_changes == 2
    bodies = [avcc_nals(b) for f in fragments(data) for b in f.sample_bytes()]
    # in the sample: the decoder holds the other size's set until this one replaces it
    assert bodies[8][0] == SPS_320x240
    assert not any(n[0] & 0x1F == 7 for body in bodies[12:] for n in body)  # and then it is the one that is in effect


def test_a_caller_that_wants_one_size_per_file_gets_the_change_as_an_exception():
    before = stream(count=4, gop=4)
    after = stream(count=4, gop=4, sps=SPS_240x320, first_us=before[-1][0] + 40_000)
    muxer = Fmp4Muxer(on_size_change="raise")
    for ts, unit, _ in before:
        muxer.add(ts, unit)
    with pytest.raises(PictureSizeChanged) as raised:
        muxer.add(*after[0][:2])
    assert (raised.value.old, raised.value.new) == ((320, 240), (240, 320))
    assert muxer.frames == 4  # nothing of the new picture was taken
    with pytest.raises(ValueError, match="on_size_change"):
        Fmp4Muxer(on_size_change="sometimes")


def test_an_sps_that_cannot_be_read_uses_the_size_it_was_given():
    odd = bytes([0x67, 0x42, 0xC0, 0x0C, 0xFF])
    items = [(0, au(odd, PPS_BASELINE, b"\x65" + b"\xaa" * 20), True)]
    data, muxer = mux(items, width=640, height=360)
    avc1 = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "avc1")
    assert avc1.payload[24:28] == struct.pack(">HH", 640, 360) and (muxer.width, muxer.height) == (640, 360)
    with pytest.raises(ValueError, match="ends too soon"):
        Fmp4Muxer().add(*items[0][:2])


def test_a_finished_muxer_takes_nothing_more_and_finish_is_idempotent():
    muxer = Fmp4Muxer()
    for ts, unit, _ in stream(count=3):
        muxer.add(ts, unit)
    assert muxer.finish() and muxer.finish() == b"" and muxer.finished
    with pytest.raises(ValueError, match="finished"):
        muxer.add(0, b"")


# ---- real decoders ---------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if not HAVE_FFMPEG:
        pytest.skip(NO_FFMPEG)  # ty: ignore[too-many-positional-arguments]
    root = tmp_path_factory.mktemp("clips")
    return {
        "landscape": encode(root / "land.h264", size="320x240", seconds=3),
        "portrait": encode(root / "port.h264", size="240x320", seconds=2),
        "high": encode(root / "high.h264", size="1920x1080", seconds=0.4, profile="high", gop=3),
        "cropped": encode(root / "crop.h264", size="322x242", seconds=0.5, profile="main"),
    }


def mux_clips(path: Path, *clips: list[bytes], fps: int = 15, patch: bool = True, **kwargs) -> Fmp4Muxer:
    muxer = Fmp4Muxer(**kwargs)
    ts = 7_000_000_000
    with path.open("wb") as out:
        for clip in clips:
            for unit in clip:
                out.write(muxer.add(ts, unit))
                ts += 1_000_000 // fps
        out.write(muxer.finish())
        for offset, data in muxer.duration_patches() if patch else []:
            out.seek(offset)
            out.write(data)
    return muxer


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_ffprobe_reads_the_file_and_ffmpeg_decodes_every_picture(clips, tmp_path):
    path = tmp_path / "one.mp4"
    muxer = mux_clips(path, clips["landscape"])
    info = probe(path)
    stream_info = info["stream"]
    assert stream_info["codec_name"] == "h264" and stream_info["profile"] == "Constrained Baseline"
    assert (stream_info["width"], stream_info["height"]) == (320, 240)
    assert int(stream_info["nb_read_frames"]) == len(clips["landscape"]) == muxer.frames == 45
    assert float(info["format"]["duration"]) == pytest.approx(3.0, abs=0.01)
    assert "isom" in info["format"]["tags"]["major_brand"]
    run = decode(path)
    assert run.returncode == 0 and run.stderr == ""


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_high_profile_with_cropping_and_a_cropped_main_profile_picture(clips, tmp_path):
    for name, size, profile in (("high", (1920, 1080), "High"), ("cropped", (322, 242), "Main")):
        path = tmp_path / f"{name}.mp4"
        mux_clips(path, clips[name])
        info = probe(path)["stream"]
        assert (info["width"], info["height"], info["profile"]) == (*size, profile)
        assert int(info["nb_read_frames"]) == len(clips[name])
        assert decode(path).returncode == 0


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_a_rotation_in_the_middle_decodes_at_the_new_size(clips, tmp_path):
    path = tmp_path / "rotated.mp4"
    muxer = mux_clips(path, clips["landscape"], clips["portrait"])
    assert muxer.size_changes == 1 and (muxer.width, muxer.height) == (320, 240)
    sizes = frame_sizes(path)
    assert sizes == [(320, 240)] * len(clips["landscape"]) + [(240, 320)] * len(clips["portrait"])
    assert probe(path)["stream"]["width"] == 320  # the header names the first size
    run = decode(path)
    assert run.returncode == 0 and run.stderr == ""


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
@pytest.mark.parametrize("closed", [True, False], ids=["length patched in", "never closed"])
def test_a_file_that_was_cut_off_plays_up_to_the_last_whole_fragment(clips, tmp_path, closed):
    whole = tmp_path / "whole.mp4"
    mux_clips(whole, clips["landscape"], patch=closed)
    data = whole.read_bytes()
    frags = fragments(data)
    assert len(frags) >= 6
    per_fragment = [len(f.samples) for f in frags]
    cuts = {
        "inside an mdat": (frags[3].mdat.offset + 100, sum(per_fragment[:3])),
        "inside a moof": (frags[4].moof_offset + 20, sum(per_fragment[:4])),
        "between fragments": (frags[5].moof_offset, sum(per_fragment[:5])),
    }
    for name, (cut, whole_frames) in cuts.items():
        cut_path = tmp_path / "cut.mp4"
        cut_path.write_bytes(data[:cut])
        read = int(probe(cut_path)["stream"]["nb_read_frames"])
        assert whole_frames <= read <= whole_frames + max(per_fragment), name  # a torn sample may or may not show
        assert decode(cut_path).returncode == 0, name


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_a_file_cut_off_before_its_first_fragment_still_opens_as_an_empty_video(clips, tmp_path):
    whole = tmp_path / "whole.mp4"
    mux_clips(whole, clips["landscape"])
    data = whole.read_bytes()
    header = fragments(data)[0].moof_offset
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(data[:header])
    info = probe(cut)["stream"]
    assert (info["width"], info["height"]) == (320, 240)


@pytest.mark.skipif(
    not os.environ.get("HMI_TEST_CHROME"),
    reason="set HMI_TEST_CHROME to a Chrome binary to load a file in a <video>",
)
def test_a_browser_loads_the_file_and_plays_it_to_the_end(clips, tmp_path):
    path = tmp_path / "rotated.mp4"
    mux_clips(path, clips["landscape"], clips["portrait"])
    page = tmp_path / "page.html"
    page.write_text(
        """<!doctype html><pre id="out">pending</pre><video id="v" muted playsinline></video><script>
        const v = document.getElementById('v'), out = document.getElementById('out');
        const done = (extra) => { out.textContent = JSON.stringify({duration: v.duration,
            w: v.videoWidth, h: v.videoHeight,
            t: v.currentTime, error: v.error && v.error.message, ...extra}); };
        v.addEventListener('error', () => done({event: 'error'}));
        v.addEventListener('ended', () => done({event: 'ended'}));
        v.addEventListener('loadeddata', () => v.play());
        setTimeout(() => done({event: 'timeout'}), 7000);
        v.src = 'rotated.mp4';
        </script>"""
    )
    run = subprocess.run(
        [
            os.environ["HMI_TEST_CHROME"],
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--user-data-dir={tmp_path / 'profile'}",
            "--autoplay-policy=no-user-gesture-required",
            "--virtual-time-budget=12000",
            "--dump-dom",
            page.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    out = run.stdout[run.stdout.index('<pre id="out">') + len('<pre id="out">') : run.stdout.index("</pre>")]
    result = json.loads(out)
    assert result["error"] is None and result["duration"] == pytest.approx(5.0, abs=0.05)
    assert result["t"] > 4.5


# ---- HEVC ------------------------------------------------------------------------------------------------------------


def hevc_size_of(sps: bytes) -> tuple[int, int]:
    info = parse_hevc_sps(sps)
    return info.width, info.height


def hevc_mux(items, **kwargs) -> tuple[bytes, Fmp4Muxer]:
    return mux(items, family="hevc", **kwargs)


def hvcc_of(data: bytes) -> bytes:
    return parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "hev1", "hvcC").payload


def read_hvcc(payload: bytes) -> dict:
    """The fields of an ``hvcC`` box (ISO/IEC 14496-15 8.3.3.1), and its arrays of NAL units by type."""
    out = {
        "version": payload[0],
        "space": payload[1] >> 6,
        "tier": (payload[1] >> 5) & 1,
        "profile": payload[1] & 0x1F,
        "compatibility": struct.unpack_from(">I", payload, 2)[0],
        "constraints": int.from_bytes(payload[6:12], "big"),
        "level": payload[12],
        "reserved": (payload[13] >> 4, payload[15] >> 2, payload[16] >> 2, payload[17] >> 3, payload[18] >> 3),
        "segmentation": struct.unpack_from(">H", payload, 13)[0] & 0x0FFF,
        "parallelism": payload[15] & 3,
        "chroma": payload[16] & 3,
        "depth_luma": 8 + (payload[17] & 7),
        "depth_chroma": 8 + (payload[18] & 7),
        "frame_rate": struct.unpack_from(">H", payload, 19)[0],
        "constant_frame_rate": payload[21] >> 6,
        "layers": (payload[21] >> 3) & 7,
        "nested": (payload[21] >> 2) & 1,
        "length_size": 1 + (payload[21] & 3),
    }
    arrays: dict[int, tuple[int, list[bytes]]] = {}
    pos = 23
    for _ in range(payload[22]):
        completeness, kind = payload[pos] >> 7, payload[pos] & 0x3F
        assert payload[pos] & 0x40 == 0  # the reserved bit
        (count,) = struct.unpack_from(">H", payload, pos + 1)
        pos += 3
        nals = []
        for _ in range(count):
            (length,) = struct.unpack_from(">H", payload, pos)
            nals.append(payload[pos + 2 : pos + 2 + length])
            pos += 2 + length
        arrays[kind] = (completeness, nals)
    assert pos == len(payload)  # nothing left over
    return {**out, "arrays": arrays}


def test_the_real_hevc_pattern_sps_says_its_profile_level_size_and_depth():
    info = parse_hevc_sps(HEVC_SPS_240x400)
    assert (info.width, info.height) == (240, 400)
    assert (info.profile_space, info.tier_flag, info.profile_idc, info.level_idc) == (0, 0, 1, 60)
    assert (info.compatibility_flags, info.constraint_flags) == (0x60000000, 0x900000000000)
    assert (info.chroma_format_idc, info.bit_depth_luma, info.bit_depth_chroma) == (1, 8, 8)
    assert (info.sub_layers, info.temporal_id_nesting) == (1, 1)
    assert hevc_codec_string(HEVC_SPS_240x400) == "hev1.1.6.L60.90"  # the string the hub's codec line carries


@pytest.mark.parametrize(
    ("shape", "size"),
    [
        ({"width": 1920, "height": 1088, "crop": (0, 0, 0, 4)}, (1920, 1080)),  # a 4:2:0 crop unit is two pixels
        ({"width": 640, "height": 360, "crop": (3, 1, 0, 0)}, (632, 360)),
        ({"width": 176, "height": 144, "chroma": 2, "crop": (1, 2, 3, 4)}, (170, 137)),  # 4:2:2: 2 wide, 1 tall
        ({"width": 176, "height": 144, "chroma": 3, "crop": (1, 2, 3, 4)}, (173, 137)),  # 4:4:4: one pixel
        ({"width": 176, "height": 144, "chroma": 0, "crop": (2, 2, 0, 0)}, (172, 144)),  # monochrome
        ({"width": 176, "height": 144, "chroma": 3, "separate": True, "crop": (1, 1, 1, 1)}, (174, 142)),
        ({"width": 1280, "height": 720}, (1280, 720)),
    ],
    ids=["crop bottom", "crop left and right", "4:2:2", "4:4:4", "4:0:0", "separate planes", "no window"],
)
def test_an_hevc_picture_is_sized_by_its_conformance_window(shape, size):
    sps = build_hevc_sps(**shape)
    assert hevc_size_of(sps) == size
    assert (parse_hevc_sps(sps).chroma_format_idc) == shape.get("chroma", 1)


def test_an_hevc_sps_with_sub_layers_a_high_bit_depth_and_emulation_prevention_is_read_to_the_end():
    sps = build_hevc_sps(
        3840, 2160, sub_layers=3, depth=10, chroma=2, profile=4, tier=1, space=2, level=153, nesting=0,
        compatibility=0x0000_0008, constraints=0,
    )  # fmt: skip
    assert b"\x00\x00\x03" in sps  # the zero runs of the profile are escaped
    info = parse_hevc_sps(sps)
    assert (info.width, info.height, info.sub_layers, info.temporal_id_nesting) == (3840, 2160, 3, 0)
    assert (info.profile_space, info.tier_flag, info.profile_idc, info.level_idc) == (2, 1, 4, 153)
    assert (info.compatibility_flags, info.constraint_flags) == (8, 0)
    assert (info.chroma_format_idc, info.bit_depth_luma, info.bit_depth_chroma) == (2, 10, 10)
    # the codec string of the same unit (what the hub's codec line says): space B, profile 4, flag 3 reversed, tier H
    assert hevc_codec_string(sps) == "hev1.B4.10000000.H153"


def test_a_unit_that_is_not_an_hevc_sps_or_is_cut_short_is_refused():
    with pytest.raises(ValueError, match="not an HEVC SPS"):
        parse_hevc_sps(HEVC_PPS)
    with pytest.raises(ValueError, match="not an HEVC SPS"):
        parse_hevc_sps(bytes([0x67, 0x42, 0xC0, 0x1E] * 5))  # an H.264 SPS
    with pytest.raises(ValueError, match="ends too soon"):
        parse_hevc_sps(HEVC_SPS_240x400[:16])
    with pytest.raises(ValueError, match="empty picture"):
        parse_hevc_sps(build_hevc_sps(16, 16, crop=(8, 8, 0, 0)))


def test_the_hevc_file_is_a_header_and_whole_fragments_that_tile_it():
    data, muxer = hevc_mux(hevc_stream(count=13, gop=4))
    top = parse_boxes(data)  # asserts the sizes add up to the file exactly
    kinds = [b.kind for b in top]
    assert kinds[:2] == ["ftyp", "moov"] and kinds[2:] == ["moof", "mdat"] * ((len(kinds) - 2) // 2)
    ftyp = top[0].payload
    assert ftyp[:4] == b"isom" and b"hev1" in ftyp[8:] and b"avc1" not in ftyp[8:] and b"iso5" in ftyp[8:]
    assert (muxer.family, muxer.codec, muxer.width, muxer.height) == ("hevc", "hev1.1.6.L60.90", 240, 400)


def test_the_hevc_moov_describes_one_hev1_video_track():
    data, _ = hevc_mux(hevc_stream())
    moov = parse_boxes(data)[1]
    assert [c.kind for c in moov.children] == ["mvhd", "trak", "mvex"]
    trak = moov.find("trak")
    assert struct.unpack_from(">II", trak.find("tkhd").payload, len(trak.find("tkhd").payload) - 8) == (
        240 << 16,
        400 << 16,
    )
    assert trak.find("mdia", "hdlr").payload[8:12] == b"vide"
    stbl = trak.find("mdia", "minf", "stbl")
    assert [c.kind for c in stbl.children] == ["stsd", "stts", "stsc", "stsz", "stco"]
    stsd = stbl.find("stsd")
    assert struct.unpack_from(">I", stsd.payload, 4)[0] == 1
    hev1 = stsd.find("hev1")
    assert hev1.payload[24:28] == struct.pack(">HH", 240, 400)
    assert [c.kind for c in hev1.children] == ["hvcC"]


def test_hvcc_says_what_the_real_sps_says_and_carries_its_vps_sps_and_pps_whole():
    data, _ = hevc_mux(pattern_stream("hevc"))
    hvcc = read_hvcc(hvcc_of(data))
    assert hvcc["version"] == 1 and (hvcc["space"], hvcc["tier"], hvcc["profile"], hvcc["level"]) == (0, 0, 1, 60)
    assert (hvcc["compatibility"], hvcc["constraints"]) == (0x60000000, 0x900000000000)
    assert (hvcc["chroma"], hvcc["depth_luma"], hvcc["depth_chroma"]) == (1, 8, 8)
    assert (hvcc["layers"], hvcc["nested"], hvcc["length_size"]) == (1, 1, 4)
    assert hvcc["reserved"] == (0xF, 0x3F, 0x3F, 0x1F, 0x1F)  # the reserved bits are ones, as the box says
    assert (hvcc["segmentation"], hvcc["parallelism"], hvcc["frame_rate"], hvcc["constant_frame_rate"]) == (0, 0, 0, 0)
    first = pattern_units("hevc")[0]
    wanted = {t: [bytes(first[s:e])] for t, s, e in iter_nals(first, "hevc") if t in (32, 33, 34)}
    assert {t: nals for t, (_, nals) in hvcc["arrays"].items()} == wanted
    assert list(hvcc["arrays"]) == [32, 33, 34]  # VPS, SPS, PPS, in that order
    assert all(completeness == 0 for completeness, _ in hvcc["arrays"].values())  # ``hev1``: others may follow


def test_hvcc_of_a_wide_stream_with_temporal_layers_names_its_format_depth_tier_and_layers():
    sps = build_hevc_sps(1920, 1080, sub_layers=3, depth=10, chroma=2, profile=4, tier=1, space=1, level=123, nesting=0)
    data, muxer = hevc_mux(hevc_stream(sps=sps))
    hvcc = read_hvcc(hvcc_of(data))
    assert (hvcc["space"], hvcc["tier"], hvcc["profile"], hvcc["level"]) == (1, 1, 4, 123)
    assert (hvcc["chroma"], hvcc["depth_luma"], hvcc["depth_chroma"]) == (2, 10, 10)
    assert (hvcc["layers"], hvcc["nested"]) == (3, 0)
    assert (muxer.width, muxer.height) == (1920, 1080) and muxer.codec.startswith("hev1.A4.")


def test_hevc_samples_are_length_prefixed_and_the_parameter_sets_and_delimiters_stay_out_of_them():
    items = hevc_stream(count=9, gop=4)
    data, _ = hevc_mux(items)
    bodies = [b for frag in fragments(data) for b in frag.sample_bytes()]
    assert len(bodies) == 9
    for (_, unit, key), body in zip(items, bodies, strict=True):
        nals = avcc_nals(body)  # (4-byte lengths, checked to add up exactly)
        assert [(n[0] >> 1) & 0x3F for n in nals] == ([20] if key else [1])  # no AUD (35), VPS, SPS or PPS (32 to 34)
        assert nals[0] == unit[unit.rindex(START) + 4 :]


def test_hevc_delimiters_and_filler_data_are_dropped_and_sei_is_kept():
    sei = bytes([0x4E, 0x01, 0x05, 0x04]) + b"abcd\x80"  # a prefix SEI (39)
    unit = au(
        HEVC_AUD_NAL,
        HEVC_VPS,
        HEVC_SPS_240x400,
        HEVC_PPS,
        sei,
        bytes([IDR_N_LP, 0x01]) + b"\xaa" * 30,
        bytes([0x4C, 0x01]) + b"\xff" * 4,
    )
    sample, key, vps, sps, pps = to_hvcc(unit)
    assert key and (vps, sps, pps) == ([HEVC_VPS], [HEVC_SPS_240x400], [HEVC_PPS])
    assert [(n[0] >> 1) & 0x3F for n in avcc_nals(sample)] == [
        32,
        33,
        34,
        39,
        20,
    ]  # as is: the muxer decides what hvcC has
    plain, _, _, _, _ = to_hvcc(unit, drop=frozenset({35, 38, 32, 33, 34}))
    assert [(n[0] >> 1) & 0x3F for n in avcc_nals(plain)] == [39, 20]


@pytest.mark.parametrize(
    ("kind", "key"),
    [(0x20, True), (0x22, True), (0x24, True), (0x26, True), (0x28, True), (0x2A, True), (0x2C, False), (0x02, False)],
    ids=["BLA_W_LP", "BLA_W_RADL", "BLA_N_LP", "IDR_W_RADL", "IDR_N_LP", "CRA", "reserved IRAP", "TRAIL_R"],
)
def test_the_key_pictures_of_an_hevc_file_are_its_irap_pictures(kind, key):
    unit = au(HEVC_AUD_NAL, HEVC_VPS, HEVC_SPS_240x400, HEVC_PPS, bytes([kind, 0x01]) + b"\xaa" * 20)
    assert to_hvcc(unit)[1] is key
    muxer = Fmp4Muxer(family="hevc")
    assert bool(muxer.add(0, unit)) is key  # a key picture with its sets starts the file, anything else is skipped
    assert muxer.started is key


def test_nothing_is_written_until_an_hevc_key_picture_with_a_vps_an_sps_and_a_pps_arrives():
    muxer = Fmp4Muxer(family="hevc")
    slice_nal = bytes([IDR_N_LP, 0x01]) + b"\xaa" * 20
    delta = au(HEVC_AUD_NAL, bytes([TRAIL_R, 0x01]) + b"\xbb" * 20)
    key_without_sets = au(HEVC_AUD_NAL, slice_nal)
    h264_style = au(HEVC_AUD_NAL, HEVC_SPS_240x400, HEVC_PPS, slice_nal)  # the pair H.264 needs: no VPS
    key = au(HEVC_AUD_NAL, HEVC_VPS, HEVC_SPS_240x400, HEVC_PPS, slice_nal)
    for index, unit in enumerate((delta, key_without_sets, h264_style)):
        assert muxer.add(index * 40_000, unit) == b""
    assert not muxer.started and muxer.frames == 0 and muxer.skipped == 3 and muxer.duration_patches() == []
    first = muxer.add(120_000, key)
    assert first.startswith(b"\x00\x00\x00\x24ftyp") and muxer.started and muxer.frames == 1
    assert [b.kind for b in parse_boxes(first + muxer.finish())] == ["ftyp", "moov", "moof", "mdat"]


def test_hevc_key_pictures_with_the_same_parameter_sets_do_not_repeat_them_and_start_a_fragment():
    items = hevc_stream(count=17, gop=8, fps=25)
    assert items[8][1].count(HEVC_SPS_240x400) == 1  # the input does repeat them in front of every key picture
    data, _ = hevc_mux(items, fragment_ms=100)
    frags = fragments(data)
    assert [len(f.samples) for f in frags] == [3, 3, 2, 3, 3, 2, 1]
    for frag in frags:
        assert 0x02000000 not in [flag for _, _, flag in frag.samples][1:]  # a key picture only ever begins a fragment
        assert not any(((n[0] >> 1) & 0x3F) in (32, 33, 34, 35) for b in frag.sample_bytes() for n in avcc_nals(b))
    flags = [flag for f in frags for _, _, flag in f.samples]
    assert flags == [0x02000000 if key else 0x01010000 for _, _, key in items]


def test_hevc_timing_is_the_same_clock_as_h264s():
    items = hevc_stream(count=5, gop=5)
    ts = [10_000_000, 10_040_000, 10_080_000, 10_200_000, 10_220_000]  # a pause in the middle
    data, muxer = hevc_mux([(t, unit, key) for t, (_, unit, key) in zip(ts, items, strict=True)])
    durations = [d for f in fragments(data) for d, _, _ in f.samples]
    assert durations == [3600, 3600, 10_800, 1800, 1800] and muxer.duration_us == sum(durations) * 1_000_000 // 90_000
    millis = muxer.duration_us // 1000
    moov = parse_boxes(data)[1]
    assert struct.unpack_from(">I", moov.find("mvhd").payload, 16)[0] == millis
    assert struct.unpack_from(">Q", moov.find("mvex", "mehd").payload, 4)[0] == millis


def test_an_hevc_size_change_goes_on_in_the_same_file_with_the_new_parameter_sets_in_the_samples():
    other = build_hevc_sps(400, 240)
    before = hevc_stream(count=8, gop=4)
    after = hevc_stream(count=8, gop=4, sps=other, first_us=before[-1][0] + 40_000)
    data, muxer = hevc_mux(before + after)
    assert (muxer.width, muxer.height, muxer.size_changes) == (240, 400, 1)  # the header keeps the first picture's size
    assert read_hvcc(hvcc_of(data))["arrays"][33][1] == [HEVC_SPS_240x400]
    hev1 = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "hev1")
    assert hev1.payload[24:28] == struct.pack(">HH", 240, 400)
    bodies = [[(n[0] >> 1) & 0x3F for n in avcc_nals(b)] for f in fragments(data) for b in f.sample_bytes()]
    assert len(bodies) == 16
    assert bodies[:8] == [[20], [1], [1], [1]] * 2  # the first size: its sets are in hvcC only
    # the new size: its sets are in every key picture's sample, for a decoder that comes in at that point
    assert bodies[8] == [32, 33, 34, 20] == bodies[12] and bodies[9] == [1]
    samples = [b for f in fragments(data) for b in f.sample_bytes()]
    assert avcc_nals(samples[8])[1] == other


def test_going_back_to_the_first_hevc_size_puts_its_parameter_sets_back_in_the_stream():
    other = build_hevc_sps(400, 240)
    one = hevc_stream(count=4, gop=4)
    two = hevc_stream(count=4, gop=4, sps=other, first_us=one[-1][0] + 40_000)
    three = hevc_stream(count=8, gop=4, first_us=two[-1][0] + 40_000)
    data, muxer = hevc_mux(one + two + three)
    assert muxer.size_changes == 2
    bodies = [avcc_nals(b) for f in fragments(data) for b in f.sample_bytes()]
    assert (
        bodies[8][1] == HEVC_SPS_240x400
    )  # in the sample: the decoder holds the other size's set until this replaces it
    assert not any((n[0] >> 1) & 0x3F == 33 for body in bodies[12:] for n in body)  # and then it is the one in effect


def test_a_caller_that_wants_one_hevc_size_per_file_gets_the_change_as_an_exception():
    before = hevc_stream(count=4, gop=4)
    after = hevc_stream(count=4, gop=4, sps=build_hevc_sps(400, 240), first_us=before[-1][0] + 40_000)
    muxer = Fmp4Muxer(family="hevc", on_size_change="raise")
    for ts, unit, _ in before:
        muxer.add(ts, unit)
    with pytest.raises(PictureSizeChanged) as raised:
        muxer.add(*after[0][:2])
    assert (raised.value.old, raised.value.new) == ((240, 400), (400, 240)) and muxer.frames == 4


def test_an_hevc_sps_that_cannot_be_read_uses_the_size_it_was_given_and_the_profile_it_can_read():
    odd = HEVC_SPS_240x400[:19]  # cut inside the picture size: the profile is there, the size is not
    items = [(0, au(HEVC_AUD_NAL, HEVC_VPS, odd, HEVC_PPS, bytes([IDR_N_LP, 0x01]) + b"\xaa" * 20), True)]
    data, muxer = hevc_mux(items, width=640, height=360)
    hev1 = parse_boxes(data)[1].find("trak", "mdia", "minf", "stbl", "stsd", "hev1")
    assert hev1.payload[24:28] == struct.pack(">HH", 640, 360) and (muxer.width, muxer.height) == (640, 360)
    assert read_hvcc(hvcc_of(data))["profile"] == 1 and read_hvcc(hvcc_of(data))["level"] == 60
    with pytest.raises(ValueError, match="ends too soon"):
        Fmp4Muxer(family="hevc").add(*items[0][:2])


def test_a_muxer_writes_h264_or_hevc_and_nothing_else():
    assert Fmp4Muxer().family == "h264" and Fmp4Muxer(family="h264").family == "h264"
    for other in ("mjpeg", "vp9", ""):
        with pytest.raises(ValueError, match="h264 or hevc"):
            Fmp4Muxer(family=other)


def test_an_h264_unit_given_to_an_hevc_muxer_is_no_start_and_the_other_way_round():
    h264 = stream(count=1)[0][1]
    hevc = hevc_stream(count=1)[0][1]
    muxer = Fmp4Muxer(family="hevc")
    assert muxer.add(0, h264) == b"" and not muxer.started
    other = Fmp4Muxer(family="h264")
    assert other.add(0, hevc) == b"" and not other.started


# ---- HEVC and H.264 in real decoders


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_ffprobe_reads_the_real_hevc_pattern_as_hevc_in_240x400_with_60_pictures_and_ffmpeg_decodes_them(tmp_path):
    path = tmp_path / "pattern.mp4"
    muxer = mux_clips(path, list(pattern_units("hevc")), family="hevc")
    info = probe(path)
    stream_info = info["stream"]
    assert stream_info["codec_name"] == "hevc" and stream_info["codec_tag_string"] == "hev1"
    assert stream_info["profile"] == "Main" and stream_info["pix_fmt"] == "yuv420p"
    assert (stream_info["width"], stream_info["height"]) == (240, 400)
    assert int(stream_info["nb_read_frames"]) == muxer.frames == 60
    assert float(info["format"]["duration"]) == pytest.approx(4.0, abs=0.01)
    assert "isom" in info["format"]["tags"]["major_brand"]
    run = decode(path)
    assert run.returncode == 0 and run.stderr == ""


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
def test_ffprobe_reads_the_real_h264_pattern_as_h264_in_240x400_with_60_pictures_and_ffmpeg_decodes_them(tmp_path):
    path = tmp_path / "pattern.mp4"
    muxer = mux_clips(path, list(pattern_units("h264")))
    info = probe(path)
    stream_info = info["stream"]
    assert stream_info["codec_name"] == "h264" and stream_info["codec_tag_string"] == "avc1"
    assert (stream_info["width"], stream_info["height"]) == (240, 400)
    assert int(stream_info["nb_read_frames"]) == muxer.frames == 60
    assert float(info["format"]["duration"]) == pytest.approx(4.0, abs=0.01)
    run = decode(path)
    assert run.returncode == 0 and run.stderr == ""


@pytest.mark.skipif(not HAVE_FFMPEG, reason=NO_FFMPEG)
@pytest.mark.parametrize("closed", [True, False], ids=["length patched in", "never closed"])
def test_a_cut_off_hevc_file_plays_up_to_the_last_whole_fragment(tmp_path, closed):
    whole = tmp_path / "whole.mp4"
    mux_clips(whole, list(pattern_units("hevc")), patch=closed, family="hevc")
    data = whole.read_bytes()
    frags = fragments(data)
    assert len(frags) >= 6
    per_fragment = [len(f.samples) for f in frags]
    cuts = {
        "inside an mdat": (frags[3].mdat.offset + 100, sum(per_fragment[:3])),
        "inside a moof": (frags[4].moof_offset + 20, sum(per_fragment[:4])),
        "between fragments": (frags[5].moof_offset, sum(per_fragment[:5])),
    }
    for name, (cut, whole_frames) in cuts.items():
        cut_path = tmp_path / "cut.mp4"
        cut_path.write_bytes(data[:cut])
        read = int(probe(cut_path)["stream"]["nb_read_frames"])
        assert whole_frames <= read <= whole_frames + max(per_fragment), name
        assert decode(cut_path).returncode == 0, name


@pytest.mark.skipif(not HAVE_X265, reason=NO_X265)
def test_a_rotation_in_real_hevc_decodes_at_the_new_size_with_the_header_naming_the_first(tmp_path):
    land = encode_hevc(tmp_path / "l.h265", size="320x240")
    port = encode_hevc(tmp_path / "p.h265", size="240x320")
    path = tmp_path / "rotated.mp4"
    muxer = mux_clips(path, land, port, family="hevc")
    assert muxer.size_changes == 1 and (muxer.width, muxer.height) == (320, 240) and muxer.frames == 60
    assert frame_sizes(path) == [(320, 240)] * 30 + [(240, 320)] * 30
    assert probe(path)["stream"]["width"] == 320  # the header names the first size
    run = decode(path)
    assert run.returncode == 0 and run.stderr == ""
