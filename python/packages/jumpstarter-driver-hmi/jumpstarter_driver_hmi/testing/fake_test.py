"""The fake source: its synthetic HEVC and JPEG units are what they say they are, and it declares what it takes."""

from __future__ import annotations

import threading
import time

import pytest

from ..common import CODECS, CodecLine, InputError, KeyboardCaps, PointerCaps, PointerEvent, RotaryCaps, parse_input
from ..hub.codecs import (
    HEVC_PPS,
    HEVC_SPS,
    HEVC_VPS,
    codec_string_of,
    first_sps,
    has_idr,
    hevc_codec_string,
    is_reference,
    iter_nals,
    nal_types,
    scan_unit,
)
from ..source import InputSink
from .fake import (
    FakeInputSink,
    FakeSource,
    hevc_delta_unit,
    hevc_key_unit,
    make_hevc_pps,
    make_hevc_sps,
    make_hevc_vps,
    make_jpeg,
    make_sps,
)
from .pattern import PATTERN_SIZE, pattern_units

# -- an independent reader of the HEVC SPS: what a muxer or a decoder would read from it -------------------------------


class _Reader:
    """Reads bits and Exp-Golomb codes out of an RBSP (emulation-prevention bytes already taken out)."""

    def __init__(self, data: bytes) -> None:
        self.bits = "".join(f"{byte:08b}" for byte in data)
        self.at = 0

    def u(self, count: int) -> int:
        value = int(self.bits[self.at : self.at + count], 2) if count else 0
        self.at += count
        return value

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
        return (1 << zeros) - 1 + self.u(zeros)


def _rbsp(nal: bytes) -> bytes:
    """The payload of a NAL unit without its two header bytes and its emulation-prevention bytes."""
    out, zeros = bytearray(), 0
    for byte in nal[2:]:
        if zeros >= 2 and byte == 3:
            zeros = 0
            continue
        out.append(byte)
        zeros = zeros + 1 if byte == 0 else 0
    return bytes(out)


def read_hevc_sps(nal: bytes) -> dict:
    """The profile, level and visible size an HEVC SPS says (up to its conformance window)."""
    reader = _Reader(_rbsp(nal))
    reader.u(4)  # sps_video_parameter_set_id
    assert reader.u(3) == 0  # sps_max_sub_layers_minus1
    reader.u(1)  # sps_temporal_id_nesting_flag
    space, tier, profile = reader.u(2), reader.u(1), reader.u(5)
    compatibility = reader.u(32)
    constraints = reader.u(48)
    level = reader.u(8)
    assert reader.ue() == 0  # sps_seq_parameter_set_id
    chroma = reader.ue()
    width, height = reader.ue(), reader.ue()
    crop = (0, 0, 0, 0)
    if reader.u(1):
        crop = (reader.ue(), reader.ue(), reader.ue(), reader.ue())
    sub = 2 if chroma in (1, 2) else 1  # SubWidthC (the height's SubHeightC is 2 only for 4:2:0)
    sub_h = 2 if chroma == 1 else 1
    return {
        "profile_space": space,
        "tier": tier,
        "profile": profile,
        "compatibility": compatibility,
        "constraints": constraints,
        "level": level,
        "chroma": chroma,
        "coded": (width, height),
        "size": (width - sub * (crop[0] + crop[1]), height - sub_h * (crop[2] + crop[3])),
    }


def _nal_of(unit: bytes, kind: int, family: str = "hevc") -> bytes:
    return next(bytes(unit[start:end]) for t, start, end in iter_nals(unit, family) if t == kind)


# -- HEVC ------------------------------------------------------------------------------------------------------------


def test_the_reader_agrees_with_a_real_encoders_sps():
    """Checks the checker: what it reads out of x265's own SPS is the pattern's size and the Main profile."""
    sps = _nal_of(pattern_units("hevc")[0], HEVC_SPS)
    info = read_hevc_sps(sps)
    assert info["size"] == PATTERN_SIZE
    assert (info["profile"], info["chroma"]) == (1, 1)


@pytest.mark.parametrize(
    ("width", "height", "level"),
    [(480, 800, 93), (240, 400, 93), (1080, 2400, 153), (1206, 2622, 153), (3840, 2160, 153), (7680, 4320, 186)],
)
def test_the_hevc_sps_says_the_pictures_size_profile_and_level(width, height, level):
    info = read_hevc_sps(make_hevc_sps(width, height))
    assert info["size"] == (width, height)
    assert info["coded"][0] % 8 == 0 and info["coded"][1] % 8 == 0
    assert info["coded"][0] - width < 8 and info["coded"][1] - height < 8
    assert (info["profile_space"], info["tier"], info["profile"], info["chroma"]) == (0, 0, 1, 1)
    assert info["compatibility"] == 0x60000000  # Main and Main 10
    assert info["level"] == level
    assert info["constraints"] >> 40 == 0xB0  # progressive, frame only (and the bit before it)


def test_an_odd_size_is_made_even():
    assert read_hevc_sps(make_hevc_sps(481, 801))["size"] == (480, 800)


@pytest.mark.parametrize(
    ("width", "height", "name"),
    [(480, 800, "hev1.1.6.L93.B0"), (1080, 2400, "hev1.1.6.L153.B0"), (7680, 4320, "hev1.1.6.L186.B0")],
)
def test_the_hevc_codec_string_is_read_from_the_sps(width, height, name):
    assert hevc_codec_string(make_hevc_sps(width, height)) == name


def test_the_hevc_parameter_sets_are_the_types_a_decoder_expects():
    assert nal_types(b"\x00\x00\x00\x01" + make_hevc_vps(), "hevc") == [HEVC_VPS]
    assert nal_types(b"\x00\x00\x00\x01" + make_hevc_sps(64, 64), "hevc") == [HEVC_SPS]
    assert nal_types(b"\x00\x00\x00\x01" + make_hevc_pps(), "hevc") == [HEVC_PPS]


def test_a_hevc_parameter_set_has_no_start_code_inside_it():
    """Emulation prevention: a run of zero bytes in a set must not look like the start of a NAL unit."""
    for nal in (make_hevc_vps(), make_hevc_sps(480, 800), make_hevc_sps(1280, 720), make_hevc_pps()):
        assert b"\x00\x00\x01" not in nal and b"\x00\x00\x00" not in nal


def test_a_hevc_key_unit_is_an_idr_with_its_parameter_sets_in_front():
    sps = make_hevc_sps(480, 800)
    unit = hevc_key_unit(sps, b"00000000", 100)
    assert nal_types(unit, "hevc") == [HEVC_VPS, HEVC_SPS, HEVC_PPS, 19]
    scan = scan_unit(unit, "hevc")
    assert scan.idr and scan.picture and scan.reference
    assert (len(scan.vps), len(scan.sps), len(scan.pps)) == (1, 1, 1)
    assert first_sps(unit, "hevc") == sps
    assert codec_string_of(unit, "hevc") == "hev1.1.6.L93.B0"
    assert len(unit) > 100


def test_a_hevc_delta_unit_is_a_trailing_picture():
    unit = hevc_delta_unit(b"00000007", 50)
    assert nal_types(unit, "hevc") == [1]  # TRAIL_R
    assert not has_idr(unit, "hevc")
    assert is_reference(unit, "hevc")
    assert scan_unit(unit, "hevc").picture


def test_ffprobe_reads_the_hevc_stream_the_fake_makes():
    """A real parser finds the profile, level and size in what the fake writes (the slices do not decode)."""
    import json
    import subprocess

    from ..recording.mp4_testing import HAVE_FFMPEG

    if not HAVE_FFMPEG:
        pytest.skip("no ffmpeg")  # ty: ignore[too-many-positional-arguments]
    stream = hevc_key_unit(make_hevc_sps(1206, 2622), b"00000000", 100)
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-f",
            "hevc",
            "-i",
            "pipe:0",
            "-show_entries",
            "stream=profile,width,height,level",
            "-of",
            "json",
        ],
        input=stream,
        capture_output=True,
        check=False,
        timeout=30,
    )
    found = json.loads(probe.stdout)["streams"][0]
    assert (found["profile"], found["width"], found["height"], found["level"]) == ("Main", 1206, 2622, 153)


# -- JPEG ------------------------------------------------------------------------------------------------------------


def _markers(jpeg: bytes) -> list[tuple[int, bytes]]:
    """Walk a JPEG's marker segments up to its end: ``[(marker, payload)]``."""
    assert jpeg[:2] == b"\xff\xd8"
    out, at = [], 2
    while jpeg[at : at + 2] != b"\xff\xd9":
        assert jpeg[at] == 0xFF
        length = int.from_bytes(jpeg[at + 2 : at + 4], "big")
        out.append((jpeg[at + 1], jpeg[at + 4 : at + 2 + length]))
        at += 2 + length
    assert at + 2 == len(jpeg)
    return out


def test_a_jpeg_starts_and_ends_like_one_and_its_segments_can_be_walked():
    jpeg = make_jpeg(480, 800, b"0000002a", 100)
    assert jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9"
    markers = _markers(jpeg)
    assert [m for m, _ in markers] == [0xE0, 0xC0, 0xFE]
    frame = dict(markers)[0xC0]
    assert (int.from_bytes(frame[3:5], "big"), int.from_bytes(frame[1:3], "big")) == (480, 800)  # width, height
    assert dict(markers)[0xFE].startswith(b"0000002a")


def test_a_big_jpeg_is_split_into_comment_segments_that_fit_their_length_field():
    jpeg = make_jpeg(64, 64, b"", 150_000)
    comments = [payload for marker, payload in _markers(jpeg) if marker == 0xFE]
    assert len(comments) == 3
    assert sum(len(c) for c in comments) == 150_000
    assert all(len(c) + 2 <= 0xFFFF for c in comments)


def test_a_jpeg_is_a_key_picture_with_no_parameter_sets():
    jpeg = make_jpeg(480, 800, b"00000000", 10)
    scan = scan_unit(jpeg, "mjpeg")
    assert scan.idr and scan.picture and scan.reference
    assert not scan.sps and not scan.pps
    assert codec_string_of(jpeg, "mjpeg") == "mjpeg"
    assert has_idr(jpeg, "mjpeg")


# -- what the source declares ----------------------------------------------------------------------------------------


def test_by_default_it_makes_every_codec_and_h264_comes_first():
    caps = FakeSource().capabilities()
    assert caps.codecs == CODECS == ("h264", "hevc", "mjpeg")


def test_the_codecs_it_declares_are_the_ones_it_was_given():
    assert FakeSource(codecs=("hevc", "h264")).capabilities().codecs == ("hevc", "h264")
    assert FakeSource(codecs=["mjpeg"]).capabilities().codecs == ("mjpeg",)


@pytest.mark.parametrize("codecs", [(), ("vp9",), ("h264", "h264")])
def test_codecs_the_interface_does_not_have_are_refused(codecs):
    with pytest.raises(ValueError, match="codecs"):
        FakeSource(codecs=codecs)


def test_touch_mode_is_a_finger_hard_keys_and_text():
    caps = FakeSource().capabilities()
    assert caps.pointer.info() == {
        "kinds": ["touch"],
        "absolute": True,
        "relative": False,
        "buttons": False,
        "wheel": False,
        "max_contacts": 1,
    }
    assert caps.keyboard.info() == {"codes": [], "text": True, "layout": "us"}
    assert caps.keys == ("HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN")
    assert [k.key for k in caps.hard_keys] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]
    assert caps.optional_keys == ("POWER", "LOCK")


def test_mouse_mode_is_a_mouse_and_a_keyboard_and_no_hard_keys():
    caps = FakeSource(mode="mouse").capabilities()
    assert caps.pointer.info() == {
        "kinds": ["mouse"],
        "absolute": True,
        "relative": False,
        "buttons": True,
        "wheel": True,
        "max_contacts": 1,
    }
    assert caps.keyboard.info() == {"codes": "all", "text": True, "layout": "us"}
    assert caps.keys == () and caps.hard_keys == () and caps.optional_keys == ()
    assert caps.has_input


def test_relative_adds_relative_motion_to_the_mouse():
    assert FakeSource(mode="mouse", relative=True).capabilities().pointer.relative
    assert FakeSource(mode="mouse", relative=True).capabilities().pointer.absolute
    assert not FakeSource(mode="mouse").capabilities().pointer.relative


def test_a_keyboard_without_text_can_be_declared():
    assert not FakeSource(mode="mouse", text=False).capabilities().keyboard.text
    assert not FakeSource(text=False).capabilities().keyboard.present


@pytest.mark.parametrize("options", [{"mode": "pen"}, {"relative": True}, {"mode": "touch", "relative": True}])
def test_a_mode_it_does_not_have_is_refused(options):
    with pytest.raises(ValueError):
        FakeSource(**options)


@pytest.mark.parametrize("mode", ["touch", "mouse"])
def test_a_view_only_source_declares_nothing(mode):
    source = FakeSource(control=False, mode=mode)
    caps = source.capabilities()
    assert not caps.has_input
    assert not caps.pointer.present and not caps.keyboard.present and caps.keys == () and caps.hard_keys == ()
    assert source.input_sink() is None


def test_a_test_can_replace_what_the_pointer_and_the_keyboard_declare():
    source = FakeSource()
    source.pointer_caps = PointerCaps(kinds=("touch", "pen"), max_contacts=3, buttons=True)
    source.keyboard_caps = KeyboardCaps(codes=("KeyA",))
    caps = source.capabilities()
    assert caps.pointer.max_contacts == 3 and caps.pointer.kinds == ("touch", "pen")
    assert caps.keyboard.codes == ("KeyA",)
    assert not FakeSource(control=False).capabilities().has_input


VOLUME = RotaryCaps("volume", "Volume", push="VOLUME_PUSH")
TUNE = RotaryCaps("tune")


def test_it_has_no_rotary_unless_it_is_given_some():
    assert FakeSource().capabilities().rotaries == ()
    assert FakeSource(mode="mouse").capabilities().rotaries == ()


def test_the_rotaries_it_is_given_are_declared_and_their_push_button_is_a_key_it_takes():
    caps = FakeSource(rotaries=[VOLUME, TUNE]).capabilities()
    assert caps.rotaries == (VOLUME, TUNE)
    assert caps.keys == ("HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN", "VOLUME_PUSH")
    assert [k.key for k in caps.hard_keys] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]  # the push is the knob's
    assert caps.info(width=None, height=None)["input"]["rotaries"] == [
        {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"},
        {"id": "tune", "label": "Tune", "push": None},
    ]


def test_a_push_button_that_is_a_key_already_is_not_added_twice():
    caps = FakeSource(rotaries=[RotaryCaps("home-knob", push="home"), RotaryCaps("other", push="HOME")]).capabilities()
    assert caps.keys == ("HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN")


def test_a_mouse_can_have_a_knob_and_a_knob_alone_is_input():
    caps = FakeSource(mode="mouse", rotaries=[VOLUME]).capabilities()
    assert caps.keys == ("VOLUME_PUSH",) and caps.hard_keys == () and caps.has_input
    knob = FakeSource(mode="mouse", text=False, rotaries=[TUNE])
    knob.pointer_caps, knob.keyboard_caps = PointerCaps(), KeyboardCaps()
    assert knob.capabilities().has_input and knob.capabilities().keys == ()


@pytest.mark.parametrize("mode", ["touch", "mouse"])
def test_a_view_only_source_has_no_rotary(mode):
    caps = FakeSource(control=False, mode=mode, rotaries=[VOLUME]).capabilities()
    assert caps.rotaries == () and caps.keys == () and not caps.has_input


@pytest.mark.parametrize("rotaries", [[VOLUME, RotaryCaps("volume")], ["volume"], [{"id": "volume"}]])
def test_rotaries_that_cannot_be_declared_are_refused(rotaries):
    with pytest.raises(ValueError, match="rotar"):
        FakeSource(rotaries=rotaries)


def test_a_key_picture_costs_what_was_declared_unless_the_source_says_otherwise_once_started():
    assert FakeSource().capabilities().keyframe_cost == "cheap"
    assert FakeSource(keyframe_cost="none").capabilities().keyframe_cost == "none"
    source = FakeSource(keyframe_cost="none", start_keyframe_cost="cheap", fps=200)
    assert source.capabilities().keyframe_cost == "none"  # the encoder is not picked yet
    sink = Collector()
    source.start(sink, None, 5.0)
    try:
        assert source.capabilities().keyframe_cost == "cheap"
    finally:
        source.stop(5.0)
    source.start_keyframe_cost = "restart"  # the next start picks another
    source.start(Collector(), None, 5.0)
    try:
        assert source.capabilities().keyframe_cost == "restart"
    finally:
        source.stop(5.0)


def test_a_start_cost_that_is_not_one_is_refused():
    with pytest.raises(ValueError, match="start_keyframe_cost"):
        FakeSource(start_keyframe_cost="free")


# -- what it makes ---------------------------------------------------------------------------------------------------


class Collector:
    """A sink that keeps what a source publishes."""

    def __init__(self) -> None:
        self.lines: list[CodecLine] = []
        #: how many units had been published when each line was
        self.line_at: list[int] = []
        self.units: list[tuple[bytes, bool | None]] = []
        self.ended_with: list[str | None] = []
        self.enough = threading.Event()

    def config(self, line: CodecLine) -> None:
        self.lines.append(line)
        self.line_at.append(len(self.units))

    def media(self, ts_us: int, access_unit: bytes, *, idr: bool | None = None, reference: bool | None = None) -> None:
        self.units.append((access_unit, idr))
        if len(self.units) >= 12:
            self.enough.set()

    def discontinuity(self) -> None:
        pass

    def ended(self, error: str | None = None) -> None:
        self.ended_with.append(error)


def make(codec: str, **options) -> tuple[FakeSource, Collector]:
    """A source that has made a dozen pictures in ``codec``, stopped."""
    source = FakeSource(fps=200, gop=5, **options)
    sink = Collector()
    source.start(sink, None, 5.0, codec=codec)
    assert sink.enough.wait(10)
    source.stop(5.0)
    return source, sink


@pytest.mark.parametrize(
    ("codec", "prefix", "family"), [("h264", "avc1.", "h264"), ("hevc", "hev1.", "hevc"), ("mjpeg", "mjpeg", "mjpeg")]
)
def test_the_codec_line_names_the_codec_and_the_units_are_that_codecs(codec, prefix, family):
    source, sink = make(codec, width=480, height=800)
    line = sink.lines[0]
    assert line.codec.startswith(prefix)
    assert line.family == family
    assert (line.width, line.height, line.fps) == (480, 800, 200)
    assert source.codec == codec and source.started_codecs == [codec]
    keys = [index for index, (unit, idr) in enumerate(sink.units) if idr]
    assert all(has_idr(unit, family) == bool(idr) for unit, idr in sink.units)
    if family == "mjpeg":
        assert keys == list(range(len(sink.units)))  # every picture is a key picture
        assert all(unit[:2] == b"\xff\xd8" and unit[-2:] == b"\xff\xd9" for unit, _ in sink.units)
    else:
        assert keys[:3] == [0, 5, 10]  # a key picture every gop pictures
        assert codec_string_of(sink.units[0][0], family) == line.codec


def test_the_hevc_stream_has_its_parameter_sets_at_every_key_picture_and_trailing_pictures_between():
    _, sink = make("hevc")
    kinds = [nal_types(unit, "hevc") for unit, _ in sink.units[:7]]
    assert kinds[0] == [HEVC_VPS, HEVC_SPS, HEVC_PPS, 19]
    assert kinds[1:5] == [[1]] * 4
    assert kinds[5] == [HEVC_VPS, HEVC_SPS, HEVC_PPS, 19]


def test_the_h264_stream_is_as_it_was():
    _, sink = make("h264")
    assert nal_types(sink.units[0][0]) == [7, 8, 5]
    assert nal_types(sink.units[1][0]) == [1]
    assert first_sps(sink.units[0][0]) == make_sps(480, 800)


def until(condition, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


@pytest.mark.parametrize("codec", CODECS)
def test_a_new_size_is_a_new_codec_line_and_a_key_picture_in_every_codec(codec):
    source = FakeSource(fps=200, gop=1000)
    sink = Collector()
    source.start(sink, None, 5.0, codec=codec)
    try:
        assert sink.enough.wait(10)
        source.change_picture(200, 300, rotation=90)
        until(lambda: len(sink.lines) >= 2)
        until(lambda: len(sink.units) > sink.line_at[1] + 1)
    finally:
        source.stop(5.0)
    after = sink.line_at[1]
    assert sink.lines[1].family == codec
    assert (sink.lines[1].width, sink.lines[1].height, sink.lines[1].rotation) == (200, 300, 90)
    new_keys = [unit for unit, idr in sink.units[after:] if idr]
    assert new_keys  # a key picture follows the line (the gop is far longer than this run)
    if codec == "hevc":
        assert read_hevc_sps(first_sps(new_keys[0], "hevc") or b"")["size"] == (200, 300)
    if codec == "mjpeg":
        assert dict(_markers(new_keys[0]))[0xC0][1:5] == (300).to_bytes(2, "big") + (200).to_bytes(2, "big")


def test_start_refuses_a_codec_it_does_not_make():
    source = FakeSource(codecs=("h264",))
    with pytest.raises(RuntimeError, match="h264, not hevc"):
        source.start(Collector(), None, 1.0, codec="hevc")
    assert not source.alive and source.starts == 0


def test_the_codec_can_change_between_starts():
    source = FakeSource(fps=200)
    for codec in ("h264", "mjpeg", "hevc"):
        sink = Collector()
        source.start(sink, None, 5.0, codec=codec)
        assert sink.enough.wait(10)
        source.stop(5.0)
        assert sink.lines[0].family == codec
    assert source.started_codecs == ["h264", "mjpeg", "hevc"]
    assert source.describe()["codec"] == "hevc"


@pytest.mark.parametrize("codec", CODECS)
def test_the_pattern_is_the_real_one_of_the_codec(codec):
    _, sink = make(codec, pattern=True)
    assert (sink.lines[0].width, sink.lines[0].height) == PATTERN_SIZE
    assert sink.lines[0].family == codec
    expected = pattern_units(codec)
    assert [unit for unit, _ in sink.units] == list(expected[: len(sink.units)])
    assert all(has_idr(unit, codec) == bool(idr) for unit, idr in sink.units)
    assert sink.units[0][1]  # it starts at a key picture
    if codec == "mjpeg":
        assert all(idr for _, idr in sink.units)


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_a_key_picture_request_jumps_to_the_next_key_picture_of_the_pattern(codec):
    source = FakeSource(fps=100, pattern=True)
    sink = Collector()
    source.start(sink, None, 5.0, codec=codec)
    try:
        until(lambda: len(sink.units) >= 4)
        source.pause()
        time.sleep(0.1)  # the loop is at a delta picture, far from the next key picture of the pattern
        made = len(sink.units)
        assert not sink.units[-1][1]
        source.request_keyframe()
        source.pause(False)
        until(lambda: len(sink.units) > made)
    finally:
        source.stop(5.0)
    assert sink.units[made][1], "the first picture after the request is a key picture"
    assert source.keyframe_requests == 1


# -- the recording input sink ----------------------------------------------------------------------------------------


def test_the_input_sink_records_each_kind_of_event_in_order():
    sink = FakeInputSink()
    sink.pointer(PointerEvent("down", "touch", 5.0, 6.0, 0, 1), (100, 200))
    sink.pointer(PointerEvent("move", "touch", 5.0, 7.0, 1, 1), (100, 200))  # another finger
    sink.pointer(PointerEvent("down", "mouse", 8.0, 9.0, 0, 3), (100, 200))
    sink.pointer(PointerEvent("wheel", "mouse", 8.0, 9.0, 0, 0, 0.0, 120.0), (100, 200))
    sink.keyboard("KeyA", True)
    sink.key("HOME", "long")
    sink.rotary("volume", -2)
    sink.text("hi")
    sink.release()
    assert sink.snapshot() == [
        ("touch", "down", 5, 6, (100, 200)),
        ("pointer", "move", "touch", 5.0, 7.0, 1, 1, 0.0, 0.0, (100, 200)),
        ("pointer", "down", "mouse", 8.0, 9.0, 0, 3, 0.0, 0.0, (100, 200)),
        ("pointer", "wheel", "mouse", 8.0, 9.0, 0, 0, 0.0, 120.0, (100, 200)),
        ("keyboard", "KeyA", True),
        ("key", "HOME", "long"),
        ("rotary", "volume", -2),
        ("text", "hi"),
        ("release",),
    ]
    assert [e[0] for e in sink.of("keyboard", "key")] == ["keyboard", "key"]
    assert sink.of("rotary") == [("rotary", "volume", -2)]
    sink.clear()
    assert sink.snapshot() == []


def test_the_input_sink_refuses_when_told_to_and_records_nothing():
    sink = FakeInputSink()
    sink.refuse = "busy"
    with pytest.raises(InputError, match="busy"):
        sink.keyboard("KeyA", True)
    with pytest.raises(InputError, match="busy"):
        sink.pointer(PointerEvent("down", "mouse", 1.0, 1.0), (10, 10))
    with pytest.raises(InputError, match="busy"):
        sink.rotary("volume", 1)
    assert sink.snapshot() == []


def test_a_pointer_the_default_sink_does_not_take_is_refused():
    """The base sink takes the single-touch subset and refuses the rest, whatever a source declares."""
    base = InputSink()
    with pytest.raises(InputError):
        base.pointer(parse_input(b'{"pointer": {"type": "down", "kind": "mouse", "x": 1, "y": 1}}'), (9, 9))  # ty: ignore[invalid-argument-type]
    with pytest.raises(InputError):
        base.keyboard("KeyA", True)
    with pytest.raises(InputError, match="no rotary"):
        base.rotary("volume", 1)
