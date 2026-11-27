"""The framed protocol's reader: what scrios writes, however the pipe chunks it."""

import json
import struct

import pytest

from .scrios_framed import (
    CODEC_FAMILIES,
    FLAG_CONTROL,
    FLAG_KEY,
    FLAG_PARAMS,
    MAX_PAYLOAD,
    PROTOCOL,
    Config,
    Event,
    Fatal,
    FrameError,
    Item,
    Problem,
    ScriosStream,
    Unit,
    pack_event,
    pack_frame,
)

CONFIG = {
    "codec": "avc1.640033",
    "width": 1206,
    "height": 2622,
    "coded_width": 1216,
    "coded_height": 2656,
    "fps": 60,
    "clock_hz": 24000,
    "clock_source": "default",
    "ssrc": 123,
    "device_ssrc": 456,
    "rotation": 0,
    "point_scale": 3.0,
    "current_orientation": "rot0",
    "native_orientation": "rot0",
    "protocol": PROTOCOL,
}
KEY_UNIT = b"\x00\x00\x00\x01\x67aa\x00\x00\x00\x01\x68bb\x00\x00\x00\x01\x65cc"
DELTA_UNIT = b"\x00\x00\x00\x01\x41dd"
#: HEVC units: a VPS, an SPS and a PPS and an IDR picture (NAL types 32, 33, 34 and 19), and a trailing picture (1)
HEVC_KEY_UNIT = (
    b"\x00\x00\x00\x01\x40\x01aa\x00\x00\x00\x01\x42\x01bb\x00\x00\x00\x01\x44\x01cc\x00\x00\x00\x01\x26\x01dd"
)
HEVC_DELTA_UNIT = b"\x00\x00\x00\x01\x02\x01ee"


def config(**changes) -> bytes:
    fields = {**CONFIG, **changes}
    return pack_event(event="config", **{k: v for k, v in fields.items() if v is not None})


def stream_of(*frames: bytes) -> bytes:
    return b"".join(frames)


def as_config(item: Item) -> Config:
    assert isinstance(item, Config), item
    return item


def as_unit(item: Item) -> Unit:
    assert isinstance(item, Unit), item
    return item


def as_event(item: Item) -> Event:
    assert isinstance(item, Event), item
    return item


def message_of(item: Item) -> str:
    assert isinstance(item, Problem | Fatal), item
    return item.message


# -- frames ------------------------------------------------------------------------------------------------------------


def test_a_config_then_units_are_decoded():
    stream = ScriosStream()
    items = stream.feed(
        stream_of(
            config(),
            pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS),
            pack_frame(DELTA_UNIT, 16_667, 0),
        )
    )
    assert [type(i) for i in items] == [Config, Unit, Unit]
    first = as_config(items[0])
    assert first.line.codec == "avc1.640033"
    assert (first.line.width, first.line.height) == (1206, 2622)
    assert (first.line.coded_width, first.line.coded_height) == (1216, 2656)
    assert first.line.fps == 60 and first.line.rotation == 0 and first.line.point_scale == 3.0
    assert first.labels == {"current_orientation": "rot0", "native_orientation": "rot0", "clock_source": "default"}
    assert first.protocol == PROTOCOL
    assert items[1] == Unit(0, True, True, KEY_UNIT)
    assert items[2] == Unit(16_667, False, False, DELTA_UNIT)
    assert stream.configured and stream.frames == 3 and stream.buffered == 0


@pytest.mark.parametrize("size", [1, 2, 3, 7, 13, 64, 10_000])
def test_any_chunking_gives_the_same_items(size):
    data = stream_of(config(), pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS), pack_event(event="stats", packets=3))
    data += stream_of(*[pack_frame(DELTA_UNIT, n) for n in range(1, 6)])
    whole = ScriosStream().feed(data)
    stream, pieces = ScriosStream(), []
    for at in range(0, len(data), size):
        pieces += stream.feed(data[at : at + size])
    assert pieces == whole
    assert len(whole) == 8 and stream.buffered == 0


def test_a_header_that_arrives_in_pieces_is_waited_for():
    stream = ScriosStream()
    stream.feed(config())
    frame = pack_frame(DELTA_UNIT, 5)
    assert stream.feed(frame[:5]) == []
    assert stream.buffered == 5
    assert stream.feed(frame[5:12]) == []
    assert stream.feed(frame[12:]) == [Unit(5, False, False, DELTA_UNIT)]


def test_an_empty_payload_is_a_unit_of_no_bytes():
    stream = ScriosStream()
    stream.feed(config())
    assert stream.feed(pack_frame(b"", 3)) == [Unit(3, False, False, b"")]


def test_a_length_over_the_limit_is_an_error_that_keeps_what_came_before_it():
    stream = ScriosStream()
    good = stream_of(config(), pack_frame(KEY_UNIT, 0, FLAG_KEY))
    with pytest.raises(FrameError, match="out of step") as raised:
        stream.feed(good + struct.pack(">IQB", MAX_PAYLOAD + 1, 0, 0))
    assert [type(i) for i in raised.value.items] == [Config, Unit]


def test_the_limit_is_exact_and_can_be_lowered():
    stream = ScriosStream(max_payload=400)
    stream.feed(config())
    assert as_unit(stream.feed(pack_frame(b"x" * 400, 1))[0]).payload == b"x" * 400
    with pytest.raises(FrameError):
        stream.feed(pack_frame(b"x" * 401, 2))
    assert ScriosStream().feed(struct.pack(">IQB", 5, 0, 0)) == []  # a length that fits is waited for


def test_a_frame_of_two_gib_is_refused_at_its_header_without_waiting_for_a_payload():
    with pytest.raises(FrameError):
        ScriosStream().feed(struct.pack(">IQB", 0x7FFFFFFF, 0, 0))


# -- control ----------------------------------------------------------------------------------------------------------


def test_the_other_events_are_passed_on_with_their_fields():
    stream = ScriosStream()
    items = stream.feed(
        stream_of(
            config(),
            pack_event(event="stats", packets=5, dropped_units=2),
            pack_event(event="hid", ready=True, keyboard=True, mapping="auto"),
            pack_event(event="error", message="boom"),
            pack_event(event="stopped"),
            pack_event(event="something-new", anything=1),
        )
    )
    events = [as_event(i) for i in items[1:]]
    assert [(e.name, e.data.get("packets")) for e in events[:2]] == [("stats", 5), ("hid", None)]
    assert events[1].data == {"event": "hid", "ready": True, "keyboard": True, "mapping": "auto"}
    assert [e.name for e in events] == ["stats", "hid", "error", "stopped", "something-new"]


def test_a_control_frame_that_is_not_json_is_a_problem_and_the_stream_goes_on():
    stream = ScriosStream()
    items = stream.feed(
        stream_of(
            config(),
            pack_frame(b"this is not json", 0, FLAG_CONTROL),
            pack_frame(b"[1, 2]", 0, FLAG_CONTROL),
            pack_frame(b'{"no": "event"}', 0, FLAG_CONTROL),
            pack_frame(b"\xff\xfe", 0, FLAG_CONTROL),
            pack_frame(DELTA_UNIT, 1),
        )
    )
    assert [type(i) for i in items] == [Config, Problem, Problem, Problem, Problem, Unit]
    assert stream.bad_control == 4
    assert "not a JSON object" in message_of(items[1])


def test_a_control_frame_that_is_far_too_large_is_not_parsed():
    stream = ScriosStream()
    huge = json.dumps({"event": "stats", "pad": "x" * 100_000}).encode()
    assert isinstance(stream.feed(pack_frame(huge, 0, FLAG_CONTROL))[0], Problem)


def test_flags_that_scrios_does_not_define_are_ignored():
    stream = ScriosStream()
    stream.feed(config())
    assert stream.feed(pack_frame(DELTA_UNIT, 1, 0x04))[0] == Unit(1, False, False, DELTA_UNIT)


# -- the config -------------------------------------------------------------------------------------------------------


def test_a_second_config_is_passed_on_each_time():
    stream = ScriosStream()
    first = stream.feed(config())
    second = stream.feed(config(width=1000, height=2000, rotation=90))
    assert isinstance(first[0], Config)
    assert as_config(second[0]).line.shown_size == (2000, 1000)


def test_a_config_without_the_optional_fields_has_none_of_them():
    items = ScriosStream().feed(
        config(rotation=None, point_scale=None, coded_width=None, coded_height=None, current_orientation=None)
    )
    line = as_config(items[0]).line
    assert line.rotation is None and line.point_scale is None and line.coded_width is None
    assert as_config(items[0]).labels["current_orientation"] is None


def test_numbers_that_were_sent_as_floats_are_whole_numbers():
    line = as_config(
        ScriosStream().feed(config(width=1206.0, height=2622.0, coded_width=1216.0, rotation=90.0))[0]
    ).line
    assert (line.width, line.height, line.coded_width, line.rotation) == (1206, 2622, 1216, 90)
    assert isinstance(line.width, int)


@pytest.mark.parametrize(
    "changes",
    [
        {"width": 0},
        {"height": -1},
        {"width": "wide"},
        {"width": None},
        {"codec": ""},
        {"codec": 5},
        {"rotation": 45},
        {"point_scale": 0},
        {"fps": -1},
        {"fps": "fast"},
    ],
)
def test_a_config_that_cannot_be_right_is_a_problem_and_no_picture_is_served_after_it(changes):
    stream = ScriosStream()
    items = stream.feed(stream_of(config(**changes), pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS)))
    assert [type(i) for i in items] == [Problem, Problem]
    assert "config" in message_of(items[0])
    assert not stream.configured and stream.bad_control == 1 and stream.units_before_config == 1


def test_a_labels_that_is_not_a_short_printable_string_is_none():
    items = ScriosStream().feed(config(current_orientation="x" * 100, native_orientation=5, clock_source="a\nb"))
    assert as_config(items[0]).labels == {"current_orientation": None, "native_orientation": None, "clock_source": None}


# -- the version and the codec -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("protocol", [2, 0, 1.5, "1", True, None])
def test_another_protocol_is_fatal_and_nothing_is_decoded_after_it(protocol):
    stream = ScriosStream()
    items = stream.feed(
        stream_of(
            config(protocol=protocol),
            pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS),
            pack_event(event="stats"),
            config(),
        )
    )
    assert len(items) == 1 and isinstance(items[0], Fatal)
    assert "protocol" in message_of(items[0])
    assert not stream.configured


def test_the_fatal_message_names_both_versions():
    items = ScriosStream().feed(config(protocol=2))
    assert "2" in message_of(items[0]) and str(PROTOCOL) in message_of(items[0])
    assert "no protocol number" in message_of(ScriosStream().feed(config(protocol=None))[0])


def test_h264_and_hevc_are_the_codecs_scrios_streams():
    assert CODEC_FAMILIES == ("h264", "hevc")


@pytest.mark.parametrize("codec", ["avc1.640033", "avc3.640033", "AVC1.64001F"])
def test_h264_is_taken(codec):
    items = ScriosStream().feed(config(codec=codec))
    assert as_config(items[0]).line.codec == codec and as_config(items[0]).line.family == "h264"


@pytest.mark.parametrize("codec", ["hev1.1.6.L150.B0", "hvc1.1.6.L93.B0", "hev1.2.4.L120.90", "HEV1.1.6.L93.B0"])
def test_hevc_is_taken_and_the_codec_line_names_what_scrios_says(codec):
    stream = ScriosStream()
    items = stream.feed(
        stream_of(
            config(codec=codec),
            pack_frame(HEVC_KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS),
            pack_frame(HEVC_DELTA_UNIT, 16_667, 0),
        )
    )
    assert [type(i) for i in items] == [Config, Unit, Unit]
    line = as_config(items[0]).line
    assert line.codec == codec and line.family == "hevc" and line.format == "annexb"
    assert (line.width, line.height) == (1206, 2622) and line.fps == 60
    assert items[1] == Unit(0, True, True, HEVC_KEY_UNIT)  # the units are passed on as they are
    assert items[2] == Unit(16_667, False, False, HEVC_DELTA_UNIT)
    assert stream.configured


@pytest.mark.parametrize("codec", ["vp09.00.10.08", "av01.0.04M.08", "mjpeg", "h264", "hevc", "mp4a.40.2"])
def test_any_other_codec_is_fatal_and_the_message_names_it(codec):
    stream = ScriosStream()
    items = stream.feed(stream_of(config(codec=codec), pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS), config()))
    assert len(items) == 1 and isinstance(items[0], Fatal)
    assert codec in message_of(items[0]) and "neither H.264 nor HEVC" in message_of(items[0])
    assert not stream.configured  # nothing is decoded after it, not even a good config


def test_a_codec_that_is_not_a_codec_string_is_a_config_that_cannot_be_right_not_a_codec_to_refuse():
    for codec in ("", 5, None, "x" * 65, "avc1.\u00e9"):
        items = ScriosStream().feed(config(codec=codec))
        assert [type(i) for i in items] == [Problem], codec


def test_without_an_expectation_either_codec_may_come_and_change():
    stream = ScriosStream()
    assert as_config(stream.feed(config(codec="avc1.640033"))[0]).line.family == "h264"
    assert as_config(stream.feed(config(codec="hev1.1.6.L150.B0"))[0]).line.family == "hevc"


@pytest.mark.parametrize(
    ("expect", "codec", "named", "asked"),
    [("h264", "hev1.1.6.L150.B0", "HEVC", "H.264"), ("hevc", "avc1.640033", "H.264", "HEVC")],
)
def test_the_other_codec_than_the_one_asked_for_is_fatal(expect, codec, named, asked):
    stream = ScriosStream(expect=expect)
    items = stream.feed(stream_of(config(codec=codec), pack_frame(KEY_UNIT, 0, FLAG_KEY | FLAG_PARAMS)))
    assert len(items) == 1 and isinstance(items[0], Fatal)
    assert f"scrios streams {named} ({codec}), though {asked} was asked for" == message_of(items[0])
    assert not stream.configured


@pytest.mark.parametrize(("expect", "codec"), [("h264", "avc1.640033"), ("hevc", "hvc1.1.6.L93.B0")])
def test_the_codec_asked_for_is_taken(expect, codec):
    assert as_config(ScriosStream(expect=expect).feed(config(codec=codec))[0]).line.codec == codec


@pytest.mark.parametrize("expect", ["mjpeg", "h265", "", "avc1"])
def test_a_codec_that_scrios_does_not_make_cannot_be_asked_for(expect):
    with pytest.raises(ValueError, match="scrios streams h264 or hevc"):
        ScriosStream(expect=expect)


def test_the_codec_is_checked_in_every_config_not_only_the_first():
    stream = ScriosStream(expect="hevc")
    assert isinstance(stream.feed(config(codec="hev1.1.6.L150.B0"))[0], Config)
    items = stream.feed(config(codec="avc1.640033"))  # a later config that changes the codec
    assert isinstance(items[0], Fatal)


# -- pictures before the config ----------------------------------------------------------------------------------------


def test_units_before_the_config_are_dropped_and_the_first_is_reported_once():
    stream = ScriosStream()
    items = stream.feed(stream_of(*[pack_frame(DELTA_UNIT, n) for n in range(5)]))
    assert [type(i) for i in items] == [Problem]
    assert "before its config" in message_of(items[0])
    assert stream.units_before_config == 5 and not stream.configured
    items = stream.feed(stream_of(config(), pack_frame(KEY_UNIT, 9, FLAG_KEY | FLAG_PARAMS)))
    assert [type(i) for i in items] == [Config, Unit]
    assert stream.units_before_config == 5


def test_events_before_the_config_are_passed_on():
    items = ScriosStream().feed(pack_event(event="hid", ready=False, message="no"))
    assert as_event(items[0]).name == "hid"
