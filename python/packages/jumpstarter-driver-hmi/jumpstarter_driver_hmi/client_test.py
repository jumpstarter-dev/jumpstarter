"""The client's stream readers and input helpers against scripted streams (no driver, no exporter), and its commands
against a mock HMI.
"""

import concurrent.futures
import json
import logging
import random
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any

import pytest
from anyio import EndOfStream
from click.testing import CliRunner, Result

from .client import (
    BUTTONS,
    ENDED,
    ERROR,
    HmiStreamClosedError,
    InputNotDeclaredError,
    InputStream,
    LagMeter,
    Reporter,
    VideoStream,
    classify,
    declares_input,
    describe,
)
from .common import CodecLine, KeyboardCaps, PointerCaps, pack_media
from .driver import MockHmi
from .testing.conformance import media, wait_for
from jumpstarter.client.core import DriverInvalidArgument
from jumpstarter.common.utils import serve

LINE = CodecLine("avc1.64001F", 540, 1200, 30)
UNIT = b"\x00\x00\x00\x01\x65\x88\x84aaaa"
CANCELLED = "cancelled"
HANG = "hang"


class Portal:
    """Hands out the outcomes the test scripted, one per receive: bytes, an exception, ``CANCELLED`` or ``HANG``."""

    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.pending = []

    def start_task_soon(self, _function):
        future: concurrent.futures.Future = concurrent.futures.Future()
        outcome = self.outcomes.popleft() if self.outcomes else EndOfStream()
        if outcome == HANG:
            self.pending.append(future)
        elif outcome == CANCELLED:
            future.cancel()
        elif isinstance(outcome, BaseException):
            future.set_exception(outcome)
        else:
            future.set_result(outcome)
        return future


class Raw:
    def __init__(self, *outcomes):
        self.portal = Portal(outcomes)
        self.sent = []

    def send(self, data):
        self.sent.append(data)


def reporters() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "hmi-report-lag" and t.is_alive()]


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def header_frame(viewer=3):
    return LINE.frame(viewer)


# -- a cancelled task of the portal is the end of the stream, not an exception -------------------------------------


def test_a_cancelled_portal_task_ends_the_video_stream_quietly():
    raw = Raw(header_frame(), pack_media(1_000_000, UNIT), CANCELLED)
    video = VideoStream(raw, 1.0)
    assert video.header["viewer"] == 3
    assert isinstance(video.read_frame(1.0), str)  # the first frame read is the codec line
    assert isinstance(video.read_frame(1.0), bytes)
    assert video.read_frame(1.0) is None
    assert video.end_reason == ENDED
    assert video.end_error is None
    assert video.read_frame(1.0) is None  # and stays ended
    video.close()


def test_a_cancelled_portal_task_before_the_codec_line_is_a_closed_stream_error():
    with pytest.raises(HmiStreamClosedError) as caught:
        VideoStream(Raw(CANCELLED), 1.0)
    assert caught.value.reason == ENDED


def test_a_cancelled_portal_task_ends_the_input_errors_reader():
    stream = InputStream(Raw(b'{"error": "no"}\n', CANCELLED))
    assert stream.read_error(1.0) == "no"
    assert stream.read_error(1.0) is None
    assert stream.end_reason == ENDED


def test_a_cancelled_task_is_an_ordinary_end_to_classify():
    assert classify(concurrent.futures.CancelledError())[0] == ENDED
    assert classify(RuntimeError("broke"))[0] == ERROR
    assert classify(None)[0] == ENDED


# -- the lag reporter's thread does not outlive a stream that never opened -----------------------------------------


def test_a_timeout_before_the_codec_line_leaves_no_reporter_thread():
    before = len(reporters())
    with pytest.raises(TimeoutError):
        VideoStream(Raw(HANG), 0.05)
    assert len(reporters()) == before


def test_a_stream_that_ends_before_the_codec_line_leaves_no_reporter_thread():
    before = len(reporters())
    with pytest.raises(HmiStreamClosedError):
        VideoStream(Raw(EndOfStream()), 1.0)
    assert len(reporters()) == before


def test_a_codec_line_that_is_not_one_leaves_no_reporter_thread():
    before = len(reporters())
    with pytest.raises(HmiStreamClosedError, match="codec line"):
        VideoStream(Raw(pack_media(1, UNIT)), 1.0)
    assert len(reporters()) == before


def test_the_reporter_starts_with_the_first_report_and_ends_with_the_stream():
    before = len(reporters())
    raw = Raw(header_frame())
    video = VideoStream(raw, 1.0)
    assert len(reporters()) == before  # nothing measured yet: no thread
    video.report_lag(0.4)
    wait_until(lambda: raw.sent)
    assert json.loads(raw.sent[0]) == {"lag": 0.4}
    assert raw.sent[0].endswith(b"\n")
    assert len(reporters()) == before + 1
    video.close()
    wait_until(lambda: len(reporters()) == before)


def test_the_reporter_says_the_worst_lag_of_the_interval_and_at_most_once_in_it():
    said = []
    reporter = Reporter(said.append)
    reporter.INTERVAL_S = 0.2
    try:
        reporter.offer(0.3)
        wait_until(lambda: said == [0.3])
        reporter.offer(0.5)
        reporter.offer(2.0)
        reporter.offer(0.1)
        wait_until(lambda: len(said) == 2)
        assert said[1] == 2.0
    finally:
        reporter.stop()


def test_a_failing_report_does_not_stop_the_reporter():
    calls = []

    def report(lag):
        calls.append(lag)
        raise RuntimeError("refused")

    reporter = Reporter(report)
    reporter.INTERVAL_S = 0.05
    try:
        reporter.offer(1.0)
        wait_until(lambda: len(calls) == 1)
        reporter.offer(2.0)
        wait_until(lambda: len(calls) == 2)
    finally:
        reporter.stop()


def test_resync_is_a_line_on_the_stream():
    raw = Raw(header_frame())
    video = VideoStream(raw, 1.0)
    video.request_resync()
    assert raw.sent == [b'{"resync": true}\n']
    video.close()


def test_a_kind_from_a_newer_exporter_is_skipped_and_a_new_codec_line_is_returned():
    newer = b"\x00\x00\x00\x03\x07ab"
    raw = Raw(header_frame() + newer + CodecLine("avc1.64001F", 100, 200, 30).frame(3) + pack_media(5, UNIT))
    video = VideoStream(raw, 1.0)
    assert isinstance(video.read_frame(1.0), str)  # the codec line it opened with
    first = video.read_frame(1.0)
    assert isinstance(first, str)
    assert json.loads(first)["width"] == 100  # the codec line again, with the kind that is not ours in between
    assert isinstance(video.read_frame(1.0), bytes)
    video.close()


def test_a_read_that_times_out_loses_nothing():
    raw = Raw(header_frame(), HANG)
    video = VideoStream(raw, 1.0)
    assert isinstance(video.read_frame(1.0), str)
    with pytest.raises(TimeoutError):
        video.read_frame(0.05)
    raw.portal.pending[0].set_result(pack_media(9, UNIT))
    frame = video.read_frame(1.0)
    assert isinstance(frame, bytes)
    assert int.from_bytes(frame[:8], "big") == 9
    video.close()


# -- the lag meter -----------------------------------------------------------------------------------------------------


class SlowLagMeter(LagMeter):
    """A reference meter: the least delay of the window found by looking at all of them."""

    def __init__(self):
        super().__init__()
        self._delays = deque()

    def restart(self):
        super().restart()
        self._delays.clear()

    def take(self, now, pts_us):
        if pts_us < self._newest:
            return None
        self._newest = pts_us
        if self._since is None:
            self._since = now
        delay = now - pts_us / 1e6
        self._delays.append((now, delay))
        while self._delays and now - self._delays[0][0] > self.WINDOW_S:
            self._delays.popleft()
        if now - self._since < self.WARM_UP_S:
            return None
        behind = max(0.0, delay - min(d for _, d in self._delays))
        if behind < self.QUIET_S:
            self._behind_since = None
            self.lag_s = behind
        else:
            if self._behind_since is None:
                self._behind_since = now
            self.lag_s = min(behind, self.CLAMP_S) if now - self._behind_since >= self.CONFIRM_S else 0.0
        return self.lag_s


def test_the_lag_meter_with_a_monotonic_window_agrees_with_looking_at_every_delay():
    rng = random.Random(7)
    fast, slow = LagMeter(), SlowLagMeter()
    now, pts = 1000.0, 0
    for step in range(4000):
        now += rng.choice([0.01, 0.033, 0.033, 0.2, 1.5])
        pts += rng.choice([30_000, 33_000, 33_000, 40_000])
        pts_seen = pts + rng.choice([0, 0, 0, -500_000])  # a replayed (older) picture now and then
        if step == 2000:
            fast.restart()
            slow.restart()
        assert fast.take(now, pts_seen) == slow.take(now, pts_seen)
        assert fast.lag_s == slow.lag_s


def test_the_lag_meter_remembers_no_more_than_its_window():
    meter = LagMeter()
    now = 0.0
    for i in range(20_000):
        now += 0.033
        meter.take(
            now, i * 33_000 - i * 10
        )  # a path that slows a little with every picture: every delay is a new minimum
    assert len(meter._minima) <= LagMeter.WINDOW_S / 0.033 + 2


def test_a_picture_that_comes_late_for_good_is_a_lag_and_a_blip_is_not():
    meter = LagMeter()
    now = 0.0
    for i in range(150):
        now += 0.033
        meter.take(now, int(i * 33_000))
    assert meter.lag_s == pytest.approx(0.0, abs=1e-6)
    # the path slows: every picture from now on arrives 2 s later than it was made
    now += 2.0
    lags = []
    for i in range(150, 300):
        now += 0.033
        lags.append(meter.take(now, i * 33_000))
    assert lags[0] == 0.0  # not yet: a delay only counts once it has lasted
    assert meter.lag_s == pytest.approx(2.0, abs=0.1)


# -- the input stream's helpers: what they send, and what they refuse to ---------------------------------------------

NOTHING = {
    "pointer": {"kinds": [], "absolute": False, "relative": False, "buttons": False, "wheel": False, "max_contacts": 0},
    "keyboard": {"codes": [], "text": False, "layout": None},
    "keys": [],
    "hard_keys": [],
    "rotaries": [],
    "clipboard": False,
}
TOUCH = {**NOTHING, "pointer": {**NOTHING["pointer"], "kinds": ["touch"], "absolute": True, "max_contacts": 1}}
MOUSE = {
    **NOTHING,
    "pointer": {
        "kinds": ["mouse"],
        "absolute": True,
        "relative": False,
        "buttons": True,
        "wheel": True,
        "max_contacts": 1,
    },
}
KEYS_ALL = {"codes": "all", "text": True, "layout": "us"}


def declared(base: dict, **parts) -> dict:
    """``base`` (an ``hmi_info()["input"]``) with parts of it replaced: ``pointer={...}`` changes only what it names."""
    out = {**base, **{k: v for k, v in parts.items() if k != "pointer"}}
    if "pointer" in parts:
        out["pointer"] = {**base["pointer"], **parts["pointer"]}
    return out


def stream_of(info: dict | None, size: tuple[int, int] | None = None) -> tuple[InputStream, Raw]:
    raw = Raw()
    return InputStream(raw, info, size), raw


def sent(raw: Raw) -> list[dict]:
    """The events written to the stream, as objects."""
    return [json.loads(line) for chunk in raw.sent for line in chunk.decode().splitlines()]


def touch(kind, x, y, **extra):
    return {"touch": {"type": kind, "x": x, "y": y, "id": 0, **extra}}


def pointer(kind, type_, x=None, y=None, **extra):
    event = {"type": type_, "kind": kind, "id": 0, **extra}
    if x is not None:
        event["x"], event["y"] = x, y
    return {"pointer": event}


VOLUME = {"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"}
TUNE = {"id": "tune", "label": "Tune", "push": None}
KNOBS = declared(NOTHING, keys=["VOLUME_PUSH"], rotaries=[VOLUME, TUNE])


def test_declares_input_is_a_pointer_a_keyboard_a_hard_key_or_a_rotary():
    assert not declares_input({"input": NOTHING})
    assert not declares_input({})
    assert declares_input({"input": declared(NOTHING, rotaries=[TUNE])})
    assert not declares_input({"input": {k: v for k, v in NOTHING.items() if k != "rotaries"}})  # an older exporter
    assert declares_input({"input": TOUCH})
    assert declares_input({"input": declared(NOTHING, keyboard={"codes": ["KeyA"], "text": False, "layout": "us"})})
    assert declares_input({"input": declared(NOTHING, keyboard={"codes": [], "text": True, "layout": "us"})})
    assert declares_input({"input": declared(NOTHING, keys=["HOME"])})


def test_a_tap_on_a_touch_hmi_is_a_finger_down_and_up():
    stream, raw = stream_of(TOUCH)
    stream.tap(10, 20, hold=0, size=(100, 200))
    assert sent(raw) == [touch("down", 10, 20, w=100, h=200), touch("up", 10, 20, w=100, h=200)]


def test_a_tap_on_a_mouse_hmi_is_a_click_of_the_primary_button():
    stream, raw = stream_of(MOUSE)
    stream.tap(10, 20, hold=0)
    assert sent(raw) == [pointer("mouse", "down", 10, 20, buttons=1), pointer("mouse", "up", 10, 20, buttons=0)]


def test_a_tap_on_a_pen_hmi_is_the_tip_down_and_up():
    stream, raw = stream_of(declared(MOUSE, pointer={"kinds": ["pen"]}))
    stream.tap(1, 2, hold=0)
    assert [e["pointer"]["kind"] for e in sent(raw)] == ["pen", "pen"]


def test_a_touch_screen_is_preferred_to_a_mouse_for_a_tap_when_both_are_declared():
    stream, raw = stream_of(declared(MOUSE, pointer={"kinds": ["mouse", "touch"]}))
    stream.tap(1, 2, hold=0)
    assert [next(iter(e)) for e in sent(raw)] == ["touch", "touch"]


@pytest.mark.parametrize("call", ["tap", "swipe", "long_press"])
def test_the_pointer_helpers_say_so_when_there_is_no_pointer_and_send_nothing(call):
    stream, raw = stream_of(NOTHING)
    with pytest.raises(InputNotDeclaredError, match="declares no pointer"):
        {
            "tap": lambda: stream.tap(1, 2, hold=0),
            "swipe": lambda: stream.swipe(1, 2, 3, 4, seconds=0),
            "long_press": lambda: stream.long_press(1, 2, seconds=0),
        }[call]()
    assert raw.sent == []


def test_a_swipe_is_a_finger_down_moved_in_steps_and_up():
    stream, raw = stream_of(TOUCH)
    stream.swipe(0, 0, 100, 40, seconds=0, steps=4)
    assert sent(raw) == [
        touch("down", 0, 0),
        touch("move", 25, 10),
        touch("move", 50, 20),
        touch("move", 75, 30),
        touch("move", 100, 40),
        touch("up", 100, 40),
    ]


def test_a_swipe_on_a_mouse_hmi_drags_with_the_primary_button():
    stream, raw = stream_of(MOUSE)
    stream.swipe(0, 0, 10, 0, seconds=0, steps=1)
    assert sent(raw) == [
        pointer("mouse", "down", 0, 0, buttons=1),
        pointer("mouse", "move", 10, 0, buttons=1),
        pointer("mouse", "up", 10, 0, buttons=0),
    ]


def test_a_long_press_holds_for_the_time_it_is_given():
    stream, raw = stream_of(TOUCH)
    began = time.monotonic()
    stream.long_press(5, 6, seconds=0.2)
    assert time.monotonic() - began >= 0.2
    assert [e["touch"]["type"] for e in sent(raw)] == ["down", "up"]


@pytest.mark.parametrize(
    ("button", "bits"),
    [
        ("primary", 1),
        ("left", 1),
        ("secondary", 2),
        ("right", 2),
        ("auxiliary", 4),
        ("middle", 4),
        ("back", 8),
        ("forward", 16),
        (2, 2),
    ],
)
def test_a_click_presses_and_lets_go_of_the_button_it_is_given(button, bits):
    stream, raw = stream_of(MOUSE)
    stream.click(30, 40, button, hold=0)
    assert sent(raw) == [pointer("mouse", "down", 30, 40, buttons=bits), pointer("mouse", "up", 30, 40, buttons=0)]


def test_the_button_names_are_the_bits_of_pointer_event_buttons():
    assert BUTTONS == {
        "primary": 1,
        "left": 1,
        "secondary": 2,
        "right": 2,
        "auxiliary": 4,
        "middle": 4,
        "back": 8,
        "forward": 16,
    }


@pytest.mark.parametrize("button", ["fourth", "", 0, 64, True, None])
def test_a_button_that_is_none_is_a_value_error_and_nothing_is_sent(button):
    stream, raw = stream_of(MOUSE)
    with pytest.raises(ValueError, match="a button is one of"):
        stream.click(1, 2, button)
    assert raw.sent == []


def test_a_click_of_another_button_needs_a_mouse_that_declares_buttons():
    stream, raw = stream_of(declared(MOUSE, pointer={"buttons": False}))
    stream.click(1, 2, "primary", hold=0)
    with pytest.raises(InputNotDeclaredError, match="no button but the primary"):
        stream.click(1, 2, "secondary")
    assert len(raw.sent) == 2  # only the primary click


def test_a_click_on_a_touch_screen_points_to_tap():
    stream, raw = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="touch, not mouse"):
        stream.click(1, 2)
    assert raw.sent == []


def test_a_double_click_is_two_clicks():
    stream, raw = stream_of(MOUSE)
    stream.double_click(5, 6, "secondary", interval=0)
    assert [(e["pointer"]["type"], e["pointer"]["buttons"]) for e in sent(raw)] == [
        ("down", 2),
        ("up", 0),
        ("down", 2),
        ("up", 0),
    ]


def test_a_move_is_a_hover_with_no_button():
    stream, raw = stream_of(MOUSE)
    stream.move(7, 8, size=(10, 10))
    assert sent(raw) == [pointer("mouse", "move", 7, 8, w=10, h=10)]


def test_a_move_needs_a_mouse_that_takes_positions():
    stream, raw = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="no hover"):
        stream.move(1, 2)
    stream, raw = stream_of(declared(MOUSE, pointer={"absolute": False, "relative": True}))
    with pytest.raises(InputNotDeclaredError, match="move_by"):
        stream.move(1, 2)
    assert raw.sent == []


def test_a_move_by_is_relative_motion_with_no_position():
    stream, raw = stream_of(declared(MOUSE, pointer={"relative": True}))
    stream.move_by(5, -3)
    assert sent(raw) == [{"pointer": {"type": "move", "kind": "mouse", "id": 0, "dx": 5, "dy": -3}}]
    stream, raw = stream_of(MOUSE)
    with pytest.raises(InputNotDeclaredError, match="no relative motion"):
        stream.move_by(1, 1)
    assert raw.sent == []


def test_a_drag_holds_the_button_it_is_given_through_the_moves():
    stream, raw = stream_of(MOUSE)
    stream.drag(0, 0, 20, 0, "secondary", seconds=0, steps=2)
    assert sent(raw) == [
        pointer("mouse", "down", 0, 0, buttons=2),
        pointer("mouse", "move", 10, 0, buttons=2),
        pointer("mouse", "move", 20, 0, buttons=2),
        pointer("mouse", "up", 20, 0, buttons=0),
    ]


def test_a_drag_on_a_touch_screen_points_to_swipe():
    stream, raw = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="use swipe"):
        stream.drag(0, 0, 1, 1)
    assert raw.sent == []


def test_a_scroll_turns_the_wheel_at_a_point_with_the_sign_of_the_delta():
    stream, raw = stream_of(MOUSE, size=(200, 100))
    stream.scroll(0, 120, 10, 20)
    stream.scroll(-30, -60)  # no point: where the pointer last was
    assert sent(raw) == [
        pointer("mouse", "wheel", 10, 20, dy=120),
        pointer("mouse", "wheel", 10, 20, dx=-30, dy=-60),
    ]


def test_a_scroll_with_no_point_and_no_pointer_yet_is_at_the_center_of_the_picture():
    stream, raw = stream_of(MOUSE, size=(200, 100))
    stream.scroll(0, 5)
    assert sent(raw) == [pointer("mouse", "wheel", 100, 50, dy=5)]


def test_a_scroll_with_no_point_to_scroll_at_is_a_value_error():
    stream, raw = stream_of(MOUSE)
    with pytest.raises(ValueError, match="needs x and y"):
        stream.scroll(0, 5)
    assert raw.sent == []


def test_a_scroll_needs_a_pointer_with_a_wheel():
    stream, raw = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="no wheel"):
        stream.scroll(0, 5, 1, 1)
    stream, raw = stream_of(declared(MOUSE, pointer={"wheel": False}))
    with pytest.raises(InputNotDeclaredError, match="no wheel"):
        stream.scroll(0, 5, 1, 1)
    assert raw.sent == []


def test_the_pointer_remembers_where_it_was_put():
    stream, _ = stream_of(MOUSE)
    assert stream.position is None
    stream.move(3, 4)
    assert stream.position == (3, 4)
    stream.click(9, 8, hold=0)
    assert stream.position == (9, 8)


def test_key_down_and_up_send_the_keyboards_codes():
    stream, raw = stream_of(declared(NOTHING, keyboard=KEYS_ALL))
    stream.key_down("KeyA")
    stream.key_up("KeyA")
    assert sent(raw) == [
        {"keyboard": {"code": "KeyA", "type": "down"}},
        {"keyboard": {"code": "KeyA", "type": "up"}},
    ]


def test_a_key_the_keyboard_does_not_list_is_refused_and_nothing_is_sent():
    stream, raw = stream_of(declared(NOTHING, keyboard={"codes": ["KeyA", "Enter"], "text": False, "layout": "us"}))
    stream.key_down("Enter")
    for call in (lambda: stream.key_down("KeyB"), lambda: stream.key_up("KeyB"), lambda: stream.press("KeyB")):
        with pytest.raises(InputNotDeclaredError, match="does not take the key KeyB"):
            call()
    assert len(raw.sent) == 1


def test_a_hmi_that_only_types_text_has_no_key_codes_and_says_to_use_text():
    stream, raw = stream_of(declared(NOTHING, keyboard={"codes": [], "text": True, "layout": "us"}))
    with pytest.raises(InputNotDeclaredError, match="use text"):
        stream.key_down("KeyA")
    stream, raw = stream_of(NOTHING)
    with pytest.raises(InputNotDeclaredError, match="takes no key codes$"):
        stream.press("KeyA")
    assert raw.sent == []


def test_a_press_is_down_and_up_with_the_modifiers_held_around_it():
    stream, raw = stream_of(declared(NOTHING, keyboard=KEYS_ALL))
    stream.press("KeyA", modifiers=("ControlLeft", "ShiftLeft"), hold=0)
    assert [(e["keyboard"]["code"], e["keyboard"]["type"]) for e in sent(raw)] == [
        ("ControlLeft", "down"),
        ("ShiftLeft", "down"),
        ("KeyA", "down"),
        ("KeyA", "up"),
        ("ShiftLeft", "up"),
        ("ControlLeft", "up"),
    ]


def test_a_press_sends_nothing_when_one_of_its_keys_is_not_taken():
    stream, raw = stream_of(declared(NOTHING, keyboard={"codes": ["KeyA"], "text": False, "layout": "us"}))
    with pytest.raises(InputNotDeclaredError, match="ShiftLeft"):
        stream.press("KeyA", modifiers=("ShiftLeft",))
    assert raw.sent == []


def test_a_hard_key_is_sent_as_it_is_and_the_exporter_says_whether_the_hmi_has_it():
    stream, raw = stream_of(declared(NOTHING, keys=["HOME", "APP_SWITCH"]))
    stream.key("home")
    stream.key("recents", "long")
    stream.key("MUTE")  # not one it has: the answer is the exporter's, an error line
    assert sent(raw) == [
        {"key": {"key": "home", "press": "short"}},
        {"key": {"key": "recents", "press": "long"}},
        {"key": {"key": "MUTE", "press": "short"}},
    ]


@pytest.mark.parametrize(
    ("delta", "lines"),
    [(1, [1]), (-2, [-2]), (100, [100]), (-100, [-100]), (101, [100, 1]), (-250, [-100, -100, -50]), (3.0, [3])],
)
def test_a_rotary_turn_is_a_line_and_one_beyond_a_hundred_detents_is_several(delta, lines):
    stream, raw = stream_of(KNOBS)
    stream.rotate("volume", delta)
    assert sent(raw) == [{"rotary": {"id": "volume", "delta": d}} for d in lines]


def test_a_rotary_the_hmi_does_not_have_is_refused_and_nothing_is_sent():
    stream, raw = stream_of(KNOBS)
    with pytest.raises(InputNotDeclaredError, match="no rotary bass.*volume, tune"):
        stream.rotate("bass", 1)
    with pytest.raises(InputNotDeclaredError, match="no rotary bass"):
        stream.push("bass")
    stream, raw2 = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="declares no rotary"):
        stream.rotate("volume", 1)
    with pytest.raises(InputNotDeclaredError, match="declares no rotary"):
        stream.push("volume")
    stream, raw3 = stream_of({k: v for k, v in TOUCH.items() if k != "rotaries"})  # an exporter that has none
    with pytest.raises(InputNotDeclaredError, match="declares no rotary"):
        stream.rotate("volume", 1)
    assert raw.sent == raw2.sent == raw3.sent == []


@pytest.mark.parametrize("delta", [0, 0.0, 1.5, float("nan"), float("inf"), True, None, "2", [1]])
def test_a_turn_that_is_not_a_whole_number_of_detents_is_a_value_error_and_nothing_is_sent(delta):
    stream, raw = stream_of(KNOBS)
    with pytest.raises(ValueError, match="whole number of detents"):
        stream.rotate("volume", delta)
    assert raw.sent == []


def test_a_push_presses_the_hard_key_the_rotary_names():
    stream, raw = stream_of(KNOBS)
    stream.push("volume")
    stream.push("volume", "long")
    stream.push("volume", press="down")
    assert sent(raw) == [
        {"key": {"key": "VOLUME_PUSH", "press": "short"}},
        {"key": {"key": "VOLUME_PUSH", "press": "long"}},
        {"key": {"key": "VOLUME_PUSH", "press": "down"}},
    ]


def test_a_rotary_that_has_no_push_button_cannot_be_pushed():
    stream, raw = stream_of(KNOBS)
    with pytest.raises(InputNotDeclaredError, match="rotary tune has no push button"):
        stream.push("tune")
    assert raw.sent == []


def test_a_stream_that_does_not_know_the_rotaries_turns_but_cannot_push():
    stream, raw = stream_of(None)
    stream.rotate("anything", -3)  # the exporter is the one to say
    with pytest.raises(InputNotDeclaredError, match="named by hmi_info"):
        stream.push("anything")
    assert sent(raw) == [{"rotary": {"id": "anything", "delta": -3}}]


def test_the_raw_send_takes_a_rotary_line_as_it_is():
    stream, raw = stream_of(NOTHING)
    stream.send({"rotary": {"id": "nothing-declared", "delta": 500}})
    assert sent(raw) == [{"rotary": {"id": "nothing-declared", "delta": 500}}]


def test_text_needs_an_hmi_that_types_it():
    stream, raw = stream_of(declared(NOTHING, keyboard=KEYS_ALL))
    stream.text("héllo")
    assert sent(raw) == [{"text": "héllo"}]
    stream, raw = stream_of(TOUCH)
    with pytest.raises(InputNotDeclaredError, match="does not take text"):
        stream.text("x")
    assert raw.sent == []


def test_the_raw_events_are_sent_whatever_the_hmi_declares():
    stream, raw = stream_of(NOTHING)  # the exporter is the one to say no
    stream.touch("down", 1, 2, id=3, size=(10, 20))
    stream.pointer("wheel", 5, 6, kind="mouse", dy=-120, size=(10, 20))
    stream.pointer("move", kind="mouse", dx=1, dy=2)
    stream.keyboard("KeyZ", True)
    stream.send({"key": {"key": "NO_SUCH_KEY"}})
    assert sent(raw) == [
        {"touch": {"type": "down", "x": 1, "y": 2, "id": 3, "w": 10, "h": 20}},
        pointer("mouse", "wheel", 5, 6, dy=-120, w=10, h=20),
        {"pointer": {"type": "move", "kind": "mouse", "id": 0, "dx": 1, "dy": 2}},
        {"keyboard": {"code": "KeyZ", "type": "down"}},
        {"key": {"key": "NO_SUCH_KEY"}},
    ]


def test_a_stream_made_without_what_the_hmi_declares_checks_nothing():
    stream, raw = stream_of(None)
    stream.scroll(0, 1, 5, 5)
    stream.key("ANYTHING")
    stream.text("x")
    stream.press("KeyA", modifiers=("ShiftLeft",), hold=0)
    stream.click(1, 2, "back", hold=0)
    assert len(sent(raw)) == 1 + 1 + 1 + 4 + 2


def test_a_closed_stream_is_the_streams_own_error():
    class Closed(Raw):
        def send(self, data):
            raise concurrent.futures.CancelledError()

    stream = InputStream(Closed(), TOUCH)
    with pytest.raises(HmiStreamClosedError, match="closed"):
        stream.tap(1, 2, hold=0)


# -- the client against a mock HMI: the input stream's declared checks, start(codec) -----------------------------------


@contextmanager
def serving(**options: Any):
    """``(client, driver)`` of a mock HMI that shows its picture."""
    merged: dict[str, Any] = {"width": 240, "height": 400, "fps": 60, "idle_stop_s": 0, **options}
    driver = MockHmi(**merged)
    try:
        with serve(driver) as client:
            yield client, driver
    finally:
        driver.close()


def events(driver, *kinds):
    return driver._the_source().sink_input.of(*kinds)


def test_the_helpers_check_against_the_hmi_they_are_connected_to():
    with serving() as (client, driver), client.video() as video, client.input() as stream:  # a touch screen
        media(video)
        stream.tap(10, 20, hold=0)
        with pytest.raises(InputNotDeclaredError, match="no wheel"):
            stream.scroll(0, 120)
        with pytest.raises(InputNotDeclaredError, match="takes no key codes"):
            stream.press("KeyA")
        stream.key("home")
        wait_for(lambda: len(events(driver, "touch", "key")) == 3, what="the events")
        assert stream.poll_errors(0.2) == []


KNOBS_OPTION = [{"id": "volume", "label": "Volume", "push": "VOLUME_PUSH"}, "tune"]


def test_the_rotary_helpers_reach_a_mock_with_knobs_and_check_against_what_it_declares():
    with serving(rotaries=KNOBS_OPTION) as (client, driver), client.video() as video, client.input() as stream:
        media(video)
        stream.rotate("volume", -2)
        stream.rotate("tune", 250)  # three lines
        stream.push("volume", "long")
        with pytest.raises(InputNotDeclaredError, match="no rotary bass"):
            stream.rotate("bass", 1)
        with pytest.raises(InputNotDeclaredError, match="no push button"):
            stream.push("tune")
        wait_for(lambda: len(events(driver, "rotary", "key")) == 5, what="the events")
        assert stream.poll_errors(0.2) == []
        got = events(driver, "rotary", "key")
    assert got == [
        ("rotary", "volume", -2),
        ("rotary", "tune", 100),
        ("rotary", "tune", 100),
        ("rotary", "tune", 50),
        ("key", "VOLUME_PUSH", "long"),
    ]


def test_a_rotary_alone_opens_the_input_stream():
    with serving(mode="mouse", rotaries=["dial"]) as (client, driver):
        source = driver._the_source()
        source.pointer_caps, source.keyboard_caps = PointerCaps(), KeyboardCaps()
        with client.video() as video, client.input() as stream:
            media(video)
            stream.rotate("dial", 1)
            with pytest.raises(InputNotDeclaredError, match="no pointer"):
                stream.tap(1, 1)
            wait_for(lambda: events(driver, "rotary"), what="the turn")


def test_a_mouse_hmi_takes_clicks_scrolls_drags_keys_and_text_through_the_helpers():
    with serving(mode="mouse", relative=True) as (client, driver), client.video() as video, client.input() as stream:
        media(video)
        stream.click(10, 20, "secondary", hold=0)
        stream.double_click(11, 21, interval=0)
        stream.move(30, 40)
        stream.move_by(2, 3)
        stream.drag(1, 1, 5, 5, seconds=0, steps=1)
        stream.scroll(0, 100, 50, 60)
        stream.press("KeyA", modifiers=("ShiftLeft",), hold=0)
        stream.text("hi")
        wait_for(lambda: events(driver, "text"), what="the last event")
        assert stream.poll_errors(0.3) == []
        got = events(driver, "pointer", "keyboard", "text")
    assert [(e[0], e[1]) for e in got if e[0] == "pointer"][:2] == [("pointer", "down"), ("pointer", "up")]
    assert [e[1] for e in got if e[0] == "keyboard"] == ["ShiftLeft", "KeyA", "KeyA", "ShiftLeft"]
    assert got[-1] == ("text", "hi")
    wheel = [e for e in got if e[0] == "pointer" and e[1] == "wheel"]
    assert len(wheel) == 1 and wheel[0][3:5] == (50.0, 60.0) and wheel[0][8] == 100.0


def test_a_view_only_hmi_has_no_input_stream():
    with (
        serving(control=False) as (client, _),
        pytest.raises(HmiStreamClosedError, match="takes no input"),
        client.input(),
    ):
        pass


def test_start_takes_a_codec_and_a_wrong_one_is_a_value_error():
    with serving() as (client, _):
        assert client.start(codec="hevc")["state"] == "running"
        assert client.hmi_info()["codec"] == "hevc"
        with pytest.raises(DriverInvalidArgument, match="produces"):
            client.start(codec="vp9")
        assert client.start()["state"] == "running"  # no codec said: the lease stays in the one it chose
        assert client.hmi_info()["codec"] == "hevc"


# -- the commands -----------------------------------------------------------------------------------------------------


def run(client, *args: str) -> Result:
    """Run ``j <hmi> <args>`` in process, with logging off (all of it) while it runs.

    A package's live logging (``log_cli``) suspends and resumes pytest's output capture around every record, and
    resuming it replaces the ``sys.stdout`` the runner swapped in: a record logged by the driver while a command runs
    would send the command's output to pytest instead of to the runner.
    """
    logging.disable(logging.CRITICAL)  # a command that fails makes the driver log a warning
    try:
        return CliRunner().invoke(client.cli(), list(args), catch_exceptions=False)
    finally:
        logging.disable(logging.NOTSET)


def test_info_prints_what_a_touch_screen_declares_in_words():
    with serving() as (client, _):
        out = run(client, "info").output
    assert "picture: not started, rotation 0" in out
    assert "codecs: h264 (in use), hevc, mjpeg; recorded to MP4" in out
    assert "pointer: touch; absolute; 1 contact\n  with no buttons or wheel" in out
    assert "keyboard: no key codes; types text (us layout)" in out
    assert "hard keys: HOME (short/long), BACK (short/long), APP_SWITCH (short/long), VOLUME_UP (short/long)" in out
    assert "also takes: VOLUME_DOWN" in out
    assert "clipboard: no" in out
    assert "quality: minimal, low, medium, high, auto" in out


def test_info_prints_what_a_mouse_declares_in_words_and_the_codec_in_use():
    with serving(mode="mouse", relative=True, codec="mjpeg") as (client, _):
        out = run(client, "info").output
    assert "codecs: h264, hevc, mjpeg (in use); not recorded" in out
    assert "pointer: mouse; absolute and relative; 1 contact\n  with buttons, wheel" in out
    assert "keyboard: every key; types text (us layout)" in out
    assert "hard keys: none" in out


def test_info_prints_the_rotaries_and_leaves_their_push_buttons_out_of_the_other_keys():
    with serving(rotaries=KNOBS_OPTION) as (client, _):
        out = run(client, "info").output
    assert "rotaries: volume (Volume, push VOLUME_PUSH), tune (Tune)" in out
    assert "also takes: VOLUME_DOWN\n" in out  # the push is the knob's, not "also taken"
    assert "input: none" not in out


def test_info_says_when_there_are_no_rotaries():
    with serving() as (client, _):
        assert "rotaries: none" in run(client, "info").output


def test_info_says_a_hmi_with_only_a_rotary_is_not_view_only():
    with serving(mode="mouse", rotaries=["dial"]) as (client, driver):
        source = driver._the_source()
        source.pointer_caps, source.keyboard_caps = PointerCaps(), KeyboardCaps()
        out = run(client, "info").output
    assert "rotaries: dial (Dial)" in out and "pointer: none" in out and "keyboard: none" in out
    assert "view-only" not in out


def test_info_says_a_view_only_hmi_is_one():
    with serving(control=False) as (client, _):
        out = run(client, "info").output
    assert "pointer: none" in out and "keyboard: none" in out and "hard keys: none" in out
    assert "rotaries: none" in out
    assert "input: none (a view-only HMI)" in out


def test_info_json_is_the_hmi_info():
    with serving() as (client, _):
        assert json.loads(run(client, "info", "--json").output) == client.hmi_info()


def test_describe_names_the_picture_once_there_is_one():
    with serving() as (client, _), client.video() as video:
        media(video)
        assert "picture: 240x400, rotation 0" in describe(client.hmi_info())


def test_start_takes_a_codec_option():
    with serving() as (client, _):
        result = run(client, "start", "--codec", "hevc")
        assert result.exit_code == 0 and json.loads(result.output)["state"] == "running"
        assert client.hmi_info()["codec"] == "hevc"
        assert run(client, "start", "--codec", "vp9").exit_code == 2  # not a codec the interface has


def test_start_with_a_codec_the_hmi_does_not_produce_says_so():
    with serving(codecs=["h264"]) as (client, _):
        result = run(client, "start", "--codec", "hevc")
        assert result.exit_code == 1 and "produces h264, not 'hevc'" in result.output


def test_view_starts_the_stream_in_the_codec_asked_for_and_then_serves(monkeypatch):
    served = []
    monkeypatch.setattr("jumpstarter_driver_hmi.web.serve_view", lambda client, **kwargs: served.append(kwargs))
    with serving() as (client, _):
        assert run(client, "view", "--codec", "mjpeg", "--no-open", "--port", "1234").exit_code == 0
        assert client.hmi_info()["codec"] == "mjpeg"
        assert served == [{"port": 1234, "open_browser": False}]
        served.clear()
        assert run(client, "view", "--no-open").exit_code == 0
        assert client.hmi_info()["codec"] == "mjpeg" and len(served) == 1  # no codec said: left as it is


def test_view_with_a_codec_the_hmi_does_not_produce_does_not_serve(monkeypatch):
    served = []
    monkeypatch.setattr("jumpstarter_driver_hmi.web.serve_view", lambda client, **kwargs: served.append(kwargs))
    with serving(codecs=["h264"]) as (client, _):
        result = run(client, "view", "--codec", "hevc")
        assert result.exit_code == 1 and "produces" in result.output and served == []


def test_the_touch_commands_reach_a_touch_screen():
    with serving() as (client, driver):
        assert run(client, "tap", "10", "20").exit_code == 0
        assert run(client, "swipe", "1", "2", "30", "40", "--seconds", "0").exit_code == 0
        assert run(client, "key", "home", "--press", "long").exit_code == 0
        assert run(client, "text", "hello").exit_code == 0
        got = events(driver, "touch", "key", "text")
    assert [e[:2] for e in got[:2]] == [("touch", "down"), ("touch", "up")]
    assert (got[0][2:4], got[1][2:4]) == ((10, 20), (10, 20))
    swipe = [e for e in got if e[0] == "touch"][2:]
    assert swipe[0][1] == "down" and swipe[-1][1] == "up" and swipe[-1][2:4] == (30, 40)
    assert ("key", "HOME", "long") in got and ("text", "hello") in got


def test_the_mouse_commands_reach_a_mouse():
    with serving(mode="mouse") as (client, driver):
        assert run(client, "click", "10", "20").exit_code == 0
        assert run(client, "click", "11", "21", "--button", "right").exit_code == 0
        assert run(client, "click", "12", "22", "--double").exit_code == 0
        assert run(client, "scroll", "0", "120", "--at", "50", "60").exit_code == 0
        assert run(client, "scroll", "0", "-30").exit_code == 0
        assert run(client, "press", "KeyA", "--modifier", "ShiftLeft").exit_code == 0
        assert run(client, "text", "hi").exit_code == 0
        got = events(driver, "pointer", "keyboard", "text")
    clicks = [(e[1], e[6]) for e in got if e[0] == "pointer" and e[1] in ("down", "up")]
    assert clicks == [("down", 1), ("up", 0), ("down", 2), ("up", 0)] + [("down", 1), ("up", 0)] * 2
    wheels = [(e[3:5], e[7:9]) for e in got if e[0] == "pointer" and e[1] == "wheel"]
    assert wheels == [((50.0, 60.0), (0.0, 120.0)), ((120.0, 200.0), (0.0, -30.0))]  # no --at: the picture's center
    assert [e[1:] for e in got if e[0] == "keyboard" and e[2]] == [("ShiftLeft", True), ("KeyA", True)]
    assert got[-1] == ("text", "hi")


def test_the_rotate_and_push_commands_reach_a_mock_with_knobs():
    with serving(rotaries=KNOBS_OPTION) as (client, driver):
        assert run(client, "turn", "volume", "3").exit_code == 0
        assert run(client, "turn", "volume", "-2").exit_code == 0  # a negative number is not an option
        assert run(client, "turn", "tune", "130").exit_code == 0
        assert run(client, "push", "volume").exit_code == 0
        assert run(client, "push", "volume", "--press", "long").exit_code == 0
        got = events(driver, "rotary", "key")
    assert got == [
        ("rotary", "volume", 3),
        ("rotary", "volume", -2),
        ("rotary", "tune", 100),
        ("rotary", "tune", 30),
        ("key", "VOLUME_PUSH", "short"),
        ("key", "VOLUME_PUSH", "long"),
    ]


def test_rotate_and_push_say_why_they_cannot_and_exit_with_an_error():
    with serving(rotaries=KNOBS_OPTION) as (client, driver):
        for args, why in (
            (("turn", "bass", "1"), "no rotary bass"),
            (("turn", "volume", "0"), "whole number of detents"),
            (("push", "tune"), "no push button"),
            (("push", "bass"), "no rotary bass"),
            (("push", "volume", "--press", "down"), "takes short, long, not down"),  # the exporter's answer
        ):
            result = run(client, *args)
            assert result.exit_code == 1 and why in result.output, (args, result.output)
        assert run(client, "turn", "volume", "x").exit_code == 2  # not a number
        assert run(client, "push", "volume", "--press", "tap").exit_code == 2
        assert events(driver, "rotary", "key") == []
    with serving() as (client, _):  # a touch screen with no knobs
        result = run(client, "turn", "volume", "1")
        assert result.exit_code == 1 and "declares no rotary" in result.output
    with serving(control=False, rotaries=KNOBS_OPTION) as (client, _):
        result = run(client, "push", "volume")
        assert result.exit_code == 1 and "takes no input" in result.output


def test_a_command_the_hmi_does_not_take_says_why_and_exits_with_an_error():
    with serving() as (client, driver):  # a touch screen
        for args, why in (
            (("click", "1", "2"), "touch, not mouse"),
            (("scroll", "0", "5"), "no wheel"),
            (("press", "KeyA"), "takes no key codes"),
            (("key", "MUTE"), "this HMI has no key MUTE"),  # the exporter's answer: a hard key is sent as it is
        ):
            result = run(client, *args)
            assert result.exit_code == 1 and why in result.output, (args, result.output)
        assert events(driver, "touch", "pointer", "keyboard", "key") == []
    with serving(mode="mouse") as (client, _):  # a mouse and keyboard: no hard keys, but text and taps are fine
        result = run(client, "key", "HOME")
        assert result.exit_code == 1 and "this HMI has no key HOME" in result.output
        assert run(client, "text", "x").exit_code == 0
        assert run(client, "tap", "1", "2").exit_code == 0
    with serving(control=False) as (client, _):
        result = run(client, "tap", "1", "2")
        assert result.exit_code == 1 and "takes no input" in result.output


def test_a_command_the_exporter_refuses_prints_its_answer():
    """A line that gets through the client's check but that the device does not take: the exporter's error line."""
    with serving(mode="mouse") as (client, driver):
        driver._the_source().sink_input.refuse = "the device is busy"
        result = run(client, "tap", "10", "20")
        assert result.exit_code == 1 and "the device is busy" in result.output


def test_recording_says_an_mjpeg_stream_is_not_recorded_before_it_starts_anything(tmp_path):
    with serving(codec="mjpeg") as (client, driver):
        result = run(client, "recording", "-o", str(tmp_path / "x.mp4"))
        assert result.exit_code == 1 and "mjpeg stream is not recorded" in result.output
        assert not (tmp_path / "x.mp4").exists() and driver._source.starts == 0


@pytest.mark.parametrize("codec", ["h264", "hevc"])
def test_recording_fetches_an_mp4_of_the_stream(tmp_path, codec):
    with serving(codec=codec) as (client, _):
        out = tmp_path / f"{codec}.mp4"
        result = run(client, "recording", "-o", str(out), "--seconds", "1")
        assert result.exit_code == 0 and out.stat().st_size > 0
        assert b"ftyp" in out.read_bytes()[:16] and "240x400" in result.output


@pytest.mark.parametrize(
    ("codec", "starts"), [("h264", b"\x00\x00\x00\x01"), ("hevc", b"\x00\x00\x00\x01"), ("mjpeg", b"\xff\xd8")]
)
def test_record_writes_the_streams_own_units_to_a_file(tmp_path, codec, starts):
    with serving(codec=codec) as (client, _):
        out = tmp_path / "raw"
        result = run(client, "record", "-o", str(out), "--seconds", "0.3")
        assert result.exit_code == 0, result.output
        assert out.read_bytes().startswith(starts)
        assert result.output.split()[-1].startswith({"h264": "avc1.", "hevc": "hev1.", "mjpeg": "mjpeg"}[codec])
