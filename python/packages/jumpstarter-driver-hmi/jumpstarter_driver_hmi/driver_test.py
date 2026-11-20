import json
import os
import tempfile
import threading
import time
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

from .client import _STREAM_ENDS, HmiClient, HmiStreamClosedError
from .common import CodecLine, HardKey, InputError, KeyboardCaps, PointerCaps, PointerEvent
from .driver import MAX_DELTA_PX, MAX_KEYS_DOWN, MockHmi, _InputState, _Session
from .hub import Hub
from .hub.codecs import NAL_IDR, first_sps, has_idr, nal_types
from .testing import ALL_EVENTS, FakeSource, HmiConformance, hevc_key_unit, make_hevc_sps, make_jpeg
from .testing.conformance import media, settle, wait_for
from .testing.pattern import pattern_units
from jumpstarter.client.core import DriverError
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

#: An SPS, a PPS and an IDR slice: a key access unit.
KEY_UNIT = (
    b"\x00\x00\x00\x01\x67\x42\xc0\x1f\xac\xd9\x40\x10\x00\x00\x00\x01\x68\xce\x3c\x80\x00\x00\x00\x01\x65\x88\x84aaaa"
)


class MockConformance(HmiConformance):
    """The suite every implementation passes, on the mock: ``options`` say which mock."""

    options: ClassVar[dict] = {}
    #: the mock's fake device says everything it was given
    reports = ALL_EVENTS

    def make_driver(self):
        options: dict[str, Any] = {"width": 240, "height": 400, "fps": 60, "gop": 30, "idle_stop_s": 0, **self.options}
        return MockHmi(**options)

    def input_events(self, driver):
        return source(driver).sink_input.snapshot()

    def change_picture(self, driver):
        if self.options.get("pattern"):
            return False  # the real pattern has one size
        source(driver).change_picture(200, 300, rotation=90)
        return True


class TestMockHmi(MockConformance):
    """The mock, a touch screen with hard keys that offers every codec, is an HMI."""


class TestMockHmiMouse(MockConformance):
    """So is a mouse and keyboard, with buttons and a wheel and no hard keys."""

    options: ClassVar[dict] = {"mode": "mouse"}


class TestMockHmiRelativeMouse(MockConformance):
    """A mouse that takes motion by a distance too."""

    options: ClassVar[dict] = {"mode": "mouse", "relative": True}


class TestMockHmiViewOnly(MockConformance):
    """And one that declares no input at all: the suite checks it is a valid, view-only HMI."""

    options: ClassVar[dict] = {"control": False}


class TestMockHmiHevc(MockConformance):
    """In HEVC from the start."""

    options: ClassVar[dict] = {"codec": "hevc"}


class TestMockHmiMjpeg(MockConformance):
    """In Motion JPEG from the start: every picture is a key picture, and nothing is recorded."""

    options: ClassVar[dict] = {"codec": "mjpeg", "mode": "mouse"}


class TestMockHmiOneCodec(MockConformance):
    """An HMI that offers one codec only."""

    options: ClassVar[dict] = {"codecs": ["hevc"]}


class TestMockHmiRotaries(MockConformance):
    """A touch screen with a knob that can be pushed and one that cannot."""

    options: ClassVar[dict] = {"rotaries": ["tune", {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"}]}


class TestMockHmiMouseRotary(MockConformance):
    """A mouse and keyboard with a knob; its push button is the only hard key."""

    options: ClassVar[dict] = {"mode": "mouse", "rotaries": [{"id": "dial", "push": "ENTER"}]}


class TestMockHmiRotaryWithPresses(MockConformance):
    """A knob whose push button takes only short and long (the default) and one key that takes all four presses."""

    options: ClassVar[dict] = {
        "rotaries": [{"id": "volume", "push": "VOLUME_PUSH"}],
        "hard_keys": ["HOME", {"key": "VOLUME_PUSH", "presses": ["down", "up"]}],
    }


class TestMockHmiViewOnlyWithRotaries(MockConformance):
    """A view-only HMI declares no rotary even when its source has one."""

    options: ClassVar[dict] = {"control": False, "rotaries": ["volume"]}


class TestMockHmiKeyPictureCostSaidAtStart(MockConformance):
    """A source that cannot be asked for a key picture until it has started, and then can."""

    options: ClassVar[dict] = {"keyframe_cost": "none", "start_keyframe_cost": "cheap"}


class TestMockHmiKeyPictureRestartsTheCapture(MockConformance):
    """And one whose key picture costs a restart of the capture, said at start."""

    options: ClassVar[dict] = {"keyframe_cost": "cheap", "start_keyframe_cost": "restart"}


@pytest.fixture
def driver():
    driver = MockHmi(width=240, height=400, fps=60, gop=30, idle_stop_s=0)
    yield driver
    driver.close()


@pytest.fixture
def hmi(driver):
    with serve(driver) as client:
        yield client


def source(driver) -> FakeSource:
    return cast(FakeSource, driver._the_source())


# -- configuration -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        {"max_size": 0},
        {"bit_rate": True},
        {"max_fps": -3},
        {"idle_stop_s": -1},
        {"stall_s": "x"},
        {"start_timeout_s": 0},
        {"control": "yes"},
        {"hard_keys": "HOME"},
        {"hard_keys": [3]},
        {"hard_keys": [{"key": "HOME", "presses": ["tap"]}]},
    ],
)
def test_a_wrong_option_is_a_configuration_error(options):
    with pytest.raises(ConfigurationError):
        MockHmi(**options)


def test_nothing_is_started_by_building_the_driver():
    driver = MockHmi()
    assert driver._source is None
    assert driver.status()["state"] == "stopped"


# -- the mock's own options: mode, relative, codecs, codec and pattern -------------------------------------------------


@pytest.mark.parametrize(
    "options",
    [
        {"mode": "pen"},
        {"mode": "Touch"},
        {"mode": None},
        {"relative": True},
        {"mode": "touch", "relative": True},
        {"mode": "mouse", "relative": "yes"},
        {"codecs": []},
        {"codecs": "h264"},
        {"codecs": ["vp9"]},
        {"codecs": ["h264", "h264"]},
        {"codecs": [264]},
        {"codec": "vp9"},
        {"codec": "hevc", "codecs": ["h264"]},
        {"pattern": 1},
        {"keyframe_cost": "free"},
        {"start_keyframe_cost": "free"},
        {"rotaries": "volume"},
        {"rotaries": [3]},
        {"rotaries": ["not an id"]},
        {"rotaries": [{"label": "Volume"}]},
        {"rotaries": [{"id": "volume", "detents": 24}]},
        {"rotaries": [{"id": "volume", "label": 3}]},
        {"rotaries": ["volume", "volume"]},
        {"rotaries": ["volume", {"id": "volume", "push": "VOLUME_PUSH"}]},
    ],
)
def test_a_wrong_mock_option_is_a_configuration_error(options):
    with pytest.raises(ConfigurationError):
        MockHmi(**options)


def test_the_mock_is_a_touch_screen_that_offers_every_codec_by_default():
    driver = MockHmi()
    try:
        info = driver.hmi_info()
        assert (info["codecs"], info["codec"], info["recording"]) == (["h264", "hevc", "mjpeg"], "h264", True)
        assert info["input"]["pointer"] == {
            "kinds": ["touch"],
            "absolute": True,
            "relative": False,
            "buttons": False,
            "wheel": False,
            "max_contacts": 1,
        }
        assert info["input"]["keyboard"] == {"codes": [], "text": True, "layout": "us"}
        assert info["input"]["keys"] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"]
        assert [k["key"] for k in info["input"]["hard_keys"]] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]
    finally:
        driver.close()


def test_the_mock_can_be_a_mouse_and_keyboard():
    driver = MockHmi(mode="mouse")
    try:
        input_ = driver.hmi_info()["input"]
        assert input_["pointer"] == {
            "kinds": ["mouse"],
            "absolute": True,
            "relative": False,
            "buttons": True,
            "wheel": True,
            "max_contacts": 1,
        }
        assert input_["keyboard"] == {"codes": "all", "text": True, "layout": "us"}
        assert input_["keys"] == [] and input_["hard_keys"] == []
    finally:
        driver.close()


def test_a_mouse_can_take_relative_motion_too():
    driver = MockHmi(mode="mouse", relative=True)
    try:
        pointer = driver.hmi_info()["input"]["pointer"]
        assert pointer["relative"] is True and pointer["absolute"] is True
    finally:
        driver.close()


def test_the_mock_has_no_rotary_unless_it_is_configured_with_some():
    driver = MockHmi()
    try:
        assert driver.hmi_info()["input"]["rotaries"] == []
    finally:
        driver.close()


def test_the_mock_declares_the_rotaries_it_is_configured_with_and_takes_their_push_buttons_as_keys():
    driver = MockHmi(rotaries=["tune-knob", {"id": "volume", "label": "Volume", "push": "volume-push"}])
    try:
        input_ = driver.hmi_info()["input"]
        assert input_["rotaries"] == [
            {"id": "tune-knob", "label": "Tune Knob", "push": None},
            {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"},
        ]
        assert input_["keys"] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN", "VOLUME_PUSH"]
        assert [k["key"] for k in input_["hard_keys"]] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]
    finally:
        driver.close()


def test_a_mouse_mock_can_have_a_rotary_too():
    driver = MockHmi(mode="mouse", rotaries=[{"id": "volume", "push": "MUTE"}])
    try:
        input_ = driver.hmi_info()["input"]
        assert [r["id"] for r in input_["rotaries"]] == ["volume"] and input_["keys"] == ["MUTE"]
    finally:
        driver.close()


@pytest.mark.parametrize("mode", ["touch", "mouse"])
def test_control_false_declares_nothing_in_either_mode(mode):
    driver = MockHmi(mode=mode, control=False, rotaries=["volume"])
    try:
        input_ = driver.hmi_info()["input"]
        assert input_["pointer"]["kinds"] == [] and input_["pointer"]["max_contacts"] == 0
        assert input_["keyboard"] == {"codes": [], "text": False, "layout": None}
        assert input_["keys"] == [] and input_["hard_keys"] == [] and input_["rotaries"] == []
    finally:
        driver.close()


def test_the_codecs_a_mock_offers_are_the_ones_it_is_told_to():
    driver = MockHmi(codecs=["hevc", "mjpeg"])
    try:
        info = driver.hmi_info()
        assert (info["codecs"], info["codec"]) == (["hevc", "mjpeg"], "hevc")  # the first is the default
        assert source(driver).capabilities().codecs == ("hevc", "mjpeg")
    finally:
        driver.close()


@pytest.mark.parametrize("codec", ["h264", "hevc", "mjpeg"])
def test_the_codec_option_chooses_the_codec_of_the_stream(codec):
    driver = MockHmi(width=240, height=400, fps=60, idle_stop_s=0, codec=codec)
    try:
        assert driver.hmi_info()["codec"] == codec
        with serve(driver) as client, client.video() as video:
            assert video.header["codec"].startswith({"h264": "avc1.", "hevc": "hev1.", "mjpeg": "mjpeg"}[codec])
            assert video.header["format"] == ("jpeg" if codec == "mjpeg" else "annexb")
            assert client.hmi_info()["codec"] == codec
            assert media(video)
        assert source(driver).started_codecs == [codec]
    finally:
        driver.close()


@pytest.mark.parametrize("codec", ["h264", "hevc", "mjpeg"])
def test_the_pattern_is_the_real_one_of_the_codec(codec):
    driver = MockHmi(pattern=True, fps=60, idle_stop_s=0, codec=codec)
    try:
        with serve(driver) as client, client.video() as video:
            assert (video.header["width"], video.header["height"]) == (240, 400)
            first = media(video)[0][8:]
            assert first in pattern_units(codec)
    finally:
        driver.close()


# -- what the HMI says about itself ------------------------------------------------------------------------------------

#: What a pointer, a keyboard, hard keys and a clipboard say when the HMI declares none of them.
NOTHING_DECLARED = {
    "pointer": {"kinds": [], "absolute": False, "relative": False, "buttons": False, "wheel": False, "max_contacts": 0},
    "keyboard": {"codes": [], "text": False, "layout": None},
    "keys": [],
    "hard_keys": [],
    "rotaries": [],
    "clipboard": False,
}


def test_hmi_info_before_the_stream_has_a_picture(hmi):
    info = hmi.hmi_info()
    assert (info["interface"], info["v"]) == ("hmi", 1)
    assert (info["width"], info["height"], info["rotation"]) == (None, None, 0)
    assert info["input"]["pointer"]["kinds"] == ["touch"]
    assert info["input"]["keys"] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"]
    assert (info["codecs"], info["codec"], info["recording"]) == (["h264", "hevc", "mjpeg"], "h264", True)
    assert info["quality"]["adjustable"] is True


def test_hmi_info_after_the_stream_started_has_the_shown_size(hmi):
    with hmi.video() as video:
        media(video)
        assert (hmi.hmi_info()["width"], hmi.hmi_info()["height"]) == (240, 400)


def test_hmi_info_has_the_shown_size_turned_by_the_rotation(hmi, driver):
    with hmi.video() as video:
        media(video)
        source(driver).change_picture(240, 400, rotation=90)
        wait_for(lambda: isinstance(video.read_frame(5.0), str), what="the new codec line")
        info = hmi.hmi_info()
        assert (info["width"], info["height"], info["rotation"]) == (400, 240, 90)


def test_a_view_only_hmi_has_no_input():
    """It is a valid HMI: a picture, and nothing declared. The input stream is refused, by the client and by the
    exporter.
    """
    driver = MockHmi(control=False)
    try:
        with serve(driver) as client:
            assert client.hmi_info()["input"] == NOTHING_DECLARED
            with pytest.raises(HmiStreamClosedError, match="takes no input"), client.input():
                pass
            with client.stream("input") as raw, pytest.raises(_STREAM_ENDS):  # asked of the exporter directly
                raw.send(b'{"touch": {"type": "down", "x": 1, "y": 1}}\n')
                raw.receive()
            with client.video() as video:  # and it still shows the picture
                assert media(video)
    finally:
        driver.close()


def test_an_hmi_that_declares_only_a_keyboard_takes_input_and_a_pointer_is_refused():
    driver = MockHmi()
    source(driver).pointer_caps = PointerCaps()
    source(driver).keyboard_caps = KeyboardCaps(codes=("KeyA",))
    try:
        with serve(driver) as client, client.video() as video, client.input() as stream:
            media(video)
            stream.touch("down", 5, 5)
            stream.keyboard("KeyA", True)
            assert stream.read_error(5.0) == "this HMI has no pointer"
            wait_for(lambda: source(driver).sink_input.of("keyboard"), what="the key")
    finally:
        driver.close()


def test_the_hard_keys_can_be_overridden_by_the_exporter():
    driver = MockHmi(hard_keys=["home", {"key": "volume-up", "label": "Louder", "presses": ["short"]}])
    try:
        with serve(driver) as client:
            hard = client.hmi_info()["input"]["hard_keys"]
            assert hard == [
                {"key": "HOME", "label": "Home", "presses": ["short", "long"]},
                {"key": "VOLUME_UP", "label": "Louder", "presses": ["short"]},
            ]
    finally:
        driver.close()


def test_power_and_lock_are_not_offered_by_default(rig):
    """Pressing them can lock the user out of a device whose only way in is its HMI: they are opt-in."""
    r = rig()
    keys = r.client.hmi_info()["input"]["keys"]
    assert "POWER" not in keys and "LOCK" not in keys
    assert not {"POWER", "LOCK"} & {k["key"] for k in r.client.hmi_info()["input"]["hard_keys"]}
    r.stream.send({"key": {"key": "power"}})  # sent as it is: the client would not
    r.stream.send({"key": {"key": "lock"}})
    errors, events = r.settle()
    assert errors == ["this HMI has no key POWER", "this HMI has no key LOCK"]
    assert events == []


def test_an_exporter_can_add_a_power_or_lock_key_by_listing_it(rig):
    r = rig(hard_keys=["HOME", "LOCK"])
    info = r.client.hmi_info()["input"]
    assert "LOCK" in info["keys"] and "POWER" not in info["keys"]
    assert [k["key"] for k in info["hard_keys"]] == ["HOME", "LOCK"]
    r.stream.key("lock")
    r.stream.send({"key": {"key": "power"}})  # only what is listed
    errors, events = r.settle()
    assert events == [("key", "LOCK", "short")]
    assert errors == ["this HMI has no key POWER"]


def test_a_listed_key_the_source_cannot_send_is_refused_and_not_drawn():
    driver = MockHmi(hard_keys=["HOME", "TELEPORT"])
    try:
        caps = driver._caps()
        assert "TELEPORT" not in caps.keys
        assert [k.key for k in caps.hard_keys] == ["HOME"]  # a button a UI draws is a key the HMI takes
        assert [k["key"] for k in driver.hmi_info()["input"]["hard_keys"]] == ["HOME"]
    finally:
        driver.close()


def test_the_hard_keys_default_to_the_sources():
    driver = MockHmi()
    try:
        assert [k.key for k in driver._caps().hard_keys] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]
        assert HardKey("HOME", "Home") in driver._caps().hard_keys
    finally:
        driver.close()


def test_hard_keys_are_not_a_mouses():
    """A mouse and keyboard HMI has no hard keys: listing some in the exporter's config adds none."""
    driver = MockHmi(mode="mouse", hard_keys=["HOME"])
    try:
        assert driver._caps().keys == () and driver._caps().hard_keys == ()
    finally:
        driver.close()


# -- the rig: an open picture and input stream, and a way to know the exporter has taken what was sent -----------------

#: The size of the picture of the mock in these tests.
SIZE = (240, 400)
#: A line the exporter always answers with an error that nothing else says. It answers lines in the order it took them,
#: so the errors up to the answer to this one are all the errors of the lines sent before it, and what the source has
#: received by then is everything those lines made it receive.
BARRIER = {"key": {"key": "BARRIER_OF_THE_TEST"}}
BARRIER_ANSWER = "this HMI has no key BARRIER_OF_THE_TEST"


class Rig:
    """A mock HMI with a picture and an input stream open, and the source behind it."""

    def __init__(self, driver: MockHmi, client, video, stack: ExitStack) -> None:
        self.driver, self.client, self.video = driver, client, video
        self._input = ExitStack()
        stack.callback(self._input.close)
        self.stream = self._input.enter_context(client.input())

    @property
    def sink(self):
        """What the source received."""
        return source(self.driver).sink_input

    def pointer(self, type_: str, x=None, y=None, kind: str = "mouse", **fields) -> None:
        """A pointer event, as the wire has it."""
        event = {"type": type_, "kind": kind, **fields}
        if x is not None:
            event["x"], event["y"] = x, y
        self.stream.send({"pointer": event})

    def touch(self, type_: str, x, y, **fields) -> None:
        """A touch event of the shorthand form."""
        self.stream.send({"touch": {"type": type_, "x": x, "y": y, **fields}})

    def key(self, code: str, down: bool = True) -> None:
        self.stream.send({"keyboard": {"code": code, "type": "down" if down else "up"}})

    def settle(self) -> tuple[list[str], list[tuple]]:
        """``(the errors the exporter answered with, what the source received)``, once it has taken all that was
        sent.
        """
        self.stream.send(BARRIER)
        errors: list[str] = []
        while True:
            message = self.stream.read_error(10.0)
            assert message is not None, "the input stream ended"
            if message == BARRIER_ANSWER:
                return errors, [e for e in self.sink.snapshot() if e[0] != "release"]
            errors.append(message)

    def end_input(self) -> list[tuple]:
        """Close the input stream: everything the source received, once it has been told the input has ended."""
        self._input.close()
        wait_for(lambda: self.sink.of("release"), what="the end of the input to reach the source")
        return self.sink.snapshot()


@pytest.fixture
def rig():
    """``rig(**options)`` is a mock HMI with a picture and an input stream open: ``options`` are the mock's, and
    ``pointer`` and ``keyboard`` replace what the source declares.
    """
    stack = ExitStack()

    def make(*, pointer: PointerCaps | None = None, keyboard: KeyboardCaps | None = None, **options: Any) -> Rig:
        merged: dict[str, Any] = {"width": 240, "height": 400, "fps": 60, "idle_stop_s": 0, **options}
        driver = MockHmi(**merged)
        stack.callback(driver.close)
        fake = source(driver)
        fake.pointer_caps, fake.keyboard_caps = pointer, keyboard
        client = stack.enter_context(serve(driver))
        video = stack.enter_context(client.video())
        media(video)
        return Rig(driver, client, video, stack)

    with stack:
        yield make


def ptr(type_, kind, x, y, id=0, buttons=0, dx=0.0, dy=0.0, size=SIZE):
    """What a source sees of a pointer event that is not a single finger."""
    return (
        "pointer",
        type_,
        kind,
        None if x is None else float(x),
        None if y is None else float(y),
        id,
        buttons,
        float(dx),
        float(dy),
        size,
    )


# -- input: touch ------------------------------------------------------------------------------------------------------


def test_touch_events_reach_the_source_as_a_single_finger_in_order(rig):
    r = rig()
    r.touch("down", 10.4, 20.6, w=240, h=400)
    r.touch("move", 11, 21)
    r.touch("up", 11, 21)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 10, 21, SIZE),
        ("touch", "move", 11, 21, SIZE),
        ("touch", "up", 11, 21, SIZE),
    ]


@pytest.mark.parametrize(
    "wire",
    [
        lambda t, x, y: {"touch": {"type": t, "x": x, "y": y}},
        lambda t, x, y: {"pointer": {"type": t, "kind": "touch", "x": x, "y": y}},
        lambda t, x, y: {
            "pointer": {"type": t, "kind": "touch", "x": x, "y": y, "id": 0, "buttons": 0 if t == "up" else 1}
        },
    ],
)
def test_the_touch_shorthand_and_a_pointer_of_kind_touch_are_the_same_events(rig, wire):
    r = rig()
    for kind, point in (("down", (5, 6)), ("move", (7, 8)), ("up", (7, 8))):
        r.stream.send(wire(kind, *point))
    errors, events = r.settle()
    assert errors == []
    assert events == [("touch", "down", 5, 6, SIZE), ("touch", "move", 7, 8, SIZE), ("touch", "up", 7, 8, SIZE)]


def test_a_cancel_is_an_up(rig):
    r = rig()
    r.touch("down", 5, 5)
    r.touch("cancel", 6, 6)
    assert [e[1] for e in r.settle()[1]] == ["down", "up"]


def test_coordinates_are_whole_pixels_held_to_the_picture(rig):
    r = rig()
    r.touch("down", -5, 9999)
    r.touch("move", 1e12, -1e12)
    r.touch("up", 9999, -5)
    r.touch("down", 3.2, 4.7)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 0, 399, SIZE),
        ("touch", "move", 239, 0, SIZE),
        ("touch", "up", 239, 0, SIZE),
        ("touch", "down", 3, 5, SIZE),
    ]
    assert all(isinstance(value, int) for event in events for value in event[2:4])


def test_a_move_or_up_of_a_finger_that_is_not_down_is_nothing(rig):
    r = rig()
    r.touch("move", 5, 5)  # a finger does not hover
    r.touch("up", 5, 5)
    r.pointer("move", 5, 5, kind="touch")
    r.stream.key("home")
    errors, events = r.settle()
    assert errors == []
    assert events == [("key", "HOME", "short")]


def test_a_second_down_is_the_same_finger_moving(rig):
    r = rig()
    r.touch("down", 5, 5)
    r.touch("down", 6, 6)
    r.touch("up", 6, 6)
    assert [e[1] for e in r.settle()[1]] == ["down", "move", "up"]


def test_a_late_up_after_the_finger_was_lifted_is_dropped(rig):
    r = rig()
    r.touch("down", 5, 5)
    r.touch("up", 5, 5)
    r.touch("up", 5, 5)
    assert [e[1] for e in r.settle()[1]] == ["down", "up"]


def test_a_pointer_kind_the_device_does_not_declare_is_ignored_not_an_error(rig):
    r = rig()  # a touch screen
    for kind in ("mouse", "pen"):
        r.pointer("down", 5, 5, kind=kind, buttons=1)
        r.pointer("move", 6, 6, kind=kind, buttons=1)
        r.pointer("up", 6, 6, kind=kind)
        r.pointer("wheel", 6, 6, kind=kind, dy=120)
    r.touch("down", 9, 9)
    errors, events = r.settle()
    assert errors == []
    assert events == [("touch", "down", 9, 9, SIZE)]


def test_a_second_contact_beyond_max_contacts_is_ignored_with_its_moves_and_up(rig):
    r = rig()  # one finger
    r.touch("down", 5, 5)
    r.touch("down", 50, 50, id=1)
    r.touch("move", 51, 51, id=1)
    r.touch("move", 6, 6)
    r.touch("up", 51, 51, id=1)
    r.touch("up", 6, 6)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 5, 5, SIZE),
        ("touch", "move", 6, 6, SIZE),
        ("touch", "up", 6, 6, SIZE),
    ]


def test_a_finger_that_comes_when_there_is_room_is_followed_under_the_number_the_device_knows(rig):
    """The client's own id (a browser's pointerId is any number) becomes the smallest free contact number."""
    r = rig()
    r.touch("down", 5, 5)
    r.touch("down", 50, 50, id=1)  # ignored: there is room for one
    r.touch("up", 5, 5)
    r.touch("down", 60, 60, id=1)  # now there is
    r.touch("up", 60, 60, id=1)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 5, 5, SIZE),
        ("touch", "up", 5, 5, SIZE),
        ("touch", "down", 60, 60, SIZE),  # the first finger is lifted: the device's number 0 is free again
        ("touch", "up", 60, 60, SIZE),
    ]


def test_the_device_is_told_small_numbers_whatever_ids_the_client_uses(rig):
    r = rig(pointer=PointerCaps.touch(max_contacts=2))
    r.touch("down", 10, 10, id=4711)
    r.touch("down", 20, 20, id=1_000_003)
    r.touch("move", 21, 21, id=1_000_003)
    r.touch("up", 10, 10, id=4711)
    r.touch("up", 21, 21, id=1_000_003)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 10, 10, SIZE),
        ptr("down", "touch", 20, 20, id=1, buttons=1),
        ptr("move", "touch", 21, 21, id=1, buttons=1),
        ("touch", "up", 10, 10, SIZE),
        ptr("up", "touch", 21, 21, id=1, buttons=0),
    ]


def test_every_contact_up_to_max_contacts_is_followed_and_a_lifted_one_makes_room(rig):
    r = rig(pointer=PointerCaps.touch(max_contacts=2))
    r.touch("down", 10, 10)
    r.touch("down", 20, 20, id=1)
    r.touch("down", 30, 30, id=2)  # a third: ignored
    r.touch("move", 21, 21, id=1)
    r.touch("move", 31, 31, id=2)
    r.touch("up", 10, 10)
    r.touch("down", 32, 32, id=2)  # there is room now
    r.touch("move", 33, 33, id=2)
    r.touch("up", 33, 33, id=2)
    r.touch("up", 21, 21, id=1)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 10, 10, SIZE),
        ptr("down", "touch", 20, 20, id=1, buttons=1),
        ptr("move", "touch", 21, 21, id=1, buttons=1),
        ("touch", "up", 10, 10, SIZE),
        ("touch", "down", 32, 32, SIZE),  # the client's id 2 takes the number the lifted finger freed
        ("touch", "move", 33, 33, SIZE),
        ("touch", "up", 33, 33, SIZE),
        ptr("up", "touch", 21, 21, id=1, buttons=0),
    ]


def test_contacts_of_different_kinds_are_different_contacts_even_with_the_same_id(rig):
    r = rig(pointer=PointerCaps(kinds=("touch", "pen"), max_contacts=2))
    r.touch("down", 10, 10)
    r.pointer("down", 20, 20, kind="pen", buttons=1)
    r.pointer("move", 21, 21, kind="pen")
    r.touch("up", 10, 10)
    r.pointer("up", 21, 21, kind="pen")
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 10, 10, SIZE),
        ptr("down", "pen", 20, 20, id=1, buttons=1),  # the device's number 1: number 0 is the finger's
        ptr("move", "pen", 21, 21, id=1, buttons=1),
        ("touch", "up", 10, 10, SIZE),
        ptr("up", "pen", 21, 21, id=1, buttons=0),
    ]


def test_a_button_a_touch_screen_does_not_have_is_ignored_and_the_primary_one_is_kept(rig):
    r = rig()
    r.touch("down", 5, 5, buttons=2)  # a secondary button: not declared, so nothing happens
    r.touch("up", 5, 5)
    r.touch("down", 6, 6, buttons=3)  # primary and secondary: the primary one is what it has
    r.touch("move", 7, 7, buttons=3)
    r.touch("move", 8, 8, buttons=2)  # the primary one is let go: the finger is lifted
    r.touch("move", 9, 9)  # and a finger that is up does not move
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("touch", "down", 6, 6, SIZE),
        ("touch", "move", 7, 7, SIZE),
        ("touch", "up", 8, 8, SIZE),
    ]


def test_a_picture_that_changed_refuses_the_touch_aimed_at_the_old_one(rig):
    r = rig()
    source(r.driver).change_picture(200, 300)
    wait_for(lambda: isinstance(r.video.read_frame(5.0), str), what="the new codec line")
    r.touch("down", 5, 5, w=240, h=400)
    errors, events = r.settle()
    assert len(errors) == 1 and "200x300" in errors[0] and "240x400" in errors[0]
    assert events == []


def test_an_event_aimed_at_the_picture_that_is_shown_is_taken(rig):
    r = rig()
    source(r.driver).change_picture(200, 300)
    wait_for(lambda: isinstance(r.video.read_frame(5.0), str), what="the new codec line")
    r.touch("down", 5, 5, w=200, h=300)
    errors, events = r.settle()
    assert (errors, events) == ([], [("touch", "down", 5, 5, (200, 300))])


def test_a_rotated_picture_is_pointed_at_in_the_shown_orientation(rig):
    r = rig()
    source(r.driver).change_picture(240, 400, rotation=90)
    wait_for(lambda: isinstance(r.video.read_frame(5.0), str), what="the new codec line")
    r.touch("down", 390, 230, w=400, h=240)
    r.touch("up", 399, 239)
    r.touch("down", 5, 5, w=240, h=400)  # aimed at the picture as it was before it was turned
    errors, events = r.settle()
    assert events == [("touch", "down", 390, 230, (400, 240)), ("touch", "up", 399, 239, (400, 240))]
    assert len(errors) == 1 and "400x240" in errors[0]


def test_a_down_aimed_at_the_wrong_picture_never_makes_a_contact(rig):
    """The refused down is nothing: the move and up that follow it are dropped, not held for a finger."""
    r = rig()
    r.touch("down", 5, 5, w=1, h=1)
    r.touch("move", 6, 6)
    r.touch("up", 6, 6)
    errors, events = r.settle()
    assert len(errors) == 1 and events == []


# -- input: mouse ------------------------------------------------------------------------------------------------------


def test_a_click_is_a_down_and_an_up_with_the_buttons_after_each(rig):
    r = rig(mode="mouse")
    r.pointer("down", 100, 200, buttons=1)
    r.pointer("up", 100, 200, buttons=0)
    errors, events = r.settle()
    assert errors == []
    assert events == [ptr("down", "mouse", 100, 200, buttons=1), ptr("up", "mouse", 100, 200, buttons=0)]


def test_a_drag_moves_with_the_button_that_is_held(rig):
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=1)
    r.pointer("move", 20, 20)  # no buttons said: what is held stays held
    r.pointer("move", 30, 30, buttons=1)
    r.pointer("up", 30, 30)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("down", "mouse", 10, 10, buttons=1),
        ptr("move", "mouse", 20, 20, buttons=1),
        ptr("move", "mouse", 30, 30, buttons=1),
        ptr("up", "mouse", 30, 30, buttons=0),
    ]


def test_a_down_with_no_buttons_said_is_the_primary_button(rig):
    r = rig(mode="mouse")
    r.pointer("down", 5, 5)
    r.pointer("up", 5, 5)
    assert [e[6] for e in r.settle()[1]] == [1, 0]


def test_a_second_button_is_a_move_and_the_last_one_let_go_is_an_up(rig):
    """``buttons`` is the state after the event (as a browser's ``PointerEvent.buttons``): a ``move`` when buttons
    change while one is held, an ``up`` when the last is let go.
    """
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=1)
    r.pointer("move", 11, 11, buttons=3)  # the secondary button goes down too
    r.pointer("move", 12, 12, buttons=2)  # the primary one is let go
    r.pointer("move", 13, 13, buttons=0)  # and the last
    r.pointer("up", 13, 13)  # a late up after that is nothing
    r.pointer("move", 14, 14, buttons=0)  # now it hovers
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("down", "mouse", 10, 10, buttons=1),
        ptr("move", "mouse", 11, 11, buttons=3),
        ptr("move", "mouse", 12, 12, buttons=2),
        ptr("up", "mouse", 13, 13, buttons=0),
        ptr("move", "mouse", 14, 14, buttons=0),
    ]


def test_a_second_down_while_a_button_is_held_is_the_buttons_changing(rig):
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=1)
    r.pointer("down", 10, 10, buttons=3)
    r.pointer("up", 10, 10)
    assert [e[1:3] + e[6:7] for e in r.settle()[1]] == [("down", "mouse", 1), ("move", "mouse", 3), ("up", "mouse", 0)]


def test_an_up_lifts_the_mouse_whatever_buttons_it_says(rig):
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=3)
    r.pointer("up", 10, 10, buttons=1)
    r.pointer("move", 11, 11)
    events = r.settle()[1]
    assert events == [
        ptr("down", "mouse", 10, 10, buttons=3),
        ptr("up", "mouse", 10, 10, buttons=0),
        ptr("move", "mouse", 11, 11, buttons=0),
    ]


def test_a_mouse_that_moves_with_nothing_down_is_hovering_until_a_button_comes_down_on_a_move(rig):
    r = rig(mode="mouse")
    r.pointer("move", 50, 60)
    r.pointer("move", 51, 61, buttons=1)  # a browser says a button went down while another was held with a move
    errors, events = r.settle()
    assert errors == []
    assert events == [ptr("move", "mouse", 50, 60), ptr("down", "mouse", 51, 61, buttons=1)]


def test_a_mouse_with_only_the_primary_button_ignores_the_others(rig):
    r = rig(pointer=PointerCaps.mouse(buttons=False))
    r.pointer("down", 5, 5, buttons=2)  # the secondary button: ignored
    r.pointer("up", 5, 5)
    r.pointer("down", 6, 6, buttons=3)  # both: the primary one
    r.pointer("move", 7, 7, buttons=3)
    r.pointer("move", 8, 8, buttons=2)  # the primary one is let go
    r.pointer("move", 9, 9, buttons=1)  # the primary button is pressed again with the other still held: a press
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("down", "mouse", 6, 6, buttons=1),
        ptr("move", "mouse", 7, 7, buttons=1),
        ptr("up", "mouse", 8, 8, buttons=0),
        ptr("down", "mouse", 9, 9, buttons=1),
    ]


def test_a_second_mouse_pointer_is_beyond_the_one_contact(rig):
    r = rig(mode="mouse")
    r.pointer("down", 5, 5, buttons=1)
    r.pointer("down", 50, 50, buttons=1, id=1)
    r.pointer("up", 50, 50, id=1)
    r.pointer("up", 5, 5)
    errors, events = r.settle()
    assert errors == []
    assert [e[1] for e in events] == ["down", "up"] and events[0][5] == 0


def test_a_pen_hovers_and_draws(rig):
    r = rig(pointer=PointerCaps(kinds=("pen",), buttons=True, max_contacts=1))
    r.pointer("move", 5, 5, kind="pen")
    r.pointer("down", 5, 5, kind="pen", buttons=1)
    r.pointer("move", 9, 9, kind="pen", buttons=1)
    r.pointer("up", 9, 9, kind="pen")
    errors, events = r.settle()
    assert errors == []
    assert [(e[1], e[2], e[6]) for e in events] == [
        ("move", "pen", 0),
        ("down", "pen", 1),
        ("move", "pen", 1),
        ("up", "pen", 0),
    ]


# -- input: wheel and relative motion ----------------------------------------------------------------------------------


def test_a_wheel_reaches_the_device_with_its_deltas_and_the_buttons_that_are_held(rig):
    r = rig(mode="mouse")
    r.pointer("wheel", 100, 200, dy=120)
    r.pointer("wheel", 101, 201, dx=-30.5, dy=-60)
    r.pointer("down", 10, 10, buttons=1)
    r.pointer("wheel", 11, 11, dy=40)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("wheel", "mouse", 100, 200, dy=120),
        ptr("wheel", "mouse", 101, 201, dx=-30.5, dy=-60),
        ptr("down", "mouse", 10, 10, buttons=1),
        ptr("wheel", "mouse", 11, 11, buttons=1, dy=40),
    ]


def test_a_wheel_does_not_start_or_end_a_drag(rig):
    r = rig(mode="mouse")
    r.pointer("wheel", 10, 10, dy=5)
    r.pointer("up", 10, 10)
    assert [e[1] for e in r.settle()[1]] == ["wheel"]


def test_a_wheel_the_device_has_none_of_is_an_error(rig):
    r = rig(pointer=PointerCaps.mouse(wheel=False))
    r.pointer("wheel", 100, 200, dy=120)
    r.pointer("move", 5, 5)
    errors, events = r.settle()
    assert errors == ["this pointer has no wheel"]
    assert events == [ptr("move", "mouse", 5, 5)]  # and the stream goes on


def test_a_touch_screen_has_no_wheel(rig):
    r = rig()
    r.pointer("wheel", 100, 200, kind="touch", dy=120)
    errors, events = r.settle()
    assert errors == ["this pointer has no wheel"] and events == []


def test_a_wheel_is_aimed_at_the_picture_as_the_rest_is(rig):
    r = rig(mode="mouse")
    r.pointer("wheel", 100, 200, dy=120, w=99, h=99)
    errors, events = r.settle()
    assert len(errors) == 1 and "240x400" in errors[0] and events == []


def test_relative_motion_is_an_error_for_a_mouse_that_gives_positions_only(rig):
    r = rig(mode="mouse")
    r.pointer("move", dx=5, dy=-3)
    errors, events = r.settle()
    assert errors == ["this pointer takes positions, not relative motion"] and events == []


def test_relative_motion_reaches_a_mouse_that_declares_it_with_no_position(rig):
    r = rig(mode="mouse", relative=True)
    r.pointer("move", dx=5, dy=-3)  # hovering by a distance
    r.pointer("down", 10, 10, buttons=1)
    r.pointer("move", dx=2, dy=3)  # dragging by a distance
    r.pointer("move", 20, 20, dx=9, dy=9)  # a position: the distance means nothing
    r.pointer("up", 20, 20)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("move", "mouse", None, None, dx=5, dy=-3),
        ptr("down", "mouse", 10, 10, buttons=1),
        ptr("move", "mouse", None, None, buttons=1, dx=2, dy=3),
        ptr("move", "mouse", 20, 20, buttons=1),
        ptr("up", "mouse", 20, 20),
    ]


def test_a_mouse_that_is_relative_only_takes_no_positions_for_a_move(rig):
    r = rig(pointer=PointerCaps(kinds=("mouse",), absolute=False, relative=True, buttons=True, max_contacts=1))
    r.pointer("move", 20, 20)
    r.pointer("move", dx=4, dy=4)
    r.pointer("down", 30, 30, buttons=1)  # a press says where: that is passed on
    errors, events = r.settle()
    assert errors == ["this pointer takes relative motion, not positions"]
    assert events == [ptr("move", "mouse", None, None, dx=4, dy=4), ptr("down", "mouse", 30, 30, buttons=1)]


def test_a_relative_move_of_a_touch_screen_that_is_not_down_is_nothing(rig):
    r = rig(pointer=PointerCaps(kinds=("touch",), absolute=True, relative=True, max_contacts=1))
    r.pointer("move", kind="touch", dx=4, dy=4)
    errors, events = r.settle()
    assert (errors, events) == ([], [])


def test_a_wheel_delta_or_relative_motion_that_is_absurdly_large_is_held_to_a_limit(rig):
    r = rig(mode="mouse", relative=True)
    r.pointer("wheel", 5, 5, dx=-1e300, dy=1e12)
    r.pointer("move", dx=1e12, dy=-1e300)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ptr("wheel", "mouse", 5, 5, dx=-MAX_DELTA_PX, dy=MAX_DELTA_PX),
        ptr("move", "mouse", None, None, dx=MAX_DELTA_PX, dy=-MAX_DELTA_PX),
    ]


# -- input: the end of the stream and restarts ------------------------------------------------------------------------


def test_what_is_still_down_when_the_input_ends_is_lifted_and_released_and_the_source_told(rig):
    r = rig(mode="mouse")
    r.pointer("down", 10, 20, buttons=3)
    r.pointer("move", 30, 40)
    r.key("KeyA")
    r.key("KeyB")
    r.key("ShiftLeft")
    r.key("ShiftLeft", False)
    r.settle()
    events = r.end_input()
    assert events[-4:] == [
        ptr("up", "mouse", 30, 40, buttons=0),  # lifted where it was last, with no button held
        ("keyboard", "KeyB", False),  # released in the opposite order they went down
        ("keyboard", "KeyA", False),
        ("release",),
    ]
    assert [e for e in events if e[0] == "keyboard" and e[1] == "ShiftLeft"] == [
        ("keyboard", "ShiftLeft", True),
        ("keyboard", "ShiftLeft", False),
    ]  # one that was let go is not released again


def test_a_modifier_that_is_still_down_goes_up_after_the_keys_it_modified(rig):
    r = rig(mode="mouse")
    for code in ("ControlLeft", "ShiftLeft", "KeyA"):
        r.key(code)
    r.key("ShiftLeft", False)  # let go early, and pressed again: it is now the last of them to have gone down
    r.key("ShiftLeft")
    r.settle()
    assert r.end_input()[-4:] == [
        ("keyboard", "ShiftLeft", False),
        ("keyboard", "KeyA", False),
        ("keyboard", "ControlLeft", False),
        ("release",),
    ]


def test_a_finger_left_down_is_lifted_where_it_last_was(rig):
    r = rig()
    r.touch("down", 5, 5)
    r.touch("move", 77, 88)
    r.settle()
    assert r.end_input()[-2:] == [("touch", "up", 77, 88, SIZE), ("release",)]


def test_an_input_stream_that_ends_lets_go_only_of_what_it_held_while_another_is_open(rig):
    r = rig(mode="mouse")
    r.key("ShiftLeft")  # held by the first stream
    r.settle()
    with r.client.input() as other:
        other.send({"keyboard": {"code": "KeyA", "type": "down"}})
        other.send(BARRIER)
        while other.read_error(10.0) != BARRIER_ANSWER:
            pass
    wait_for(lambda: r.driver._inputs == 1, what="the second input stream to end")
    seen = r.sink.snapshot()
    assert ("keyboard", "KeyA", False) in seen  # what the second stream held is let go
    assert ("keyboard", "ShiftLeft", False) not in seen and not r.sink.of("release")  # the first one's is not
    events = r.end_input()  # the last one to end lets go of everything
    assert events[-2:] == [("keyboard", "ShiftLeft", False), ("release",)]


def test_every_contact_is_lifted_when_the_input_ends_in_the_order_they_went_down(rig):
    r = rig(pointer=PointerCaps.touch(max_contacts=3))
    r.touch("down", 1, 1)
    r.touch("down", 2, 2, id=2)
    r.touch("down", 3, 3, id=1)
    r.settle()
    assert r.end_input()[-4:] == [
        ("touch", "up", 1, 1, SIZE),
        ptr("up", "touch", 2, 2, id=1),  # under the numbers the device was given, not the client's
        ptr("up", "touch", 3, 3, id=2),
        ("release",),
    ]


def test_a_hovering_mouse_and_a_still_keyboard_leave_nothing_to_lift(rig):
    r = rig(mode="mouse")
    r.pointer("move", 5, 5)
    r.key("KeyA")
    r.key("KeyA", False)
    r.settle()
    assert r.end_input()[-2:] == [("keyboard", "KeyA", False), ("release",)]
    assert [e for e in r.sink.snapshot() if e[0] == "pointer" and e[1] == "up"] == []


def test_a_stream_that_ends_with_nothing_held_only_tells_the_source(rig):
    r = rig()
    r.stream.key("home")
    r.settle()
    assert r.end_input() == [("key", "HOME", "short"), ("release",)]


def test_a_restart_forgets_what_was_held_and_the_new_capture_is_not_told_of_it(rig):
    """The new capture never saw the contact or the key go down: its moves, its up and the key's up are dropped."""
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=1)
    r.key("KeyA")
    r.settle()
    r.client.set_quality("low")  # restarts the capture
    wait_for(lambda: source(r.driver).starts == 2, what="the restart")
    wait_for(lambda: isinstance(r.video.read_frame(5.0), bytes), what="a picture of the new capture")
    before = len(r.sink.snapshot())
    r.pointer("move", 11, 11)  # hovering now: no button is held on the new capture
    r.pointer("up", 11, 11)
    r.key("KeyA", False)
    r.key("KeyB")
    r.pointer("down", 20, 20, buttons=1)
    errors, events = r.settle()
    assert errors == []
    assert events[before:] == [
        ptr("move", "mouse", 11, 11),
        ("keyboard", "KeyB", True),
        ptr("down", "mouse", 20, 20, buttons=1),
    ]
    # at the end only what the new capture saw go down is let go
    assert r.end_input()[before + 3 :] == [
        ptr("up", "mouse", 20, 20),
        ("keyboard", "KeyB", False),
        ("release",),
    ]


def test_a_stream_that_ends_after_a_restart_lifts_nothing_the_new_capture_never_saw(rig):
    r = rig(mode="mouse")
    r.pointer("down", 10, 10, buttons=1)
    r.key("KeyA")
    r.settle()
    r.client.set_quality("low")
    wait_for(lambda: source(r.driver).starts == 2, what="the restart")
    before = len(r.sink.snapshot())
    assert r.end_input()[before:] == [("release",)]


# -- input: the keyboard -----------------------------------------------------------------------------------------------


def test_keys_go_down_and_up_in_the_order_they_were_sent(rig):
    r = rig(mode="mouse")
    for code, down in (
        ("ShiftLeft", True),
        ("KeyA", True),
        ("KeyA", False),
        ("ControlLeft", True),
        ("KeyB", True),
        ("KeyB", False),
        ("ControlLeft", False),
        ("ShiftLeft", False),
    ):
        r.key(code, down)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("keyboard", "ShiftLeft", True),
        ("keyboard", "KeyA", True),
        ("keyboard", "KeyA", False),
        ("keyboard", "ControlLeft", True),
        ("keyboard", "KeyB", True),
        ("keyboard", "KeyB", False),
        ("keyboard", "ControlLeft", False),
        ("keyboard", "ShiftLeft", False),
    ]


def test_a_repeat_of_a_key_that_is_down_is_dropped_because_the_device_repeats_by_itself(rig):
    r = rig(mode="mouse")
    for _ in range(4):
        r.key("KeyA")
    r.key("KeyA", False)
    r.key("KeyA")  # pressed again after it was let go: a new press
    errors, events = r.settle()
    assert errors == []
    assert events == [("keyboard", "KeyA", True), ("keyboard", "KeyA", False), ("keyboard", "KeyA", True)]


def test_a_key_up_for_a_key_that_is_not_down_is_dropped(rig):
    r = rig(mode="mouse")
    r.key("KeyA", False)
    r.key("KeyB")
    r.key("KeyA", False)
    r.key("KeyB", False)
    r.key("KeyB", False)
    errors, events = r.settle()
    assert errors == []
    assert events == [("keyboard", "KeyB", True), ("keyboard", "KeyB", False)]


def test_a_key_that_is_not_in_the_list_is_an_error_and_one_that_is_goes_through(rig):
    r = rig(mode="mouse", keyboard=KeyboardCaps(codes=("KeyA", "Enter"), text=True))
    r.key("KeyB")
    r.key("KeyB", False)
    r.key("Enter")
    r.key("Enter", False)
    errors, events = r.settle()
    assert errors == ["this HMI does not take the key KeyB"] * 2
    assert events == [("keyboard", "Enter", True), ("keyboard", "Enter", False)]


def test_a_keyboard_that_takes_every_key_takes_any_code_but_not_what_is_not_a_code(rig):
    r = rig(mode="mouse")
    r.key("IntlBackslash")
    r.key("F13")
    r.stream.send({"keyboard": {"code": "a key", "type": "down"}})
    r.stream.send({"keyboard": {"code": "", "type": "down"}})
    r.stream.send({"keyboard": {"code": "KeyA", "type": "hold"}})
    errors, events = r.settle()
    assert events == [("keyboard", "IntlBackslash", True), ("keyboard", "F13", True)]
    assert len(errors) == 3 and "keyboard." in errors[0] and "keyboard." in errors[1] and "down" in errors[2]


def test_a_device_that_only_types_text_takes_no_key_codes(rig):
    r = rig()  # a touch screen: typed text, hard keys, no keyboard keys
    r.key("KeyA")
    r.stream.text("a")
    errors, events = r.settle()
    assert errors == ["this HMI takes no key codes"]
    assert events == [("text", "a")]


def test_no_more_keys_are_down_at_once_than_a_keyboard_has_fingers(rig):
    r = rig(mode="mouse")
    for i in range(1, MAX_KEYS_DOWN + 2):
        r.key(f"F{i}")
    errors, events = r.settle()
    assert errors == [f"{MAX_KEYS_DOWN} keys are down already"]
    assert len(events) == MAX_KEYS_DOWN
    r.key("F1", False)  # one let go makes room
    r.key(f"F{MAX_KEYS_DOWN + 1}")
    errors, events = r.settle()
    assert errors == [] and events[-2:] == [("keyboard", "F1", False), ("keyboard", f"F{MAX_KEYS_DOWN + 1}", True)]


def test_a_device_with_no_keyboard_takes_neither_key_codes_nor_text(rig):
    r = rig(keyboard=KeyboardCaps())
    r.key("KeyA")
    r.stream.send({"text": "hi"})
    errors, events = r.settle()
    assert errors == ["this HMI takes no key codes", "this HMI does not take text"] and events == []


# -- input: hard keys and text -----------------------------------------------------------------------------------------


def test_hard_keys_go_to_the_source_by_their_canonical_name_with_their_press(rig):
    r = rig()
    for name, press in (("recents", "short"), ("home", "long"), ("VOLUME-UP", "short"), ("keycode_back", "long")):
        r.stream.key(name, press)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("key", "APP_SWITCH", "short"),
        ("key", "HOME", "long"),
        ("key", "VOLUME_UP", "short"),
        ("key", "BACK", "long"),
    ]


def test_a_press_the_key_does_not_declare_is_an_error(rig):
    r = rig()
    r.stream.key("HOME", "down")  # a hard key takes short and long unless it says
    r.stream.key("HOME", "short")
    errors, events = r.settle()
    assert errors == ["the key HOME takes short, long, not down"] and events == [("key", "HOME", "short")]


@pytest.mark.parametrize("key", ["HOME", "VOLUME_DOWN"])  # one a UI draws, one that is only in the list of keys
@pytest.mark.parametrize("press", ["down", "up"])
def test_a_key_takes_short_and_long_unless_it_says_and_down_and_up_are_refused(rig, key, press):
    """A key that is in ``keys`` but has no entry in ``hard_keys`` takes the default presses too."""
    r = rig()
    r.stream.key(key, press)
    r.stream.key(key, "long")
    errors, events = r.settle()
    assert errors == [f"the key {key} takes short, long, not {press}"]
    assert events == [("key", key, "long")]


def test_a_key_that_says_it_takes_only_short_refuses_long(rig):
    r = rig(hard_keys=["HOME", {"key": "BACK", "presses": ["short"]}])
    r.stream.key("BACK", "long")
    r.stream.key("BACK", "short")
    r.stream.key("HOME", "long")  # a key with no presses of its own takes the default
    errors, events = r.settle()
    assert errors == ["the key BACK takes short, not long"]
    assert events == [("key", "BACK", "short"), ("key", "HOME", "long")]


def test_a_key_that_declares_all_four_presses_takes_them(rig):
    r = rig(hard_keys=[{"key": "HOME", "presses": ["short", "long", "down", "up"]}])
    for press in ("down", "up", "short", "long"):
        r.stream.key("HOME", press)
    errors, events = r.settle()
    assert errors == []
    assert events == [("key", "HOME", p) for p in ("down", "up", "short", "long")]


def test_a_key_that_declares_down_and_up_only_refuses_short(rig):
    r = rig(hard_keys=[{"key": "HOME", "presses": ["down", "up"]}])
    r.stream.key("HOME", "short")
    r.stream.key("HOME", "down")
    r.stream.key("HOME", "up")
    errors, events = r.settle()
    assert errors == ["the key HOME takes down, up, not short"]
    assert events == [("key", "HOME", "down"), ("key", "HOME", "up")]


def test_a_hard_key_the_device_does_not_have_is_an_error(rig):
    r = rig()
    r.stream.send({"key": {"key": "NO_SUCH_KEY"}})
    r.stream.send({"key": {"key": "MUTE"}})  # a common name this device does not offer
    errors, events = r.settle()
    assert errors == ["this HMI has no key NO_SUCH_KEY", "this HMI has no key MUTE"] and events == []


def test_a_mouse_and_keyboard_has_no_hard_keys(rig):
    r = rig(mode="mouse")
    r.stream.send({"key": {"key": "HOME"}})
    errors, events = r.settle()
    assert errors == ["this HMI has no key HOME"] and events == []


@pytest.mark.parametrize("mode", ["touch", "mouse"])
def test_text_reaches_a_device_that_types_it(rig, mode):
    r = rig(mode=mode)
    r.stream.text("héllo wörld")
    r.stream.text("")
    errors, events = r.settle()
    assert errors == []
    assert events == [("text", "héllo wörld"), ("text", "")]


def test_text_is_an_error_for_a_device_that_cannot_type_it(rig):
    r = rig(keyboard=KeyboardCaps(codes=("KeyA",), text=False))
    r.stream.send({"text": "hi"})
    errors, events = r.settle()
    assert errors == ["this HMI does not take text"] and events == []


def test_the_events_of_every_kind_reach_the_source_in_the_order_they_were_sent(rig):
    r = rig()
    r.stream.key("recents")
    r.stream.touch("down", 10.4, 20.6, size=SIZE)
    r.stream.touch("move", 11, 21)
    r.stream.touch("up", 11, 21)
    r.stream.key("home", "long")
    r.stream.text("héllo")
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("key", "APP_SWITCH", "short"),
        ("touch", "down", 10, 21, SIZE),
        ("touch", "move", 11, 21, SIZE),
        ("touch", "up", 11, 21, SIZE),
        ("key", "HOME", "long"),
        ("text", "héllo"),
    ]


# -- input: rotaries -----------------------------------------------------------------------------------------------

#: A volume knob that can be pushed and a tuning knob that cannot.
KNOBS = [{"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"}, "tune"]


def turn(rotary_id, delta):
    """The wire line of a rotary turn."""
    return {"rotary": {"id": rotary_id, "delta": delta}}


def test_a_rotary_turn_reaches_the_source_with_its_id_and_delta_in_order(rig):
    r = rig(rotaries=KNOBS)
    for line in (turn("volume", -2), turn("tune", 1), turn("volume", 100), turn("volume", -100)):
        r.stream.send(line)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("rotary", "volume", -2),
        ("rotary", "tune", 1),
        ("rotary", "volume", 100),
        ("rotary", "volume", -100),
    ]


def test_a_rotary_is_a_turn_and_holds_nothing_when_the_input_ends(rig):
    r = rig(rotaries=KNOBS)
    r.stream.send(turn("tune", 3))
    r.settle()
    assert r.end_input() == [("rotary", "tune", 3), ("release",)]


def test_the_events_of_every_kind_including_a_rotary_reach_the_source_in_order(rig):
    r = rig(rotaries=KNOBS)
    r.stream.key("volume-push")
    r.stream.send(turn("volume", 2))
    r.stream.touch("down", 10, 20)
    r.stream.send(turn("tune", -1))
    r.stream.touch("up", 10, 20)
    errors, events = r.settle()
    assert errors == []
    assert events == [
        ("key", "VOLUME_PUSH", "short"),
        ("rotary", "volume", 2),
        ("touch", "down", 10, 20, SIZE),
        ("rotary", "tune", -1),
        ("touch", "up", 10, 20, SIZE),
    ]


def test_a_rotary_the_device_does_not_have_is_an_error_and_nothing_reaches_it(rig):
    r = rig(rotaries=KNOBS)
    r.stream.send(turn("bass", 1))
    r.stream.send(turn("Volume", 1))  # ids are as they are declared
    r.stream.send(turn("volume", 1))
    errors, events = r.settle()
    assert errors == ["this HMI has no rotary bass", "this HMI has no rotary Volume"]
    assert events == [("rotary", "volume", 1)]


def test_a_rotary_when_none_is_declared_is_an_error(rig):
    r = rig()
    r.stream.send(turn("volume", 1))
    errors, events = r.settle()
    assert errors == ["this HMI has no rotary volume"] and events == []


@pytest.mark.parametrize("delta", [0, 101, -101, 1000000, 1.5, 2.0, "2", True, None, [1]])
def test_a_delta_that_is_not_a_whole_number_of_detents_up_to_100_is_an_error(rig, delta):
    r = rig(rotaries=KNOBS)
    r.stream.send(turn("volume", delta))
    errors, events = r.settle()
    assert len(errors) == 1 and "rotary.delta must be a whole number of detents" in errors[0]
    assert events == []


@pytest.mark.parametrize("rotary_id", [None, 3, "", "not an id", "9lives", "x" * 40, ["volume"]])
def test_a_rotary_id_that_is_not_a_name_is_an_error(rig, rotary_id):
    r = rig(rotaries=KNOBS)
    r.stream.send(turn(rotary_id, 1))
    errors, events = r.settle()
    assert errors == ["rotary.id must be the name of a rotary"] and events == []


def test_a_rotary_line_that_is_not_an_object_or_has_no_delta_is_an_error(rig):
    r = rig(rotaries=KNOBS)
    r.stream.send({"rotary": "volume"})
    r.stream.send({"rotary": {"id": "volume"}})
    errors, events = r.settle()
    assert len(errors) == 2 and events == []


def test_a_source_that_cannot_turn_a_rotary_now_is_an_error_and_the_stream_goes_on(rig):
    r = rig(rotaries=KNOBS)
    r.sink.refuse = "busy"
    r.stream.send(turn("volume", 1))
    errors, _ = r.settle()
    assert errors == ["busy"]
    r.sink.refuse = None
    r.stream.send(turn("volume", 1))
    assert r.settle() == ([], [("rotary", "volume", 1)])


def test_a_view_only_hmi_has_no_rotary_even_when_the_source_has_one():
    driver = MockHmi(control=False, rotaries=KNOBS)
    try:
        with serve(driver) as client:
            assert client.hmi_info()["input"]["rotaries"] == []
            with pytest.raises(HmiStreamClosedError, match="takes no input"), client.input():
                pass
            with client.stream("input") as raw, pytest.raises(_STREAM_ENDS):  # asked of the exporter directly
                raw.send(b'{"rotary": {"id": "volume", "delta": 1}}\n')
                raw.receive()
        assert source(driver).sink_input.snapshot() == []
    finally:
        driver.close()


def test_a_hmi_with_a_rotary_and_nothing_else_has_input():
    """A knob is input: the stream opens, and a turn is taken (a pointer, keys and text are refused)."""
    driver = MockHmi(mode="mouse", rotaries=["volume"])
    source(driver).pointer_caps, source(driver).keyboard_caps = PointerCaps(), KeyboardCaps()
    try:
        with serve(driver) as client:
            input_ = client.hmi_info()["input"]
            assert [r["id"] for r in input_["rotaries"]] == ["volume"]
            assert input_["pointer"]["kinds"] == [] and input_["keyboard"]["codes"] == [] and input_["keys"] == []
            with client.video() as video, client.input() as stream:
                media(video)
                stream.send(turn("volume", 3))
                stream.touch("down", 5, 5)
                stream.key("HOME")
                assert settle(stream) == ["this HMI has no pointer", "this HMI has no key HOME"]
                wait_for(lambda: source(driver).sink_input.of("rotary"), what="the turn")
        assert source(driver).sink_input.of("rotary", "touch", "key") == [("rotary", "volume", 3)]
    finally:
        driver.close()


def test_a_stream_with_nothing_declared_is_refused_and_a_rotary_is_what_makes_it_input():
    nothing = MockHmi(mode="mouse")
    source(nothing).pointer_caps, source(nothing).keyboard_caps = PointerCaps(), KeyboardCaps()
    try:
        with serve(nothing) as client:
            assert client.hmi_info()["input"] == NOTHING_DECLARED
            with pytest.raises(HmiStreamClosedError, match="takes no input"), client.input():
                pass
            with client.stream("input") as raw, pytest.raises(_STREAM_ENDS):
                raw.send(b'{"rotary": {"id": "volume", "delta": 1}}\n')
                raw.receive()
    finally:
        nothing.close()


def test_the_push_button_of_a_rotary_is_a_hard_key_that_works(rig):
    r = rig(rotaries=KNOBS)
    r.stream.key("volume-push")
    r.stream.key("VOLUME_PUSH", "long")
    errors, events = r.settle()
    assert errors == []
    assert events == [("key", "VOLUME_PUSH", "short"), ("key", "VOLUME_PUSH", "long")]


def test_the_push_button_takes_the_presses_it_declares_and_no_others(rig):
    r = rig(rotaries=KNOBS)
    r.stream.key("VOLUME_PUSH", "down")  # a push takes short and long unless the exporter says
    errors, events = r.settle()
    assert errors == ["the key VOLUME_PUSH takes short, long, not down"] and events == []
    only_short = rig(rotaries=KNOBS, hard_keys=["HOME", {"key": "VOLUME_PUSH", "presses": ["short"]}])
    only_short.stream.key("VOLUME_PUSH", "long")
    only_short.stream.key("VOLUME_PUSH", "short")
    errors, events = only_short.settle()
    assert errors == ["the key VOLUME_PUSH takes short, not long"] and events == [("key", "VOLUME_PUSH", "short")]


def test_a_rotary_without_a_push_button_has_no_key_and_another_rotarys_push_is_not_its(rig):
    r = rig(rotaries=KNOBS)
    r.stream.send({"key": {"key": "TUNE_PUSH"}})
    errors, events = r.settle()
    assert errors == ["this HMI has no key TUNE_PUSH"] and events == []


def test_the_rotaries_are_in_hmi_info_through_the_client(rig):
    r = rig(rotaries=KNOBS)
    input_ = r.client.hmi_info()["input"]
    assert input_["rotaries"] == [
        {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"},
        {"id": "tune", "label": "Tune", "push": None},
    ]
    assert "VOLUME_PUSH" in input_["keys"]


# -- input: lines that are wrong and a source that fails -------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"touch": 3}',
        b'{"pointer": 3}',
        b'{"pointer": {"type": "tap", "x": 1, "y": 1}}',
        b'{"pointer": {"type": "down", "kind": "finger", "x": 1, "y": 1}}',
        b'{"pointer": {"type": "down", "x": "a", "y": 1}}',
        b'{"pointer": {"type": "down", "x": 1}}',
        b'{"pointer": {"type": "down", "x": 1, "y": 1, "buttons": 64}}',
        b'{"pointer": {"type": "down", "x": 1, "y": 1, "id": -1}}',
        b'{"pointer": {"type": "wheel", "x": 1, "y": 1, "dx": "a"}}',
        b'{"touch": {"type": "down", "x": 1, "y": 1, "w": 240}}',
        b'{"touch": {"type": "down", "x": 1, "y": 1, "w": "a", "h": 1}}',
        b'{"keyboard": {"code": "a key", "type": "down"}}',
        b'{"keyboard": {"code": "KeyA", "type": "hold"}}',
        b'{"key": {"key": "HOME", "press": "tap"}}',
        b'{"key": {"code": "KeyA"}}',
        b'{"key": {"key": ""}}',
        b'{"text": 5}',
        b'{"bogus": {}}',
        b'{"touch": {"type": "down", "x": 1, "y": 1}, "text": "x"}',
    ],
)
def test_a_line_that_is_not_an_event_is_one_error_and_the_next_line_is_read(rig, line):
    r = rig()
    r.stream._raw.send(line + b"\n")
    r.touch("down", 5, 5)
    errors, events = r.settle()
    assert len(errors) == 1 and errors[0]
    assert events == [("touch", "down", 5, 5, SIZE)]


def test_a_fault_in_the_sources_sink_loses_that_event_and_not_the_stream(rig):
    r = rig()
    sink = r.sink
    real = sink.touch

    def broken(*args):
        raise TypeError("a bug in a source")

    sink.touch = broken
    r.touch("down", 5, 5)
    errors, _ = r.settle()
    assert errors == ["the HMI could not take that input"]
    sink.touch = real
    r.touch("up", 5, 5)  # the finger never went down on the device: nothing to lift
    r.touch("down", 6, 6)
    r.touch("up", 6, 6)
    r.stream.key("home")
    errors, events = r.settle()
    assert errors == []
    assert events == [("touch", "down", 6, 6, SIZE), ("touch", "up", 6, 6, SIZE), ("key", "HOME", "short")]


def test_an_input_the_source_refuses_is_answered_and_the_stream_goes_on(rig):
    r = rig()
    r.sink.refuse = "the device is busy"
    r.stream.key("home")
    r.touch("down", 5, 5)
    errors, events = r.settle()
    assert errors == ["the device is busy"] * 2 and events == []
    r.sink.refuse = None
    r.stream.key("back")
    assert r.settle() == ([], [("key", "BACK", "short")])


def test_a_client_that_never_reads_its_errors_does_not_stop_the_input(rig):
    r = rig()
    for _ in range(200):
        r.stream.send({"key": {"key": "NO_SUCH_KEY"}})  # each is an error line nobody reads
    r.stream.key("home")
    wait_for(lambda: ("key", "HOME", "short") in r.sink.snapshot(), what="the key after the errors")


def test_a_line_that_is_too_long_is_an_error_and_the_next_is_read(rig):
    r = rig()
    r.stream._raw.send(b"x" * 200_000 + b"\n")
    r.stream.key("home")
    errors, events = r.settle()
    assert any("longer" in e for e in errors)
    assert events == [("key", "HOME", "short")]


def test_input_waits_for_the_source_to_say_what_its_picture_is_and_is_then_taken():
    """``input`` opens when the capture is connected, which may be before its first codec line: a pointer at once after
    it is put on the picture, not refused for want of one.
    """
    driver = MockHmi(width=240, height=400, fps=60, idle_stop_s=0)
    source(driver).config_delay_s = 0.4
    try:
        with serve(driver) as client, client.input() as stream:  # no viewer has seen the picture yet
            began = time.monotonic()
            stream.touch("down", 5, 5)
            stream.touch("up", 5, 5)
            assert settle(stream) == []
            assert [e[:2] for e in source(driver).sink_input.of("touch")] == [("touch", "down"), ("touch", "up")]
            assert time.monotonic() - began < 5
    finally:
        driver.close()


def test_a_pointer_before_the_source_has_said_what_its_picture_is_is_an_error_not_a_crash():
    """The source's first codec line may come a little after ``start`` returns: nothing can be put on a picture
    that is not known yet.
    """
    driver = MockHmi()
    try:
        driver._ensure_started()
        driver._hub = driver._new_hub()  # a hub that has been given no codec line
        with pytest.raises(InputError, match="no picture yet"):
            driver._pointer(PointerEvent("down", "touch", 1.0, 1.0), _InputState(), PointerCaps.touch())
    finally:
        driver.close()


# -- input: what the source refused is not held -----------------------------------------------------------------------


def test_a_key_the_source_refused_is_not_held_so_the_retry_is_not_dropped_as_a_repeat(rig):
    r = rig(mode="mouse")
    r.sink.refuse = "the keyboard is not ready"
    r.key("KeyA")
    errors, events = r.settle()
    assert errors == ["the keyboard is not ready"] and events == []
    r.sink.refuse = None
    r.key("KeyA")  # the retry
    r.key("KeyA")  # and now it is down: a repeat
    assert r.settle() == ([], [("keyboard", "KeyA", True)])


def test_a_key_up_the_source_refused_is_still_held_and_the_next_up_lets_it_go(rig):
    r = rig(mode="mouse")
    r.key("KeyA")
    r.settle()
    r.sink.refuse = "busy"
    r.key("KeyA", False)
    assert r.settle()[0] == ["busy"]
    r.sink.refuse = None
    r.key("KeyA", False)
    assert r.settle() == ([], [("keyboard", "KeyA", True), ("keyboard", "KeyA", False)])
    assert r.end_input()[-1:] == [("release",)]  # and nothing is left to release


def test_a_key_the_source_would_not_let_go_of_is_released_when_the_input_ends(rig):
    r = rig(mode="mouse")
    r.key("KeyA")
    r.settle()
    r.sink.refuse = "busy"
    r.key("KeyA", False)
    r.settle()
    r.sink.refuse = None
    assert r.end_input()[-2:] == [("keyboard", "KeyA", False), ("release",)]


def test_a_pointer_event_the_source_refused_changes_nothing_the_retry_of_a_down_is_a_down(rig):
    r = rig(mode="mouse")
    r.sink.refuse = "not ready"
    r.pointer("down", 5, 5, buttons=1)
    assert r.settle() == (["not ready"], [])
    r.sink.refuse = None
    r.pointer("down", 5, 5, buttons=1)  # the device never saw the first: this is the down
    r.settle()
    r.sink.refuse = "busy"
    r.pointer("up", 5, 5)
    assert r.settle()[0] == ["busy"]
    r.sink.refuse = None
    r.pointer("up", 5, 5)  # the contact is still down as far as the exporter knows: this lifts it
    assert r.settle() == ([], [ptr("down", "mouse", 5, 5, buttons=1), ptr("up", "mouse", 5, 5)])


def test_a_touch_the_source_refused_is_not_a_finger_that_is_down(rig):
    r = rig()
    r.sink.refuse = "not ready"
    r.touch("down", 5, 5)
    r.settle()
    r.sink.refuse = None
    r.touch("move", 6, 6)  # no finger: nothing
    r.touch("down", 7, 7)  # the retry is a down, not a move
    assert r.settle() == ([], [("touch", "down", 7, 7, SIZE)])


# -- the stream's life -------------------------------------------------------------------------------------------------


def test_the_capture_starts_with_the_first_viewer_and_stops_with_the_lease(hmi, driver):
    assert source(driver).starts == 0
    with hmi.video() as video:
        media(video)
        assert source(driver).starts == 1
        assert hmi.status()["state"] == "running"
    driver.close()
    assert source(driver).stops >= 1
    assert driver.status()["state"] == "stopped"


def test_a_capture_that_dies_ends_the_viewers_and_the_next_one_starts_it_again(hmi, driver):
    with hmi.video() as video:
        media(video)
        source(driver).die("the cable was pulled")
        wait_for(lambda: video.read_frame(5.0) is None, what="the viewer's stream to end")
    assert hmi.status()["error"] == "the cable was pulled"
    with hmi.video() as again:
        assert media(again)
    assert source(driver).starts == 2
    assert hmi.status()["state"] == "running"


def test_a_capture_that_does_not_start_is_an_error_the_viewer_hears_of(hmi, driver):
    source(driver).fail_start = "no device"
    with pytest.raises(HmiStreamClosedError, match="no device"), hmi.video():
        pass
    status = hmi.status()
    assert (status["state"], status["error"]) == ("error", "no device")
    source(driver).fail_start = None
    with hmi.video() as video:
        assert media(video)
    assert hmi.status()["error"] is None


def test_stop_ends_the_viewers_and_a_new_viewer_starts_the_capture_again(hmi, driver):
    with hmi.video() as video:
        media(video)
        assert hmi.stop()["state"] == "stopped"
    assert not source(driver).alive
    with hmi.video() as video:
        assert media(video)
    assert source(driver).starts == 2


def test_stop_is_bounded_even_when_a_start_is_stuck():
    driver = MockHmi(stop_deadline_s=0.5, start_timeout_s=30)
    gate = threading.Event()
    source(driver).start_gate = gate
    starter = threading.Thread(target=lambda: _quietly(driver._ensure_started), daemon=True)
    try:
        starter.start()
        wait_for(lambda: driver._state == "starting", what="the stuck start")
        began = time.monotonic()
        driver.close()
        assert time.monotonic() - began < 5
    finally:
        gate.set()
        starter.join(timeout=5)
        driver.close()


def _quietly(call):
    try:
        call()
    except Exception:  # noqa: BLE001, S110
        pass


def test_the_stream_is_stopped_after_idle_stop_s_with_nobody_watching():
    driver = MockHmi(idle_stop_s=0.2)
    try:
        with serve(driver) as client:
            with client.video() as video:
                media(video)
            wait_for(lambda: client.status()["state"] == "stopped", what="the idle stop")
    finally:
        driver.close()


def test_a_silent_stream_is_restarted_once_a_viewer_has_waited_through_two_stall_periods():
    driver = MockHmi(stall_s=0.3)
    try:
        with serve(driver) as client, client.video() as video:
            media(video)
            source(driver).pause()
            wait_for(lambda: source(driver).starts >= 2, timeout=20, what="the restart")
            source(driver).pause(False)
            assert media(video)
    finally:
        driver.close()


# -- codecs: the stream's, chosen by the option or by start(codec=...) -----------------------------------------------

FAMILY_OF = {"h264": "avc1.", "hevc": "hev1.", "mjpeg": "mjpeg"}


def read_until(video, wanted, timeout: float = 10.0):
    """Frames of the stream until one is ``wanted``: that frame."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = video.read_frame(max(deadline - time.monotonic(), 0.01))
        assert frame is not None, "the stream ended"
        if wanted(frame):
            return frame
    pytest.fail("timed out waiting for a frame")  # ty: ignore[invalid-argument-type]


def is_codec_line(codec: str):
    return lambda frame: isinstance(frame, str) and json.loads(frame)["codec"].startswith(FAMILY_OF[codec])


def first_unit_is_a_key_picture_of(video, codec: str) -> bytes:
    """The next picture of the stream, which must be a key picture of ``codec`` with what a decoder needs."""
    unit = media(video)[0][8:]
    if codec == "mjpeg":
        assert unit[:2] == b"\xff\xd8" and unit[-2:] == b"\xff\xd9"
    else:
        assert has_idr(unit, codec)
        assert first_sps(unit, codec) is not None
    return unit


def test_start_in_a_codec_the_hmi_does_not_produce_is_a_value_error(hmi, driver):
    for codec in ("vp9", "H264", ""):
        with pytest.raises(ValueError, match="this HMI produces h264, hevc, mjpeg"):
            hmi.start(codec=codec)
    assert source(driver).starts == 0  # nothing was started, and nothing is chosen
    assert hmi.hmi_info()["codec"] == "h264"


def test_an_hmi_that_makes_one_codec_refuses_the_others():
    driver = MockHmi(codecs=["mjpeg"])
    try:
        with serve(driver) as client:
            with pytest.raises(ValueError, match="this HMI produces mjpeg, not 'h264'"):
                client.start(codec="h264")
            assert client.start(codec="mjpeg")["state"] == "running"
    finally:
        driver.close()


@pytest.mark.parametrize("codec", ["h264", "hevc", "mjpeg"])
def test_start_with_a_codec_starts_the_stream_in_it(hmi, driver, codec):
    status = hmi.start(codec=codec)
    assert status["state"] == "running"
    assert source(driver).started_codecs == [codec]
    wait_for(lambda: hmi.status()["picture"]["codec"], what="the first codec line")
    assert hmi.status()["picture"]["codec"].startswith(FAMILY_OF[codec])
    info = hmi.hmi_info()
    assert (info["codec"], info["recording"]) == (codec, codec != "mjpeg")
    with hmi.video() as video:
        assert video.header["codec"].startswith(FAMILY_OF[codec])
        assert video.header["format"] == ("jpeg" if codec == "mjpeg" else "annexb")
        first_unit_is_a_key_picture_of(video, codec)
    assert source(driver).starts == 1  # the viewer joined the stream that was started


def test_start_with_another_codec_restarts_a_running_stream_and_its_viewers_get_the_new_codec_line(hmi, driver):
    with hmi.video() as video:
        first_unit_is_a_key_picture_of(video, "h264")
        assert hmi.start(codec="hevc")["state"] == "running"
        read_until(video, is_codec_line("hevc"))
        assert video.header["codec"].startswith("hev1.") and video.header["format"] == "annexb"
        first_unit_is_a_key_picture_of(video, "hevc")  # the new line is followed by a key picture to start at
        assert hmi.hmi_info()["codec"] == "hevc"
        hmi.start(codec="mjpeg")
        read_until(video, is_codec_line("mjpeg"))
        assert video.header["format"] == "jpeg"
        first_unit_is_a_key_picture_of(video, "mjpeg")
        with hmi.video() as joiner:  # a viewer that comes later starts in the codec the stream is in
            assert joiner.header["codec"] == "mjpeg"
            first_unit_is_a_key_picture_of(joiner, "mjpeg")
    assert source(driver).started_codecs == ["h264", "hevc", "mjpeg"]


def test_start_in_the_codec_the_stream_is_in_does_not_restart_it(hmi, driver):
    hmi.start(codec="hevc")
    hmi.start(codec="hevc")
    hmi.start()
    assert source(driver).started_codecs == ["hevc"]


def test_a_codec_that_was_chosen_stays_for_the_lease_across_restarts_and_stops(hmi, driver):
    hmi.start(codec="hevc")
    hmi.set_quality("low")  # a restart for another reason
    wait_for(lambda: source(driver).starts == 2, what="the restart")
    hmi.stop()
    with hmi.video() as video:
        assert video.header["codec"].startswith("hev1.")
    assert source(driver).started_codecs == ["hevc", "hevc", "hevc"]


def test_a_new_lease_starts_in_the_codec_the_exporter_configured_not_the_one_the_last_chose():
    driver = MockHmi(codec="mjpeg", idle_stop_s=0)
    try:
        with serve(driver) as client:
            assert client.hmi_info()["codec"] == "mjpeg"
            client.start(codec="hevc")
            assert client.hmi_info()["codec"] == "hevc"
            driver.reset()  # the next lease
            assert client.hmi_info()["codec"] == "mjpeg"
            with client.video() as video:
                assert video.header["codec"] == "mjpeg"
    finally:
        driver.close()


def test_a_codec_the_source_cannot_start_in_is_not_kept(hmi, driver):
    with hmi.video() as video:
        first_unit_is_a_key_picture_of(video, "h264")
        source(driver).fail_start = "the encoder has no HEVC"
        with pytest.raises(DriverError, match="no HEVC"):
            hmi.start(codec="hevc")
        source(driver).fail_start = None
        assert hmi.hmi_info()["codec"] == "h264"  # the lease is still in the codec it had
        assert hmi.status()["error"] == "the encoder has no HEVC"
    with hmi.video() as again:  # and the next viewer starts it, in H.264
        assert again.header["codec"].startswith("avc1.")
        first_unit_is_a_key_picture_of(again, "h264")
    assert source(driver).started_codecs == ["h264", "h264"]


def test_a_stream_that_cannot_start_in_the_chosen_codec_before_it_ran_leaves_the_choice_alone(hmi, driver):
    source(driver).fail_start = "no device"
    with pytest.raises(DriverError, match="no device"):
        hmi.start(codec="mjpeg")
    source(driver).fail_start = None
    assert hmi.hmi_info()["codec"] == "h264"


def test_a_codec_the_source_declares_but_cannot_make_is_the_sources_error_to_report(driver):
    """The exporter's configured codec is checked against the source's list when the source exists."""
    driver.codec = "hevc"
    source(driver).codecs = ("h264",)
    with pytest.raises(ConfigurationError, match="produces h264, not hevc"):
        driver.hmi_info()
    with pytest.raises(RuntimeError, match="produces h264, not hevc"):
        driver._ensure_started()
    assert driver.status()["state"] == "error"


@pytest.mark.parametrize("codec", ["h264", "hevc", "mjpeg"])
def test_hmi_info_names_the_codec_the_stream_is_in_while_it_runs(hmi, codec):
    with hmi.video() as video:
        hmi.start(codec=codec)
        read_until(video, is_codec_line(codec)) if codec != "h264" else None
        info = hmi.hmi_info()
        assert (info["codec"], info["codecs"], info["recording"]) == (
            codec,
            ["h264", "hevc", "mjpeg"],
            codec != "mjpeg",
        )


def test_an_mjpeg_stream_is_not_recorded_and_the_hmi_says_so(hmi, driver):
    hmi.start(codec="mjpeg")
    assert hmi.hmi_info()["recording"] is False
    with pytest.raises(DriverError, match="MJPEG stream is not recorded"):
        hmi.start_recording()
    assert hmi.recording_status()["active"] is False
    assert hmi.status()["recording"]["active"] is False
    hmi.start(codec="h264")  # in a codec that is recorded it is
    assert hmi.hmi_info()["recording"] is True
    assert hmi.start_recording()["active"] is True
    hmi.stop_recording()


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_an_h264_or_hevc_stream_is_recorded_to_an_mp4_that_says_which(hmi, driver, codec):
    from .recording.mp4_testing import parse_boxes

    hmi.start(codec=codec)
    assert hmi.start_recording(max_seconds=30)["active"]
    wait_for(lambda: hmi.recording_status()["frames"] >= 20, what="recorded frames")
    result = hmi.stop_recording()
    assert result["delivered"] and result["reason"] == "stop"
    boxes = [box.kind for box in parse_boxes(result["data"])]
    assert boxes[0] == "ftyp" and "moov" in boxes and "moof" in boxes
    assert (result["width"], result["height"]) == (240, 400)
    sample_entry = b"hev1" if codec == "hevc" else b"avc1"
    assert sample_entry in result["data"] and (b"hvcC" in result["data"]) == (codec == "hevc")


def test_a_recording_ends_with_codec_changed_when_the_stream_goes_on_in_another_codec(hmi, driver):
    """The file holds one codec: what came before the change is in it, and the reason says why it ended."""
    from .recording.mp4_testing import parse_boxes

    hmi.start_recording(max_seconds=60)
    wait_for(lambda: hmi.recording_status()["frames"] >= 10, what="recorded frames")
    hmi.start(codec="hevc")
    status = wait_for(lambda: (s := hmi.recording_status())["active"] is False and s, what="the recording to end")
    assert status["reason"] == "codec_changed" and status["error"] is None
    result = hmi.stop_recording()
    assert result["reason"] == "codec_changed" and result["frames"] >= 10 and result["delivered"]
    assert "moov" in [box.kind for box in parse_boxes(result["data"])] and b"avc1" in result["data"]
    assert hmi.status()["recording"]["reason"] == "codec_changed"


def test_a_recording_that_the_hub_refuses_leaves_no_file_behind(hmi, driver, monkeypatch):
    """The stream says it is MJPEG though the exporter thought it was recording H.264: the recorder's refusal is the
    caller's, and the directory made for the file is removed again.
    """
    made: list[str] = []
    real = tempfile.mkdtemp
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *args, **kwargs: made.append(real(*args, **kwargs)) or made[-1])
    hmi.start(codec="mjpeg")
    wait_for(lambda: hmi.status()["picture"]["codec"] == "mjpeg", what="the codec line")
    monkeypatch.setattr(driver, "_codec", lambda: "h264")
    with pytest.raises(DriverError, match="MJPEG stream is not recorded"):
        hmi.start_recording()
    assert len(made) == 1 and not os.path.exists(made[0])
    assert driver._recording is None and driver._recording_dir is None
    assert hmi.recording_status()["active"] is False


def test_the_status_says_which_family_the_stream_is_and_counts_what_it_could_not_use(hmi, driver):
    assert hmi.status()["stream"]["family"] is None
    for codec in ("h264", "hevc", "mjpeg"):
        hmi.start(codec=codec)
        wait_for(lambda codec=codec: hmi.status()["stream"]["family"] == codec, what="the codec line")
    assert hmi.status()["stream"]["invalid_units"] == 0


@pytest.mark.parametrize(
    ("codec", "unit"),
    [
        ("hevc", hevc_key_unit(make_hevc_sps(64, 64), b"00000000", 10)),
        ("mjpeg", make_jpeg(64, 64, b"00000000", 10)),
    ],
)
def test_a_source_that_does_not_classify_its_units_has_them_read_in_its_codec(codec, unit):
    """With no hint from the source the hub scans the unit as the codec line's family: an HEVC key picture is a key
    picture, and so is a JPEG.
    """
    hub = Hub()
    session = _Session(MockHmi()._the_source(), hub)
    session.codec = codec
    session.config(CodecLine("hev1.1.6.L93.B0" if codec == "hevc" else "mjpeg", 64, 64, 30))
    viewer = hub.add_viewer()
    session.media(1_000_000, unit)
    frames = []
    while (frame := viewer.pop()) is not None:
        frames.append(frame)
    media_frames = [frame for frame in frames if frame.kind == 1]
    assert len(media_frames) == 1 and media_frames[0].idr


@pytest.mark.parametrize("hints", [(None, None), (True, True), (False, False), (False, True)])
def test_what_a_source_says_of_a_unit_goes_to_the_hub_as_it_is_and_what_it_leaves_out_is_the_hubs_to_find(hints):
    """The hub scans a unit as the codec line's family says (``scan_unit``): the session does not guess for it."""
    published: list[tuple] = []
    hub = SimpleNamespace(publish_media=lambda ts, unit, idr, reference: published.append((unit, idr, reference)))
    session = _Session(MockHmi()._the_source(), cast(Hub, hub))
    session.codec = "hevc"
    unit = hevc_key_unit(make_hevc_sps(64, 64), b"00000000", 10)
    session.media(1_000_000, unit, idr=hints[0], reference=hints[1])
    assert published == [(unit, *hints)]


# -- quality -----------------------------------------------------------------------------------------------------------


def test_a_pinned_level_restarts_the_capture_and_auto_follows_the_network_again(hmi, driver):
    with hmi.video() as video:
        media(video)
        assert source(driver).rungs[0] is not None
        assert hmi.set_quality("low")["quality"]["pinned"] == "low"
        wait_for(lambda: source(driver).starts == 2, what="the restart")
        pinned = source(driver).rungs[1]
        assert pinned is not None
        assert pinned.max_size == 960
        assert hmi.status()["quality"]["mode"] == "manual"
        assert hmi.set_quality("auto")["quality"]["mode"] == "auto"
        wait_for(lambda: source(driver).starts == 3, what="the restart")
        assert media(video)


def test_a_level_by_number_and_a_wrong_one(hmi, driver):
    assert hmi.set_quality(0)["quality"]["pinned"] == "minimal"
    with pytest.raises(ValueError, match="quality must be"):
        hmi.set_quality("ludicrous")
    with pytest.raises(ValueError):
        hmi.set_quality(9)


def test_the_ceilings_cap_the_top_rung():
    driver = MockHmi(width=1920, height=1080, max_fps=15, bit_rate=2_000_000)
    try:
        with serve(driver) as client:
            top = client.status()["quality"]["top"]
            assert (top["bit_rate"], top["max_fps"]) == (2_000_000, 15)
    finally:
        driver.close()


def test_a_hmi_whose_quality_cannot_be_adjusted_says_so():
    driver = MockHmi()
    source(driver)._quality = False
    try:
        with serve(driver) as client:
            assert client.status()["quality"] == {"adjustable": False, "mode": "fixed", "levels": []}
            with pytest.raises(ValueError, match="cannot be adjusted"), client.video():
                client.set_quality("low")
            with client.video():
                pass
        assert source(driver).rungs == [None]
    finally:
        driver.close()


# -- the hub's end of the sink -----------------------------------------------------------------------------------------


def test_timestamps_are_one_clock_that_never_goes_backwards():
    hub = Hub()
    session = _Session(MockHmi()._the_source(), hub)
    session.config(CodecLine("avc1.42C01F", 16, 16, 30))
    sps_idr = KEY_UNIT
    viewer = hub.add_viewer()
    session.media(1_000_000, sps_idr, idr=True)
    for ts in (1_033_000, 1_020_000, 1_066_000):  # the second one is out of order
        session.media(ts, b"\x00\x00\x00\x01\x41\x9aaaaa", idr=False)
    stamps = []
    while (frame := viewer.pop()) is not None:
        if frame.kind == 1:
            stamps.append(frame.ts_us)
    assert stamps == sorted(stamps)
    assert stamps[-1] - stamps[0] == 66_000  # one constant offset, not a reset


def test_a_stale_session_cannot_publish_into_a_new_one():
    hub = Hub()
    session = _Session(MockHmi()._the_source(), hub)
    session.stopped = True
    session.config(CodecLine("avc1.42C01F", 16, 16, 30))
    session.media(1, b"\x00\x00\x00\x01\x65aa", idr=True)
    assert hub.line is None
    assert hub.frames == 0


def test_the_deprecated_lag_calls_find_the_viewer(hmi):
    with hmi.video() as video:
        media(video)
        viewer = video.header["viewer"]
        assert hmi.call("report_lag", viewer, 0.1) is True
        assert hmi.call("report_path", viewer, 0.1) is True
        assert hmi.call("report_lag", viewer + 100, 0.1) is False


def test_the_status_counts_the_viewers_and_what_they_lost(hmi):
    with hmi.video() as video:
        media(video, 5)
        status = hmi.status()
        assert [v["id"] for v in status["viewers"]] == [video.header["viewer"]]
        assert status["picture"]["codec"].startswith("avc1.")
        assert status["stream"]["frames"] >= 5
        assert status["device"]["fake"] is True


def test_the_client_is_a_hmi_client(hmi):
    assert isinstance(hmi, HmiClient)
    assert HmiClient.HMI_STREAM_VERSION == 1


def test_a_joiner_gets_a_key_picture_first(hmi):
    with hmi.video() as first:
        media(first, 40)
        with hmi.video() as second:
            assert NAL_IDR in nal_types(media(second)[0][8:])


@pytest.mark.anyio
async def test_a_pump_that_cannot_send_gives_up_and_closes_the_stream(monkeypatch):
    """A viewer whose client has stopped reading must not keep its stream open until the lease ends."""
    import anyio

    from . import driver as driver_module

    monkeypatch.setattr(driver_module, "SEND_TIMEOUT_S", 0.2)
    hub = Hub()
    hub.publish_config(CodecLine("avc1.42C01F", 16, 16, 30))
    waker = driver_module._Waker()
    viewer = hub.add_viewer(notify=waker.notify)
    unit = KEY_UNIT
    for ts in range(1, 20):
        hub.publish_media(ts * 1000, unit, True)
    _unread, mine = driver_module._pipe(1)  # nobody reads the framework's end
    owner = MockHmi()
    with anyio.fail_after(10):
        await owner._pump(viewer, waker, mine)
    with pytest.raises((anyio.ClosedResourceError, anyio.BrokenResourceError)):
        await mine.send(b"x")
    waker.close()


# -- stops that overtake starts, a loop that must not wait, restarts under an open input -------


def test_a_stop_that_overtakes_a_slow_start_leaves_nothing_running():
    """The lease ends while the capture is still starting: when that start returns, nothing may be left running on a
    dead hub.
    """
    driver = MockHmi(stop_deadline_s=0.4, start_timeout_s=30)
    gate = threading.Event()
    source(driver).start_gate = gate
    outcome = []
    starter = threading.Thread(target=lambda: outcome.append(_attempt(driver._ensure_started)), daemon=True)
    try:
        starter.start()
        wait_for(lambda: driver._state == "starting", what="the stuck start")
        driver.close()  # cannot take the lock: stops what it can without it
        gate.set()  # the start returns now
        starter.join(timeout=10)
        assert not starter.is_alive()
        assert isinstance(outcome[0], RuntimeError)
        assert "stopped while it was starting" in str(outcome[0])
        wait_for(lambda: not source(driver).alive, what="the capture to be let go")
        assert driver.status()["state"] == "stopped"
        assert driver._session is None
    finally:
        gate.set()
        driver.close()


def test_a_stop_that_overtakes_a_restart_leaves_nothing_running():
    """An adaptive step or a stall restart is in progress when the lease ends."""
    driver = MockHmi(stop_deadline_s=0.4, start_timeout_s=30)
    try:
        with serve(driver) as client, client.video() as video:
            media(video)
            gate = threading.Event()
            source(driver).start_gate = gate  # the next start (the restart) waits
            restarter = threading.Thread(target=lambda: _attempt(lambda: client.set_quality("low")), daemon=True)
            restarter.start()
            wait_for(lambda: driver._state == "starting", what="the restart to begin")
            driver.close()
            gate.set()
            restarter.join(timeout=10)
            wait_for(lambda: not source(driver).alive, what="the capture to be let go")
            assert driver._session is None
    finally:
        driver.close()


def _attempt(call):
    try:
        return call()
    except Exception as e:  # noqa: BLE001
        return e


def test_what_answers_a_ui_does_not_wait_for_a_start():
    """hmi_info, status and the lag calls take no lock the start holds, and the source's capabilities are asked
    without it.
    """
    driver = MockHmi()
    try:
        source(driver)  # built
        holder = threading.Event()
        release = threading.Event()

        def hold():
            with driver._lock:
                holder.set()
                release.wait(10)

        thread = threading.Thread(target=hold, daemon=True)
        thread.start()
        holder.wait(5)
        began = time.monotonic()
        assert driver._caps().pointer.max_contacts == 1
        assert driver.hmi_info()["interface"] == "hmi"
        assert driver.status()["state"] == "stopped"
        assert driver.report_lag(1, 0.1) is False
        assert time.monotonic() - began < 2
        release.set()
        thread.join(timeout=5)
    finally:
        driver.close()


def test_status_says_starting_while_the_capture_starts():
    driver = MockHmi(start_timeout_s=30)
    gate = threading.Event()
    source(driver).start_gate = gate
    starter = threading.Thread(target=lambda: _attempt(driver._ensure_started), daemon=True)
    try:
        starter.start()
        wait_for(lambda: driver.status()["state"] == "starting", what="the state to be starting")
    finally:
        gate.set()
        starter.join(timeout=10)
        driver.close()


@pytest.mark.parametrize("cost", ["restart", "none"])
def test_a_source_that_restarts_to_give_a_key_picture_is_never_asked_for_one(cost):
    """Joins and resyncs of a large group of pictures wait for the periodic key picture instead."""
    driver = MockHmi(keyframe_cost=cost, gop=600, key_bytes=2000, join_replay_max_bytes=1, join_wait_s=0.2)
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            for _ in range(3):
                with client.video() as joiner:
                    joiner.request_resync()
                    media(joiner, 2)
            assert source(driver).keyframe_requests == 0
    finally:
        driver.close()


def test_a_cheap_source_is_asked_for_a_key_picture_for_a_joiner_the_cache_cannot_serve():
    driver = MockHmi(keyframe_cost="cheap", gop=600, key_bytes=2000, join_replay_max_bytes=1, join_wait_s=5)
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            with client.video() as joiner:
                assert NAL_IDR in nal_types(media(joiner)[0][8:])
            assert source(driver).keyframe_requests >= 1
    finally:
        driver.close()


# -- what a key picture costs, said by a source that only knows once it has started --------------------------------


def join_asks(driver, client, wait_s: float = 0.0) -> int:
    """How many requests for a key picture a joiner that the cache cannot serve (and a resync) cost the source."""
    before = source(driver).keyframe_requests
    with client.video() as joiner:
        joiner.request_resync()
        media(joiner, 2)
    return source(driver).keyframe_requests - before


def costly_start(**options):
    """A mock with big groups of pictures and a cache too small to serve a joiner: it needs a key picture asked for."""
    return MockHmi(gop=600, key_bytes=2000, join_replay_max_bytes=1, join_wait_s=0.3, **options)


@pytest.mark.parametrize("declared", ["cheap", "restart", "none"])
def test_a_source_that_declares_its_key_picture_cost_up_front_is_taken_at_its_word(declared):
    driver = costly_start(keyframe_cost=declared)
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            assert driver._hub.keyframe_cost == declared
            assert (join_asks(driver, client) >= 1) == (declared == "cheap")
    finally:
        driver.close()


def test_a_source_that_starts_with_none_and_says_cheap_once_started_is_asked_for_key_pictures():
    driver = costly_start(keyframe_cost="none", start_keyframe_cost="cheap")
    try:
        assert driver._the_source().capabilities().keyframe_cost == "none"  # until it has started
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            assert driver._hub.keyframe_cost == "cheap"
            with client.video() as joiner:
                assert NAL_IDR in nal_types(media(joiner)[0][8:])  # a key picture was asked for, not waited for
            assert source(driver).keyframe_requests >= 1
    finally:
        driver.close()


@pytest.mark.parametrize("started", ["restart", "none"])
def test_a_source_that_starts_cheap_and_says_otherwise_once_started_is_no_longer_asked(started):
    driver = costly_start(keyframe_cost="cheap", start_keyframe_cost=started)
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            assert driver._hub.keyframe_cost == started
            for _ in range(3):
                assert join_asks(driver, client) == 0
            assert source(driver).keyframe_requests == 0
    finally:
        driver.close()


@pytest.mark.parametrize(("then", "asks"), [("cheap", True), ("restart", False), ("none", False)])
def test_what_the_source_says_at_a_restart_of_the_capture_is_what_the_hub_goes_by_from_then_on(then, asks):
    driver = costly_start(keyframe_cost="none", start_keyframe_cost="restart" if then == "cheap" else "cheap")
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            assert driver._hub.keyframe_cost == ("restart" if then == "cheap" else "cheap")
            source(driver).start_keyframe_cost = then  # the next start picks another encoder
            client.set_quality("low")  # restarts the capture
            wait_for(lambda: source(driver).starts == 2, what="the restart")
            read_until_media(first)
            assert driver._hub.keyframe_cost == then
            assert (join_asks(driver, client) >= 1) == asks
    finally:
        driver.close()


def read_until_media(video) -> None:
    """Read the viewer on until a picture of the new capture arrives."""
    wait_for(lambda: isinstance(video.read_frame(5.0), bytes), what="a picture of the new capture")


def test_the_hub_is_not_told_again_when_a_restart_says_what_it_said_before():
    driver = costly_start(keyframe_cost="cheap", start_keyframe_cost="cheap")
    try:
        with serve(driver) as client, client.video() as first:
            media(first, 10)
            hub = driver._hub
            client.set_quality("low")
            wait_for(lambda: source(driver).starts == 2, what="the restart")
            read_until_media(first)
            assert driver._hub is hub and hub.keyframe_cost == "cheap"
            assert join_asks(driver, client) >= 1
    finally:
        driver.close()


def test_a_finger_down_when_the_capture_restarts_is_not_carried_into_the_new_one(hmi, driver):
    """After a restart the new capture never saw the finger go down: its moves and up are dropped, a new down works."""
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.touch("down", 10, 10)
        wait_for(lambda: source(driver).sink_input.snapshot(), what="the touch down")
        hmi.set_quality("low")  # restarts the capture
        wait_for(lambda: source(driver).starts == 2, what="the restart")
        wait_for(lambda: isinstance(video.read_frame(5.0), bytes), what="a picture of the new capture")
        stream.touch("move", 11, 11)
        stream.touch("up", 11, 11)
        stream.touch("down", 20, 20)
        wait_for(lambda: len(source(driver).sink_input.snapshot()) >= 2, what="the new down")
    kinds = [e[1] for e in source(driver).sink_input.snapshot() if e[0] == "touch"]
    assert kinds[:2] == ["down", "down"]  # the first capture's, then the new capture's own


def test_an_up_for_a_finger_that_is_not_down_is_dropped(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.touch("up", 5, 5)
        stream.key("home")
        wait_for(lambda: source(driver).sink_input.snapshot(), what="the key")
    assert source(driver).sink_input.snapshot()[0] == ("key", "HOME", "short")


@pytest.mark.parametrize("lag", [float("nan"), float("inf"), -1.0, None, "soon"])
def test_the_deprecated_lag_call_refuses_a_lag_that_cannot_be_one(hmi, driver, lag):
    with hmi.video() as video:
        media(video)
        assert driver.report_lag(video.header["viewer"], lag) is False


def test_a_recording_is_not_a_path_for_the_adaptation(hmi, driver):
    with hmi.video() as video:
        media(video)
        hmi.start_recording()
        wait_for(lambda: hmi.recording_status()["active"], what="the recording")
        assert [s.viewer for s in driver._hub.path_samples(10.0)] == [video.header["viewer"]]
        hmi.stop_recording()


def test_a_viewer_that_never_reads_is_shed_on_its_own_and_the_other_goes_on():
    """A client that has stopped reading is sent to until the viewer's queue gives: then the viewer is shed."""
    driver = MockHmi(fps=60, gop=120, key_bytes=60_000, frame_bytes=40_000, max_lag_s=0.15, max_queue_bytes=300_000)
    try:
        with serve(driver) as client, client.video() as slow, client.video() as fast:
            slow_id = slow.header["viewer"]
            media(fast, 120)  # the slow one never reads

            def shed():
                stats = {v["id"]: v for v in client.status()["viewers"]}.get(slow_id)
                return stats is None or stats["dropped_non_reference"] or stats["dropped_in_resync"] or stats["resyncs"]

            wait_for(shed, what="the slow viewer to be shed")
            assert media(fast, 20)
    finally:
        driver.close()


# -- the real test pattern ----------------------------------------------------------------------------------------


class TestMockHmiPattern(MockConformance):
    """The mock with real pictures passes the same suite."""

    options: ClassVar[dict] = {"pattern": True}


class TestMockHmiPatternHevc(MockConformance):
    """In HEVC."""

    options: ClassVar[dict] = {"pattern": True, "codec": "hevc"}


class TestMockHmiPatternMjpeg(MockConformance):
    """In Motion JPEG."""

    options: ClassVar[dict] = {"pattern": True, "codec": "mjpeg", "mode": "mouse"}


def test_the_pattern_is_real_h264_a_decoder_can_play(tmp_path):
    from .recording.mp4_testing import HAVE_FFMPEG, decode, probe

    if not HAVE_FFMPEG:
        pytest.skip("no ffmpeg")  # ty: ignore[too-many-positional-arguments]
    driver = MockHmi(pattern=True, fps=60, idle_stop_s=0)
    try:
        with serve(driver) as client, client.video() as video:
            assert (video.header["width"], video.header["height"]) == (240, 400)
            assert video.header["codec"] == "avc1.42C01E"
            client.start_recording(max_seconds=30)
            wait_for(lambda: client.recording_status()["frames"] >= 90, what="a loop and a half of the pattern")
            result = client.stop_recording()
        path = tmp_path / "pattern.mp4"
        path.write_bytes(result["data"])
        assert probe(path)["stream"]["width"] == 240
        decoded = decode(path)
        assert decoded.returncode == 0
        # every picture decodes, with nothing said about it (but the null muxer's remark on a frame rate that varies)
        said = [line for line in decoded.stderr.splitlines() if "non monotonically increasing dts" not in line]
        assert not [line for line in said if not line.startswith("    Last message repeated")]
    finally:
        driver.close()
