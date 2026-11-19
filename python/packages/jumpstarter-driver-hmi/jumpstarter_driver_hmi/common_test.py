import json

import pytest

from .common import (
    BUTTON_PRIMARY,
    BUTTON_SECONDARY,
    COMMON_KEYS,
    DEFAULT_PRESSES,
    KIND_CONFIG,
    KIND_MEDIA,
    MAX_TEXT_CHARS,
    Capabilities,
    CodecLine,
    FrameParser,
    HardKey,
    InputError,
    KeyboardCaps,
    KeyboardEvent,
    KeyEvent,
    LineBuffer,
    LineTooLongError,
    PointerCaps,
    PointerEvent,
    QualityCaps,
    RotaryCaps,
    RotaryEvent,
    TextEvent,
    canonical_key,
    codec_family,
    pack_frame,
    pack_media,
    parse_input,
    split_media,
)

LINE = CodecLine(codec="avc1.64001F", width=1080, height=2400, fps=30)


def pointer(line: bytes | str) -> PointerEvent:
    """A line that is a pointer event, as one."""
    event = parse_input(line)
    assert isinstance(event, PointerEvent)
    return event


def test_frames_round_trip_however_they_are_chunked():
    wire = LINE.frame(7) + pack_media(1234, b"\x00\x00\x00\x01\x65abc") + pack_frame(9, b"later kind")
    parser = FrameParser()
    frames = []
    for i in range(0, len(wire), 3):
        frames += parser.feed(wire[i : i + 3])
    assert [k for k, _ in frames] == [KIND_CONFIG, KIND_MEDIA, 9]
    assert json.loads(frames[0][1])["viewer"] == 7
    assert split_media(frames[1][1]) == (1234, b"\x00\x00\x00\x01\x65abc")


def test_a_frame_length_that_cannot_be_right_is_refused():
    with pytest.raises(ValueError, match="out of step"):
        FrameParser().feed(b"\xff\xff\xff\xff\x01")
    with pytest.raises(ValueError, match="out of step"):
        FrameParser().feed(b"\x00\x00\x00\x00\x01")


def test_media_shorter_than_its_timestamp_is_refused():
    with pytest.raises(ValueError):
        split_media(b"\x00\x01")


def test_the_codec_line_has_the_required_fields_and_leaves_out_what_is_not_said():
    line = json.loads(LINE.to_json())
    assert line == {
        "codec": "avc1.64001F",
        "width": 1080,
        "height": 2400,
        "fps": 30,
        "format": "annexb",
        "timestamp": "us",
        "v": 1,
    }
    full = CodecLine(
        "avc1.64001F", 1206, 2622, 60, coded_width=1216, coded_height=2656, rotation=90, point_scale=3, input="stream"
    )
    said = json.loads(full.to_json(5))
    assert (said["coded_width"], said["rotation"], said["point_scale"], said["input"], said["viewer"]) == (
        1216,
        90,
        3,
        "stream",
        5,
    )


def test_the_shown_size_is_turned_by_the_rotation():
    assert CodecLine("avc1.42E01F", 100, 200).shown_size == (100, 200)
    assert CodecLine("avc1.42E01F", 100, 200, rotation=90).shown_size == (200, 100)
    assert CodecLine("avc1.42E01F", 100, 200, rotation=180).shown_size == (100, 200)
    assert CodecLine("avc1.42E01F", 100, 200, rotation=270).shown_size == (200, 100)


def test_only_a_new_codec_string_or_coded_size_restarts_the_decoder():
    base = CodecLine("avc1.64001F", 1080, 2400, 30)
    assert base.restarts_decoder(None)
    assert not base.restarts_decoder(base)
    assert not CodecLine("avc1.64001F", 1080, 2400, 60, rotation=90, input="stream").restarts_decoder(base)
    assert CodecLine("avc1.640020", 1080, 2400, 30).restarts_decoder(base)
    assert CodecLine("avc1.64001F", 1080, 2392, 30).restarts_decoder(base)
    assert CodecLine("avc1.64001F", 1080, 2392, 30, coded_width=1080, coded_height=2400).restarts_decoder(base) is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"codec": ""},
        {"codec": "avc1.\x01"},
        {"width": 0},
        {"height": 99999},
        {"width": True},
        {"fps": -1},
        {"rotation": 45},
        {"point_scale": 0},
        {"coded_width": 0},
    ],
)
def test_a_codec_line_that_cannot_be_right_is_refused(kwargs):
    base = {"codec": "avc1.64001F", "width": 10, "height": 10, "fps": 30}
    with pytest.raises(ValueError):
        CodecLine(**{**base, **kwargs})  # ty: ignore[invalid-argument-type]


def test_a_codec_line_is_read_from_a_sources_own_object():
    line = CodecLine.from_json({"codec": "avc1.64001F", "width": 4, "height": 6, "extra": 1, "viewer": 3})
    assert (line.width, line.height, line.fps) == (4, 6, 0)
    with pytest.raises(ValueError, match="no width"):
        CodecLine.from_json({"codec": "avc1.64001F", "height": 6})


def test_a_line_buffer_limits_whole_lines():
    lines = LineBuffer(limit=10)
    assert lines.feed(b"abc\n\n  de ") == [b"abc"]
    assert lines.feed(b"f\n") == [b"de f"]
    with pytest.raises(LineTooLongError) as caught:
        lines.feed(b"ok\n" + b"x" * 11 + b"\nafter\n")  # a long line complete in one chunk is refused too
    assert caught.value.lines == [b"ok", b"after"]
    with pytest.raises(LineTooLongError):
        lines.feed(b"y" * 11)  # one that never ends
    assert lines.feed(b'"}{"key":{"key":"POWER"}}\nfine\n') == [b"fine"]  # its tail is not a line of its own
    assert lines.feed(b"fine again\n") == [b"fine again"]


@pytest.mark.parametrize(
    ("name", "canonical"),
    [
        ("home", "HOME"),
        ("App-Switcher", "APP_SWITCH"),
        ("RECENTS", "APP_SWITCH"),
        ("keycode_back", "BACK"),
        ("siri", "ASSIST"),
        ("volume_mute", "MUTE"),
    ],
)
def test_key_names_are_compared_the_one_way(name, canonical):
    assert canonical_key(name) == canonical


@pytest.mark.parametrize("bad", [None, 3, "", "  "])
def test_a_key_name_must_be_a_name(bad):
    with pytest.raises(ValueError):
        canonical_key(bad)


def test_the_common_vocabulary_is_canonical():
    assert all(canonical_key(k) == k for k in COMMON_KEYS)


def test_hard_keys_from_a_config():
    assert HardKey.parse("home") == HardKey("HOME", "Home")
    assert HardKey.parse({"key": "volume-up", "label": "Vol +", "presses": ["short"]}) == HardKey(
        "VOLUME_UP", "Vol +", ("short",)
    )
    for bad in (
        3,
        {"label": "x"},
        {"key": "HOME", "presses": ["tap"]},
        {"key": "HOME", "extra": 1},
        {"key": "HOME", "label": ""},
    ):
        with pytest.raises(ValueError):
            HardKey.parse(bad)


def test_capabilities_say_what_a_ui_shows():
    caps = Capabilities(
        pointer=PointerCaps.touch(),
        keyboard=KeyboardCaps(text=True),
        keys=("HOME",),
        hard_keys=(HardKey("HOME", "Home"),),
        quality=QualityCaps(),
        keyframe_cost="cheap",
    )
    info = caps.info(width=10, height=20)
    assert info["interface"] == "hmi"
    assert info["input"] == {
        "pointer": {
            "kinds": ["touch"],
            "absolute": True,
            "relative": False,
            "buttons": False,
            "wheel": False,
            "max_contacts": 1,
        },
        "keyboard": {"codes": [], "text": True, "layout": "us"},
        "keys": ["HOME"],
        "hard_keys": [{"key": "HOME", "label": "Home", "presses": ["short", "long"]}],
        "rotaries": [],
        "clipboard": False,
    }
    assert info["quality"] == {"adjustable": True, "auto": True, "levels": ["minimal", "low", "medium", "high"]}
    assert (info["codecs"], info["codec"], info["recording"]) == (["h264"], "h264", True)


def test_a_view_only_hmi_declares_no_input_and_nothing_to_show():
    declared = Capabilities().info(width=1, height=1)
    assert declared["input"] == {
        "pointer": {
            "kinds": [],
            "absolute": False,
            "relative": False,
            "buttons": False,
            "wheel": False,
            "max_contacts": 0,
        },
        "keyboard": {"codes": [], "text": False, "layout": None},
        "keys": [],
        "hard_keys": [],
        "rotaries": [],
        "clipboard": False,
    }
    assert not Capabilities().has_input
    assert declared["quality"] == {"adjustable": False, "auto": False, "levels": []}


def test_a_mouse_and_a_keyboard_are_declared():
    caps = Capabilities(
        codecs=("h264", "hevc", "mjpeg"),
        pointer=PointerCaps.mouse(relative=True),
        keyboard=KeyboardCaps(codes="all", text=True, layout="de"),
    )
    info = caps.info(width=800, height=600, codec="mjpeg")
    assert info["input"]["pointer"] == {
        "kinds": ["mouse"],
        "absolute": True,
        "relative": True,
        "buttons": True,
        "wheel": True,
        "max_contacts": 1,
    }
    assert info["input"]["keyboard"] == {"codes": "all", "text": True, "layout": "de"}
    assert (info["codecs"], info["codec"], info["recording"]) == (["h264", "hevc", "mjpeg"], "mjpeg", False)
    assert caps.has_input
    assert KeyboardCaps(codes=("KeyA", "Enter")).allows("KeyA")
    assert not KeyboardCaps(codes=("KeyA",)).allows("KeyB")
    assert KeyboardCaps(codes="all").allows("F13")
    assert not KeyboardCaps(codes="all").allows("not a code")


@pytest.mark.parametrize(
    "make",
    [
        lambda: Capabilities(codecs=()),
        lambda: Capabilities(codecs=("h264", "h264")),
        lambda: Capabilities(codecs=("vp9",)),
        lambda: Capabilities(keyframe_cost="free"),
        lambda: PointerCaps(kinds=("trackball",), max_contacts=1),
        lambda: PointerCaps(kinds=("touch",), max_contacts=0),
        lambda: PointerCaps(kinds=("touch",), max_contacts=33),
        lambda: PointerCaps(kinds=("mouse",), absolute=False, relative=False, max_contacts=1),
        lambda: KeyboardCaps(codes="some"),
        lambda: KeyboardCaps(codes=("Key A",)),
        lambda: KeyboardCaps(codes=(1,)),  # ty: ignore[invalid-argument-type]
    ],
)
def test_capabilities_refuse_what_the_interface_does_not_have(make):
    with pytest.raises(ValueError):
        make()


@pytest.mark.parametrize(
    ("codec", "family"),
    [
        ("avc1.64001F", "h264"),
        ("avc3.640028", "h264"),
        ("hev1.1.6.L93.B0", "hevc"),
        ("hvc1.2.4.L120.B0", "hevc"),
        ("mjpeg", "mjpeg"),
    ],
)
def test_a_codec_string_has_a_family(codec, family):
    assert codec_family(codec) == family
    assert CodecLine(codec, 10, 10).family == family


def test_a_codec_the_interface_does_not_know_is_refused():
    for codec in ("vp9", "av01.0.04M.08", "avc1", "h264"):
        with pytest.raises(ValueError):
            codec_family(codec)
        with pytest.raises(ValueError):
            CodecLine(codec, 10, 10)


def test_the_codec_line_says_how_the_units_are_written():
    assert json.loads(CodecLine("hev1.1.6.L93.B0", 10, 10, 30).to_json())["format"] == "annexb"
    mjpeg = json.loads(CodecLine("mjpeg", 10, 10, 30).to_json())
    assert (mjpeg["codec"], mjpeg["format"]) == ("mjpeg", "jpeg")
    assert CodecLine("mjpeg", 10, 10).restarts_decoder(CodecLine("avc1.64001F", 10, 10))


def test_a_touch_line_is_a_pointer_of_kind_touch():
    event = parse_input(b'{"touch": {"type": "down", "x": 10.5, "y": 20, "id": 0, "w": 100, "h": 200}}')
    assert event == PointerEvent("down", "touch", 10.5, 20.0, 0, None, 0.0, 0.0, (100, 200))
    assert parse_input('{"touch": {"type": "move", "x": 1, "y": 2}}') == PointerEvent("move", "touch", 1.0, 2.0)
    same = parse_input('{"pointer": {"type": "move", "kind": "touch", "x": 1, "y": 2}}')
    assert same == parse_input('{"touch": {"type": "move", "x": 1, "y": 2}}')


def test_a_mouse_line_has_buttons_and_a_wheel():
    down = parse_input('{"pointer": {"type": "down", "kind": "mouse", "x": 5, "y": 6, "buttons": 3}}')
    assert down == PointerEvent("down", "mouse", 5.0, 6.0, 0, BUTTON_PRIMARY | BUTTON_SECONDARY)
    wheel = pointer('{"pointer": {"type": "wheel", "kind": "mouse", "x": 5, "y": 6, "dx": -2, "dy": 120.5}}')
    assert (wheel.type, wheel.dx, wheel.dy, wheel.x, wheel.y) == ("wheel", -2.0, 120.5, 5.0, 6.0)
    relative = pointer('{"pointer": {"type": "move", "kind": "mouse", "dx": 3, "dy": -4}}')
    assert (relative.x, relative.y, relative.dx, relative.dy) == (None, None, 3.0, -4.0)
    assert pointer('{"pointer": {"type": "down", "kind": "pen", "x": 1, "y": 2}}').kind == "pen"


def test_a_cancel_is_read_as_an_up():
    assert pointer('{"touch": {"type": "cancel", "x": 1, "y": 2}}').type == "up"
    assert pointer('{"pointer": {"type": "cancel", "kind": "pen", "x": 1, "y": 2}}').type == "up"


def test_a_contact_id_is_kept_for_the_exporter_to_judge():
    assert pointer('{"touch": {"type": "down", "x": 1, "y": 2, "id": 3}}').id == 3
    with pytest.raises(InputError, match="touch.id"):
        parse_input('{"touch": {"type": "down", "x": 1, "y": 2, "id": "a"}}')


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"[1]",
        b'{"touch": {}, "key": {}}',
        b'{"scroll": {}}',
        b'{"touch": {"type": "tap", "x": 1, "y": 2}}',
        b'{"touch": {"type": "down", "x": "a", "y": 2}}',
        b'{"touch": {"type": "down", "x": 1e999, "y": 2}}',
        b'{"touch": {"type": "down", "x": true, "y": 2}}',
        b'{"touch": {"type": "down", "x": 1}}',
        b'{"touch": {"type": "move", "dx": 1, "dy": 2, "kind": "trackball"}}',
        b'{"pointer": {"type": "down", "x": 1, "y": 2}}',
        b'{"pointer": {"type": "down", "kind": "trackball", "x": 1, "y": 2}}',
        b'{"pointer": {"type": "down", "kind": "mouse", "x": 1, "y": 2, "buttons": 64}}',
        b'{"pointer": {"type": "down", "kind": "mouse", "x": 1, "y": 2, "buttons": -1}}',
        b'{"pointer": {"type": "down", "kind": "mouse", "x": 1, "y": 2, "buttons": true}}',
        b'{"pointer": {"type": "wheel", "kind": "mouse", "x": 1, "y": 2, "dx": "a"}}',
        b'{"pointer": {"type": "move", "kind": "mouse"}}',
        b'{"keyboard": {"code": "KeyA"}}',
        b'{"keyboard": {"code": "Key A", "type": "down"}}',
        b'{"keyboard": {"code": 5, "type": "down"}}',
        b'{"keyboard": {"code": "KeyA", "type": "press"}}',
        b'{"key": {"press": "short"}}',
        b'{"key": {"key": "HOME", "press": "tap"}}',
        b'{"key": {"key": "A", "code": "KeyA", "type": "down"}}',
        b'{"key": {"text": "hi"}}',
        b'{"text": 5}',
    ],
)
def test_a_wrong_line_is_an_input_error(line):
    with pytest.raises(InputError):
        parse_input(line)


def test_keyboard_lines_are_codes_going_down_and_up():
    assert parse_input('{"keyboard": {"code": "KeyA", "type": "down"}}') == KeyboardEvent("KeyA", True)
    assert parse_input('{"keyboard": {"code": "ShiftLeft", "type": "up"}}') == KeyboardEvent("ShiftLeft", False)
    assert parse_input('{"keyboard": {"code": "F13", "type": "down"}}') == KeyboardEvent("F13", True)


def test_keys_and_text():
    assert parse_input('{"key": {"key": "app-switcher"}}') == KeyEvent("APP_SWITCH", "short")
    assert parse_input('{"key": {"key": "power", "press": "long"}}') == KeyEvent("POWER", "long")
    assert parse_input('{"text": "héllo"}') == TextEvent("héllo")


def test_text_is_limited():
    with pytest.raises(InputError, match="longer"):
        parse_input(json.dumps({"text": "x" * (MAX_TEXT_CHARS + 1)}))


@pytest.mark.parametrize("aimed", [{"w": "100", "h": 200}, {"w": 100}, {"h": 200}, {"w": None, "h": 5}])
def test_an_event_that_names_its_picture_badly_is_refused_not_unchecked(aimed):
    with pytest.raises(InputError, match="touch.w and touch.h"):
        parse_input(json.dumps({"touch": {"type": "down", "x": 1, "y": 2, **aimed}}))
    with pytest.raises(InputError, match="pointer.w and pointer.h"):
        parse_input(json.dumps({"pointer": {"type": "down", "kind": "mouse", "x": 1, "y": 2, **aimed}}))


def test_a_rotary_is_declared_with_its_push_button_among_the_keys():
    caps = Capabilities(
        keys=("HOME", "VOLUME_PUSH"),
        rotaries=(RotaryCaps("volume", "Volume", "volume-push"), RotaryCaps("tune-knob")),
    )
    assert caps.has_input
    assert caps.info(width=1, height=1)["input"]["rotaries"] == [
        {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"},
        {"id": "tune-knob", "label": "Tune Knob", "push": None},
    ]
    assert Capabilities(rotaries=(RotaryCaps("knob"),)).has_input  # a rotary alone is input


@pytest.mark.parametrize(
    "make",
    [
        lambda: RotaryCaps("1knob"),
        lambda: RotaryCaps("a knob"),
        lambda: RotaryCaps(""),
        lambda: RotaryCaps("knob", 5),  # ty: ignore[invalid-argument-type]
        lambda: Capabilities(rotaries=(RotaryCaps("knob"), RotaryCaps("knob"))),
        lambda: Capabilities(keys=("HOME",), rotaries=(RotaryCaps("knob", push="KNOB_PUSH"),)),
    ],
)
def test_a_rotary_that_cannot_be_right_is_refused(make):
    with pytest.raises(ValueError):
        make()


def test_a_rotary_from_an_exporters_config():
    assert RotaryCaps.parse("volume") == RotaryCaps("volume")
    assert RotaryCaps.parse({"id": "volume", "label": "Vol", "push": "volume-push"}) == RotaryCaps(
        "volume", "Vol", "VOLUME_PUSH"
    )
    for bad in (3, {"label": "x"}, {"id": "a", "extra": 1}):
        with pytest.raises(ValueError):
            RotaryCaps.parse(bad)


def test_a_rotary_line_turns_by_detents():
    assert parse_input('{"rotary": {"id": "volume", "delta": -2}}') == RotaryEvent("volume", -2)
    assert parse_input('{"rotary": {"id": "tune-knob", "delta": 100}}') == RotaryEvent("tune-knob", 100)


@pytest.mark.parametrize(
    "line",
    [
        b'{"rotary": {"id": "volume"}}',
        b'{"rotary": {"id": "volume", "delta": 0}}',
        b'{"rotary": {"id": "volume", "delta": 101}}',
        b'{"rotary": {"id": "volume", "delta": 1.5}}',
        b'{"rotary": {"id": "volume", "delta": true}}',
        b'{"rotary": {"id": "volume", "delta": "1"}}',
        b'{"rotary": {"id": "no good", "delta": 1}}',
        b'{"rotary": {"delta": 1}}',
        b'{"rotary": 3}',
    ],
)
def test_a_wrong_rotary_line_is_an_input_error(line):
    with pytest.raises(InputError):
        parse_input(line)


def test_a_hard_key_takes_short_and_long_presses_unless_it_says():
    assert HardKey("HOME", "Home").presses == DEFAULT_PRESSES == ("short", "long")
    assert HardKey.parse("home").presses == ("short", "long")
    assert HardKey.parse({"key": "home", "presses": ["short"]}).presses == ("short",)
