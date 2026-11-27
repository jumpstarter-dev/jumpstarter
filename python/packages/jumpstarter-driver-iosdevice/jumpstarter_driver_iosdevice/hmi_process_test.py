"""The scrios HMI against a fake scrios process (see ``fake_scrios``), through Jumpstarter's client and router: the
conformance suite every HMI passes, the codecs, the input mapping (touch, keyboard keys, buttons, text), the start-up
checks, the way it ends and restarts, and the failures it must survive (a scrios that never reads its stdin, a final
close). The fake itself is checked against the protocol at the end.
"""

import base64
import logging
import os
import subprocess
import threading
import time

import pytest
from jumpstarter_driver_hmi.client import HmiClient, HmiStreamClosedError
from jumpstarter_driver_hmi.common import CodecLine, InputError
from jumpstarter_driver_hmi.hub.codecs import (
    HEVC_PPS,
    HEVC_SPS,
    HEVC_VPS,
    NAL_IDR,
    NAL_PPS,
    NAL_SPS,
    codec_string_of,
    has_idr,
    is_reference,
    nal_types,
    scan_unit,
)
from jumpstarter_driver_hmi.proc import ManagedProcess
from jumpstarter_driver_hmi.source import InputSink
from jumpstarter_driver_hmi.testing import HmiConformance
from jumpstarter_driver_hmi.testing.conformance import media, wait_for

from . import hmi as hmi_module
from .driver import IosDevice
from .fake_scrios_testing import FakeScrios
from .hid_usages import USAGES
from .hmi import IosDeviceHmi, IosDeviceHmiSource, _Run
from .scrios_framed import Config, Event, ScriosStream, Unit
from jumpstarter.common.utils import serve

UDID = "00008110-001234567890001E"
SOCKET = "/tmp/jumpstarter-test-usbmuxd.sock"


@pytest.fixture
def fake(tmp_path, monkeypatch):
    hmi_module._version.cache_clear()
    monkeypatch.setenv("FAKE_SCRIOS_FPS", "100")
    return FakeScrios.create(tmp_path, monkeypatch)


@pytest.fixture
def make(fake):
    """A factory of HMIs around the fake, closed at the end of the test."""
    made: list[IosDeviceHmi] = []

    def factory(**config) -> IosDeviceHmi:
        config.setdefault("executable", str(fake.path))
        config.setdefault("idle_stop_s", 0)  # the tests that follow the last viewer to the stop do not wait a minute
        config.setdefault("stop_deadline_s", 5.0)
        config.setdefault("stop_grace_s", 1.0)
        driver = IosDeviceHmi(device=IosDevice(udid=UDID, usbmux_socket=SOCKET), **config)
        made.append(driver)
        return driver

    yield factory
    for driver in made:
        driver.close()


def source_of(driver: IosDeviceHmi) -> IosDeviceHmiSource:
    source = driver._the_source()
    assert isinstance(source, IosDeviceHmiSource)
    return source


def sink_of(driver: IosDeviceHmi) -> InputSink:
    sink = source_of(driver).input_sink()
    assert sink is not None
    return sink


def run_of(driver: IosDeviceHmi) -> _Run:
    run = source_of(driver)._run
    assert run is not None
    return run


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def until_end(video, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if video.read_frame(1.0) is None:
                return
        except TimeoutError:
            continue
    raise AssertionError("the stream did not end")


def errors_of(stream, timeout: float = 5.0) -> list[str]:
    return stream.poll_errors(timeout)


# -- the conformance suite ---------------------------------------------------------------------------------------------


class TestIosDeviceHmi(HmiConformance):
    """The scrios HMI passes the suite every implementation passes, against a fake scrios process."""

    @pytest.fixture(autouse=True)
    def _around_the_fake(self, fake, make):
        self.fake, self._make = fake, make

    def make_driver(self):
        return self._make()

    def input_events(self, driver):
        return self.fake.input_events()

    def change_picture(self, driver):
        self.fake.op(op="config", width=1000, height=2000, rotation=90)
        return True


# -- what scrios is run with -------------------------------------------------------------------------------------------


def test_scrios_is_run_with_the_documented_arguments_and_nothing_else(fake, make):
    make().start()
    assert fake.starts()[0]["argv"] == [
        "stream",
        "--format",
        "framed",
        "--udid",
        UDID,
        "--video-codec",
        "h264",
        "--usbmuxd",
        f"UNIX:{SOCKET}",
    ]


def test_hevc_is_asked_of_scrios_by_the_name_its_option_has_for_it(fake, make):
    driver = make(codec="hevc")
    driver.start()
    argv = fake.starts()[0]["argv"]
    assert argv[argv.index("--video-codec") + 1] == "h265" and "hevc" not in argv
    assert driver.hmi_info()["codec"] == "hevc"
    assert driver.status()["device"]["codec"] == "hevc"


def test_a_change_of_codec_restarts_scrios_with_the_other_one(fake, make):
    driver = make()
    driver.start()
    assert driver.status()["device"]["codec"] == "h264"
    driver.start(codec="hevc")
    wait_for(lambda: len(fake.starts()) == 2, what="the restart")
    argv = fake.argv()
    assert argv[argv.index("--video-codec") + 1] == "h265"
    assert driver.status()["device"]["codec"] == "hevc"
    with pytest.raises(ValueError, match="produces h264, hevc, not 'mjpeg'"):
        driver.start(codec="mjpeg")
    assert len(fake.starts()) == 2  # nothing was started for it


def test_a_view_only_hmi_runs_scrios_with_no_control_and_refuses_input(fake, make):
    driver = make(control=False)
    with serve(driver) as hmi:
        with hmi.video() as video:
            media(video, 3)
            with pytest.raises(HmiStreamClosedError), hmi.input():
                pass
        assert "--no-control" in fake.starts()[0]["argv"]
        declared = hmi.hmi_info()["input"]
        assert declared["pointer"]["kinds"] == []
        assert declared["keyboard"] == {"codes": [], "text": False, "layout": None}
        assert declared["keys"] == [] and declared["hard_keys"] == []
        assert hmi.status()["device"]["control"] is False


def test_the_binary_may_be_found_on_path(fake, make, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    driver = make(executable="scrios")
    driver.start()
    assert len(fake.starts()) == 1
    assert driver.status()["device"]["program"] == str(fake.path)


def test_a_missing_binary_is_a_clear_error_and_nothing_stays_running(make, monkeypatch):
    monkeypatch.setenv("PATH", "")
    driver = make(executable="scrios")
    with pytest.raises(RuntimeError, match="scrios was not found"):
        driver.start()
    assert driver.status()["state"] == "error"
    assert "scrios was not found" in driver.status()["error"]


def test_a_program_that_is_not_scrios_is_not_run_further(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_VERSION", "some other tool 1.0")
    with pytest.raises(RuntimeError, match="does not look like scrios"):
        make().start()
    assert fake.starts() == []


def test_the_version_is_in_the_status(make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_VERSION", "scrios 0.9.1")
    device = make().start()["device"]
    assert (device["version"], device["protocol"]) == ("0.9.1", 1)


def test_another_protocol_is_a_clear_error_and_scrios_is_ended(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_PROTOCOL", "2")
    driver = make()
    with pytest.raises(RuntimeError, match=r"framed protocol 2.*this driver speaks 1"):
        driver.start()
    wait_for(lambda: not alive(fake.pid()), what="scrios to be ended")
    assert driver.status()["state"] == "error"
    assert driver.status()["device"]["failures_in_a_row"] == 1


def test_a_scrios_that_says_no_protocol_is_too_old(make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_PROTOCOL", "absent")
    with pytest.raises(RuntimeError, match="no protocol number"):
        make().start()


def test_a_scrios_that_streams_another_codec_than_the_one_asked_for_is_refused(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_CODEC", "hev1.1.6.L150.B0")  # the config says HEVC, H.264 was asked for
    driver = make()
    with pytest.raises(RuntimeError, match=r"streams HEVC \(hev1\.1\.6\.L150\.B0\), though H\.264 was asked for"):
        driver.start()
    wait_for(lambda: not alive(fake.pid()), what="scrios to be ended")
    assert driver.status()["state"] == "error" and driver.status()["device"]["failures_in_a_row"] == 1


def test_a_scrios_that_streams_h264_when_hevc_was_asked_for_is_refused(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_CODEC", "avc1.640033")
    with pytest.raises(RuntimeError, match=r"scrios streams H\.264 \(avc1\.640033\), though HEVC was asked for"):
        make(codec="hevc").start()


@pytest.mark.parametrize("codec", ["vp09.00.10.08", "mjpeg"])
def test_a_scrios_that_streams_a_codec_the_interface_does_not_take_here_is_refused(make, monkeypatch, codec):
    monkeypatch.setenv("FAKE_SCRIOS_CODEC", codec)
    with pytest.raises(RuntimeError, match=f"scrios streams {codec}, which is neither H.264 nor HEVC"):
        make().start()


def test_a_scrios_that_never_says_what_the_picture_is_ends_the_start(fake, make, monkeypatch, caplog):
    caplog.set_level(logging.WARNING)  # whatever level the run was started with
    monkeypatch.setenv("FAKE_SCRIOS_NO_CONFIG", "1")  # it streams pictures, but never says what they are
    driver = make(start_timeout_s=1.5)
    began = time.monotonic()
    with pytest.raises(RuntimeError, match=r"did not say what the picture is within 1\.5 s"):
        driver.start()
    assert time.monotonic() - began < 8
    wait_for(lambda: not alive(fake.pid()), what="scrios to be ended")
    assert "picture before its config" in caplog.text  # and none of them was served
    assert driver.status()["state"] == "error"


def test_a_scrios_that_cannot_start_says_why(make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_FAIL_START", "the device is not trusted")
    driver = make()
    with pytest.raises(RuntimeError, match="the device is not trusted"):
        driver.start()
    assert driver.status()["state"] == "error"
    assert "exited" not in driver.status()["error"] or "the device is not trusted" in driver.status()["error"]


def test_a_scrios_that_exits_before_it_says_anything_is_named_by_its_status(make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "1")
    monkeypatch.setenv("FAKE_SCRIOS_NO_CONFIG", "1")
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_CODE", "2")
    with pytest.raises(RuntimeError, match="scrios exited with status 2"):
        make().start()


def test_a_scrios_that_cannot_be_executed_is_a_start_error(fake, make):
    fake.path.write_text("#!/nonexistent/interpreter\n")
    with pytest.raises(RuntimeError, match="could not be run|does not look like scrios"):
        make().start()


# -- input -------------------------------------------------------------------------------------------------------------


def test_a_tap_and_a_swipe_are_touch_commands_in_the_shown_picture(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.tap(120, 340)
        stream.swipe(100, 200, 300, 600, seconds=0.2, steps=6)
        wait_for(lambda: [t[1] for t in fake.touches()].count("up") == 2, what="the tap and the swipe, up to their end")
        touches = fake.touches()
        assert touches[:2] == [("touch", "down", 120.0, 340.0, 0), ("touch", "up", 120.0, 340.0, 0)]
        swipe = touches[2:]
        assert swipe[0] == ("touch", "down", 100.0, 200.0, 0) and swipe[-1] == ("touch", "up", 300.0, 600.0, 0)
        assert {t[1] for t in swipe[1:-1]} == {"move"}
        assert all(isinstance(t[2], float) and t[2] == int(t[2]) for t in touches)  # whole pixels
        assert errors_of(stream, 0.3) == []


def test_a_touch_outside_the_picture_is_clamped_to_its_edge_before_scrios_gets_it(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        width = video.header["width"]
        stream.touch("down", width + 500, -20)
        stream.touch("up", width + 500, -20)
        wait_for(lambda: len(fake.touches()) >= 2, what="the touches")
        assert fake.touches()[0] == ("touch", "down", float(width - 1), 0.0, 0)


def test_the_picture_is_the_rotated_one_and_touch_is_aimed_at_it(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        fake.op(op="config", width=1000, height=2000, rotation=90)
        wait_for(lambda: hmi.hmi_info()["rotation"] == 90, what="the new picture")
        assert (hmi.hmi_info()["width"], hmi.hmi_info()["height"]) == (2000, 1000)
        stream.touch("down", 1500, 900, size=(2000, 1000))
        stream.touch("up", 1500, 900, size=(2000, 1000))
        stream.touch("down", 10, 10, size=(1000, 2000))  # aimed at the picture before it turned
        assert errors_of(stream)
        wait_for(lambda: len(fake.touches()) >= 2, what="the touches")
        assert [t[:4] for t in fake.touches()] == [("touch", "down", 1500.0, 900.0), ("touch", "up", 1500.0, 900.0)]


@pytest.mark.parametrize(
    ("key", "press", "line"),
    [
        ("HOME", "short", "key home short"),
        ("HOME", "long", "key home long"),
        ("LOCK", "short", "key lock short"),
        ("POWER", "long", "key power long"),
        ("VOLUME_UP", "short", "key volume-up short"),
        ("volume-down", "short", "key volume-down short"),
        ("MUTE", "short", "key mute short"),
        ("ASSIST", "long", "key siri long"),
        ("SIRI", "short", "key siri short"),
        ("APP_SWITCH", "short", "key app-switcher"),
        ("APP_SWITCHER", "long", "key app-switcher"),
        ("recents", "short", "key app-switcher"),
    ],
)
def test_a_key_is_a_button_press_with_the_names_scrios_documents(fake, make, key, press, line):
    # LOCK and POWER are accepted only when the exporter lists them
    everything = ["HOME", "APP_SWITCH", "LOCK", "POWER", "VOLUME_UP", "VOLUME_DOWN", "MUTE", "ASSIST"]
    with serve(make(hard_keys=everything)) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.key(key, press)
        wait_for(lambda: line in fake.commands(), what=line)
        assert errors_of(stream, 0.3) == []
        assert not [e for e in fake.events("command") if e["line"].startswith("key") and e["line"] != line]


@pytest.mark.parametrize("press", ["down", "up"])
def test_a_button_cannot_be_held_down_scrios_holds_it_itself(fake, make, press):
    """The exporter refuses a press the key does not declare (``short`` and ``long`` here) before the source sees it."""
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.key("HOME", press)
        errors = errors_of(stream)
        assert errors == [f"the key HOME takes short, long, not {press}"]
        assert not [c for c in fake.commands() if c.startswith("key")]


@pytest.mark.parametrize("press", ["down", "up"])
def test_the_source_itself_refuses_a_button_press_it_cannot_make(fake, make, press):
    """Whatever the exporter lets through, the sink does not hold a button: scrios does that itself."""
    driver = make()
    driver.start()
    sink = source_of(driver).input_sink()
    assert sink is not None
    with pytest.raises(InputError, match="short or long"):
        sink.key("HOME", press)
    assert not [c for c in fake.commands() if c.startswith("key")]


def test_a_key_scrios_has_no_button_for_is_refused(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.key("BACK")
        assert "no key BACK" in errors_of(stream)[0]
        assert not [c for c in fake.commands() if c.startswith("key")]


def test_text_is_typed_as_base64_of_utf8_in_commands_of_at_most_4096_characters(fake, make):
    text = "héllo wörld ✓ " + "ab" * 4990  # 10 000 characters, some of them beyond ASCII
    assert len(text) > 8192
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.text("hello")
        stream.text(text)
        lines = wait_for(lambda: [c for c in fake.commands() if c.startswith("kb type")] or None, what="the first")
        wait_for(lambda: len([c for c in fake.commands() if c.startswith("kb type")]) >= 4, what="all of the text")
        typed = [c.split()[2] for c in fake.commands() if c.startswith("kb type")]
        assert lines[0] == "kb type aGVsbG8="
        chunks = [base64.b64decode(p).decode("utf-8") for p in typed[1:]]
        assert [len(c) for c in chunks] == [4096, 4096, len(text) - 8192]
        assert "".join(chunks) == text
        assert errors_of(stream, 0.3) == []


def test_text_beyond_what_one_line_may_carry_is_refused_whole(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.text("x" * 16385)
        errors = errors_of(stream)
        assert errors and "longer than" in errors[0]
        assert not [c for c in fake.commands() if c.startswith("kb")]


def press(stream, code: str, kind: str) -> None:
    """One keyboard event, as a client sends it."""
    stream.send({"keyboard": {"code": code, "type": kind}})


def kb_lines(fake: FakeScrios) -> list[str]:
    """The ``kb down`` and ``kb up`` commands the fake was given, in order."""
    return [c for c in fake.commands() if c.startswith(("kb down", "kb up"))]


def commands_of_run(fake: FakeScrios, run: int) -> list[str]:
    """The commands one run of the fake was given (a run starts at its ``start`` record and ends at the next)."""
    lines: list[str] = []
    current = -1
    for entry in fake.events():
        if entry["event"] == "start":
            current += 1
        elif entry["event"] == "command" and current == run:
            lines.append(entry["line"])
    return lines


def errors_until(stream, count: int, timeout: float = 5.0) -> list[str]:
    """The error lines of the input stream, until there are ``count`` of them (or the time is up)."""
    errors: list[str] = []
    deadline = time.monotonic() + timeout
    while len(errors) < count and time.monotonic() < deadline:
        errors += errors_of(stream, 0.2)
    return errors


def after_the_input(fake: FakeScrios, sink: InputSink) -> None:
    """Wait until what was sent before this has reached the fake (a button goes through the same queue, in order)."""
    sink.key("MUTE", "short")
    wait_for(lambda: "key mute short" in fake.commands(), what="the marker")


def test_a_keyboard_key_is_a_kb_command_with_its_hid_usage_in_hex(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        for code, kind in (
            ("ShiftLeft", "down"),
            ("KeyA", "down"),
            ("KeyA", "up"),
            ("ShiftLeft", "up"),
            ("Enter", "down"),
            ("Enter", "up"),
            ("ArrowUp", "down"),
            ("ArrowUp", "up"),
        ):
            press(stream, code, kind)
        wait_for(lambda: len(kb_lines(fake)) == 8, what="the keys")
        assert kb_lines(fake) == [
            "kb down e1",
            "kb down 04",
            "kb up 04",
            "kb up e1",
            "kb down 28",
            "kb up 28",
            "kb down 52",
            "kb up 52",
        ]
        assert fake.keyboard()[:2] == [("kb", "down", 0xE1), ("kb", "down", 0x04)]
        assert errors_of(stream, 0.3) == []
        assert hmi.status()["device"]["input"]["keyboard_sent"] == 8


def test_every_key_the_hmi_declares_goes_through_with_its_own_usage(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        declared = hmi.hmi_info()["input"]["keyboard"]["codes"]
        assert declared == list(USAGES)
        for code in declared:
            press(stream, code, "down")
            press(stream, code, "up")
        wait_for(lambda: len(kb_lines(fake)) == 2 * len(declared), what="all the keys")
        assert kb_lines(fake) == [f"kb {kind} {usage:02x}" for usage in USAGES.values() for kind in ("down", "up")]
        assert errors_of(stream, 0.3) == []


@pytest.mark.parametrize("code", ["PrintScreen", "ContextMenu", "F13", "Fn", "KeyAA", "NoSuchKey"])
def test_a_key_the_hmi_does_not_declare_is_refused_and_nothing_is_sent(fake, make, code):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        press(stream, code, "down")
        errors = errors_of(stream)
        assert errors and f"does not take the key {code}" in errors[0]
        press(stream, "KeyA", "down")
        wait_for(lambda: kb_lines(fake) == ["kb down 04"], what="the next, declared key")


def test_a_repeat_is_not_sent_again_and_the_end_of_a_key_that_is_not_down_is_nothing(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        press(stream, "KeyB", "up")  # not down
        for _ in range(3):
            press(stream, "KeyA", "down")  # a held key repeats its down
        press(stream, "KeyA", "up")
        press(stream, "KeyA", "up")
        stream.key("MUTE")
        wait_for(lambda: "key mute short" in fake.commands(), what="the marker")
        assert kb_lines(fake) == ["kb down 04", "kb up 04"]
        assert errors_of(stream, 0.3) == []


def test_keys_left_down_when_the_input_ends_are_let_go_once(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        with hmi.input() as stream:
            press(stream, "ControlLeft", "down")
            press(stream, "KeyC", "down")
            wait_for(lambda: len(kb_lines(fake)) == 2, what="the keys going down")
        wait_for(lambda: len(kb_lines(fake)) >= 4, what="the keys to be let go")
        with hmi.input() as again:  # whatever else would have been sent has come by now
            again.key("MUTE")
            wait_for(lambda: "key mute short" in fake.commands(), what="the marker")
        assert sorted(kb_lines(fake)[2:]) == ["kb up 06", "kb up e0"]  # each key once: release() had nothing left
        assert len(kb_lines(fake)) == 4


def test_touches_and_keys_reach_scrios_in_the_order_they_were_sent(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.touch("down", 5, 6)
        press(stream, "KeyA", "down")
        stream.touch("up", 5, 6)
        press(stream, "KeyA", "up")
        wait_for(lambda: len(fake.input_events()) == 4, what="the input")
        assert fake.input_events() == [
            ("touch", "down", 5.0, 6.0, 0),
            ("keyboard", "KeyA", True),
            ("touch", "up", 5.0, 6.0, 0),
            ("keyboard", "KeyA", False),
        ]


def test_the_keyboard_is_refused_with_an_error_line_until_it_is_ready(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID_KEYBOARD", "false")
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        press(stream, "KeyA", "down")
        assert "keyboard is not ready" in errors_of(stream)[0]
        press(stream, "KeyA", "up")  # the exporter thinks it is down; the end of it is nothing here, and no error
        assert errors_of(stream, 0.3) == []
        stream.key("HOME")  # touch and buttons keep working
        wait_for(lambda: "key home short" in fake.commands(), what="the button")
        fake.op(op="hid", ready=True, keyboard=True)
        wait_for(lambda: hmi.status()["device"]["hid"]["keyboard"] is True, what="the keyboard")
        press(stream, "KeyA", "down")
        wait_for(lambda: kb_lines(fake) == ["kb down 04"], what="the key")
        assert hmi.status()["device"]["input"]["keyboard_refused"] == 1


def test_scrios_reason_is_in_the_error_line_when_its_services_are_not_ready(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID", "failed")
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        press(stream, "KeyA", "down")
        errors = errors_of(stream)
        assert errors and "the device refused the HID service" in errors[0]
        assert not kb_lines(fake)


def test_scrios_reason_for_a_keyboard_that_is_not_ready_is_kept_when_it_gives_one(fake, make):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        fake.op(op="hid", ready=True, keyboard=False, message="the virtual keyboard could not be attached")
        wait_for(lambda: hmi.status()["device"]["hid"]["keyboard"] is False, what="the news")
        press(stream, "KeyA", "down")
        stream.text("x")
        errors = errors_until(stream, 2)
        assert len(errors) == 2 and all("the virtual keyboard could not be attached" in e for e in errors)


def test_release_lets_go_of_the_keys_the_sink_still_holds_and_only_those(fake, make):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    for code, down in (("ShiftLeft", True), ("KeyA", True), ("KeyB", True), ("KeyB", False)):
        sink.keyboard(code, down)
    sink.release()
    sink.release()  # nothing is left
    after_the_input(fake, sink)
    assert kb_lines(fake) == ["kb down e1", "kb down 04", "kb down 05", "kb up 05", "kb up 04", "kb up e1"]
    assert source_of(driver).describe()["input"]["keyboard_sent"] == 6


def test_what_a_new_run_of_scrios_does_not_have_is_not_let_go_of_in_it(fake, make):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    sink.keyboard("KeyA", True)
    driver.stop()  # scrios releases every key when it stops
    driver.start()
    sink.release()
    sink.keyboard("KeyA", False)  # not down in this run
    after_the_input(fake, sink)
    assert [c for c in commands_of_run(fake, 0) if c.startswith("kb")] == ["kb down 04"]
    assert fake.events("keys_released")[0]["keys"] == ["04"]  # scrios let go of it when it stopped
    assert [c for c in commands_of_run(fake, 1) if c.startswith("kb")] == []  # nothing was sent to the second run


def test_release_never_raises_and_keeps_a_key_that_could_not_be_let_go(fake, make, monkeypatch):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    sink.keyboard("KeyA", True)
    writer = run_of(driver).writer
    real = writer.send

    def busy(data: bytes, **options):
        if data.startswith(b"kb up"):
            raise InputError("the device is busy: its input queue is full")
        return real(data, **options)

    monkeypatch.setattr(writer, "send", busy)
    sink.release()  # nothing is raised
    assert source_of(driver).describe()["input"]["keyboard_refused"] == 1
    monkeypatch.setattr(writer, "send", real)
    sink.release()  # the next one lets go of it
    wait_for(lambda: kb_lines(fake) == ["kb down 04", "kb up 04"], what="the key to be let go")


def test_a_key_that_is_down_is_let_go_even_when_the_keyboard_has_stopped_working(fake, make):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    sink.keyboard("KeyA", True)
    fake.op(op="hid", ready=True, keyboard=False)
    wait_for(lambda: run_of(driver).hid_keyboard is False, what="the news")
    sink.keyboard("KeyA", False)
    wait_for(lambda: kb_lines(fake) == ["kb down 04", "kb up 04"], what="the key to be let go")
    with pytest.raises(InputError, match="keyboard is not ready"):
        sink.keyboard("KeyB", True)  # but no key goes down


def test_the_sink_refuses_a_key_that_is_not_in_the_table_and_while_nothing_is_running(fake, make):
    driver = make()
    sink = sink_of(driver)
    with pytest.raises(InputError, match="does not take the key PrintScreen"):
        sink.keyboard("PrintScreen", True)
    with pytest.raises(InputError, match="not running"):
        sink.keyboard("KeyA", True)
    driver.start()
    sink.keyboard("KeyA", True)
    driver.stop()
    with pytest.raises(InputError, match="not running"):
        sink.keyboard("KeyA", False)
    assert source_of(driver).describe()["input"]["keyboard_refused"] == 3


def test_the_keyboard_is_not_there_without_control(fake, make):
    driver = make(control=False)
    driver.start()
    assert source_of(driver).input_sink() is None
    assert driver.hmi_info()["input"]["keyboard"]["codes"] == []


def test_text_is_refused_with_an_error_line_until_the_keyboard_is_ready(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID_KEYBOARD", "false")
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.text("hello")
        assert "keyboard is not ready" in errors_of(stream)[0]
        stream.key("HOME")  # touch and buttons keep working
        wait_for(lambda: "key home short" in fake.commands(), what="the button")
        fake.op(op="hid", ready=True, keyboard=True)  # the keyboard comes up later
        wait_for(lambda: hmi.status()["device"]["hid"]["keyboard"] is True, what="the keyboard")
        stream.text("hello")
        wait_for(lambda: "kb type aGVsbG8=" in fake.commands(), what="the text")


def test_touch_and_keys_are_refused_with_scrios_reason_when_its_services_are_not_ready(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID", "failed")
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.touch("down", 5, 5)
        stream.key("HOME")
        stream.text("x")
        errors = errors_of(stream)
        deadline = time.monotonic() + 5
        while len(errors) < 3 and time.monotonic() < deadline:
            errors += errors_of(stream, 0.2)
        assert len(errors) == 3 and all("the device refused the HID service" in e for e in errors)
        assert not fake.touches() and not [c for c in fake.commands() if c.startswith(("key", "kb"))]
        assert hmi.status()["device"]["hid"]["ready"] is False


def test_input_goes_through_when_scrios_has_not_said_anything_about_its_services(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID", "never")
    monkeypatch.setattr(hmi_module, "HID_WAIT_S", 0.3)
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.tap(1, 2)
        wait_for(lambda: len(fake.touches()) == 2, what="the tap")
        assert hmi.status()["device"]["hid"]["ready"] is None


def test_what_scrios_says_about_a_command_that_failed_is_logged_and_counted(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="hid_error", message="the digitizer said no")
        wait_for(lambda: hmi.status()["device"]["hid_errors"] == 1, what="the news")
        assert hmi.status()["device"]["last_hid_error"] == "the digitizer said no"


def test_a_finger_that_is_not_down_is_not_lifted_or_moved_and_a_second_down_is_a_move(fake, make):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    size = (1206, 2622)
    sink.touch("move", 1, 1, size)  # nothing is down
    sink.touch("up", 1, 1, size)
    sink.touch("down", 2, 2, size)
    sink.touch("down", 3, 3, size)  # the same finger, moving
    sink.touch("up", 3, 3, size)
    sink.touch("up", 3, 3, size)  # lifted already
    sink.key("HOME", "short")  # what follows the touches, to know that nothing else was sent
    wait_for(lambda: "key home short" in fake.commands(), what="the last command")
    assert [c for c in fake.commands() if c.startswith("touch")] == [
        "touch down 2 2 0",
        "touch move 3 3 0",
        "touch up 3 3 0",
    ]


def test_a_new_run_does_not_inherit_a_finger_that_was_down(fake, make):
    driver = make()
    driver.start()
    sink = sink_of(driver)
    sink.touch("down", 2, 2, (1206, 2622))
    driver.stop()
    driver.start()
    sink.touch("up", 2, 2, (1206, 2622))  # the new scrios has no finger down
    sink.key("HOME", "short")
    wait_for(lambda: "key home short" in fake.commands(), what="the key")
    assert fake.commands("touch up 2 2 0") == []


def test_a_slow_keyboard_holds_up_nothing_that_is_not_the_keyboard(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_KB_DELAY", "0.4")
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        for word in ("one", "two", "three"):
            stream.text(word)
        began = time.monotonic()
        stream.tap(4, 4)  # this waits in the pipe behind the keyboard in the fake, but nobody on our side waits
        media(video, 30)  # the pictures keep coming
        assert time.monotonic() - began < 3
        wait_for(lambda: len(fake.commands("touch up 4 4 0")) == 1, what="the tap")
        assert len([c for c in fake.commands() if c.startswith("kb type")]) == 3
        assert errors_of(stream, 0.2) == []


# -- how it ends, and starts again -------------------------------------------------------------------------------------


def test_stop_ends_scrios_politely_and_the_stream_can_start_again(fake, make):
    driver = make()
    with serve(driver) as hmi:
        with hmi.video() as video:
            media(video, 3)
            pid = fake.pid()
            began = time.monotonic()
            assert hmi.stop()["state"] == "stopped"
            assert time.monotonic() - began < 5
            assert "stop" in fake.commands()  # asked, not signaled
            assert fake.events("stopped")[0]["reason"] == "stop"
            wait_for(lambda: not alive(pid), what="scrios to be gone")
            until_end(video)
        assert hmi.status()["device"]["failures_in_a_row"] == 0  # a stop is not a failure
        with hmi.video() as again:
            media(again, 3)
        assert len(fake.starts()) == 2


def test_the_end_of_the_lease_stops_scrios(fake, make):
    driver = make()
    driver.start()
    pid = fake.pid()
    began = time.monotonic()
    driver.close()
    assert time.monotonic() - began < 5
    assert not alive(pid)
    assert fake.commands("stop") == ["stop"]


def test_an_idle_hmi_stops_scrios_and_a_viewer_starts_it_again(fake, make):
    driver = make(idle_stop_s=1.0)
    with serve(driver) as hmi:
        with hmi.video() as video:
            media(video, 3)
        wait_for(lambda: fake.events("stopped"), timeout=15, what="the idle stop")
        assert hmi.status()["state"] == "stopped"
        with hmi.video() as again:
            media(again, 3)
        assert len(fake.starts()) == 2


def test_a_scrios_that_dies_ends_the_stream_and_says_why(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "40")
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_CODE", "3")
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_ERROR", "the device went away")
    driver = make()
    with serve(driver) as hmi, hmi.video() as video:
        until_end(video)
        wait_for(lambda: hmi.status()["state"] == "error", what="the error state")
        status = hmi.status()
        assert "the device went away" in status["error"]
        device = status["device"]
        assert device["crashes"] == 1 and device["failures_in_a_row"] == 1
        assert device["last_exit"]["returncode"] == 3 and device["last_exit"]["expected"] is False
        assert not alive(fake.pid())


def test_a_scrios_that_is_killed_by_a_signal_is_named_so(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="signal", signal=9)
        until_end(video)
        wait_for(lambda: hmi.status()["state"] == "error", what="the error state")
        assert "killed by signal 9" in hmi.status()["error"]


def test_a_scrios_that_exits_cleanly_when_nobody_asked_it_to_is_a_failure_too(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "40")
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_CODE", "0")
    with serve(make()) as hmi, hmi.video() as video:
        until_end(video)
        wait_for(lambda: hmi.status()["state"] == "error", what="the error state")
        assert "exited with status 0" in hmi.status()["error"]
        assert hmi.status()["device"]["failures_in_a_row"] == 1


def test_the_next_viewer_starts_it_again_the_second_failure_in_a_row_waits_and_a_new_lease_forgets(
    fake, make, monkeypatch
):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "30")
    monkeypatch.setenv("FAKE_SCRIOS_FAIL_RUNS", "2")
    driver = make(restart_backoff_s=0.6, restart_backoff_max_s=2.0)
    driver.start()
    wait_for(lambda: not source_of(driver).alive, what="the first run to die")
    driver.start()  # the first failure costs nothing
    assert len(fake.starts()) == 2
    wait_for(lambda: not source_of(driver).alive, what="the second run to die")
    with pytest.raises(RuntimeError, match=r"failed 2 times in a row.*not started again for"):
        driver.start()
    assert len(fake.starts()) == 2
    assert driver.status()["state"] == "error"
    assert driver.status()["device"]["retry_in_s"] > 0
    wait_for(lambda: driver.status()["device"]["retry_in_s"] == 0, what="the back-off to pass")
    driver.start()  # the third run does not die (FAKE_SCRIOS_FAIL_RUNS=2)
    assert len(fake.starts()) == 3 and source_of(driver).alive
    assert driver.status()["device"]["last_error"] is not None


def test_a_new_lease_starts_scrios_without_the_back_off_of_the_last_one(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "30")
    monkeypatch.setenv("FAKE_SCRIOS_FAIL_RUNS", "2")
    driver = make(restart_backoff_s=30.0, restart_backoff_max_s=30.0)
    for _ in range(2):
        driver.start()
        wait_for(lambda: not source_of(driver).alive, what="a run to die")
    with pytest.raises(RuntimeError, match="not started again"):
        driver.start()
    driver.reset()
    driver.start()
    assert len(fake.starts()) == 3


def test_a_run_that_lasted_long_enough_is_not_part_of_a_run_of_failures(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_EXIT_AFTER_UNITS", "30")
    driver = make(restart_backoff_s=30.0, restart_backoff_max_s=30.0)
    source = source_of(driver)
    source._backoff.stable_s = 0.2  # the 0.3 s the fake streams before it dies is "stable"
    for _ in range(4):
        driver.start()
        wait_for(lambda: not source.alive, what="a run to die")
    assert len(fake.starts()) == 4 and source._backoff.failures == 1


def test_silence_asks_for_a_key_picture_once_and_a_scrios_that_wakes_up_is_left_alone(fake, make):
    driver = make(stall_s=0.5)
    with serve(driver) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="pause")
        wait_for(lambda: fake.commands("keyframe"), what="the request for a key picture")
        fake.op(op="resume")
        media(video, 10)
        assert len(fake.starts()) == 1
        assert hmi.status()["device"]["last_stall_at"] is not None


def test_a_silence_that_a_key_picture_does_not_end_restarts_scrios_once_and_then_ends_the_stream(
    fake, make, monkeypatch
):
    monkeypatch.setenv("FAKE_SCRIOS_SILENT_AFTER_RUNS", "1")  # the restarted scrios is silent from the start
    driver = make(stall_s=0.5)
    with serve(driver) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="pause")
        wait_for(lambda: len(fake.starts()) == 2, timeout=20, what="the restart")
        until_end(video, 30)
        wait_for(lambda: hmi.status()["state"] == "error", what="the error state")
        assert "stalled" in hmi.status()["error"]
        assert len(fake.starts()) == 2  # not restarted again
        assert hmi.status()["device"]["stall_restarts"] == 1
        assert fake.commands("keyframe")


def test_the_viewers_keep_their_stream_across_a_stall_restart(fake, make):
    driver = make(stall_s=0.5)
    with serve(driver) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="pause")
        wait_for(lambda: len(fake.starts()) == 2, timeout=20, what="the restart")
        picture = media(video, 5)  # the restarted scrios makes pictures again (its control file has no pause)
        assert len(picture) == 5


# -- a scrios that misbehaves ------------------------------------------------------------------------------------------


def test_a_key_picture_without_parameter_sets_is_given_the_ones_seen_before(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="bare_key")
        deadline = time.monotonic() + 15
        seen = 0
        while time.monotonic() < deadline:
            kinds = nal_types(media(video)[0][8:])
            if NAL_IDR in kinds:
                seen += 1
                assert kinds.index(NAL_SPS) < kinds.index(NAL_PPS) < kinds.index(NAL_IDR)
                if hmi.status()["device"]["keys_without_params"]:
                    break
        assert hmi.status()["device"]["keys_without_params"] == 1
        assert seen >= 1


def test_a_frame_that_cannot_be_right_ends_the_run_and_is_counted_as_a_failure(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="oversize")
        until_end(video)
        wait_for(lambda: hmi.status()["state"] == "error", what="the error state")
        assert "out of step" in hmi.status()["error"]
        wait_for(lambda: not alive(fake.pid()), what="scrios to be ended")
        assert hmi.status()["device"]["failures_in_a_row"] == 1


def test_a_control_frame_that_is_not_json_is_counted_and_the_stream_goes_on(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="bad_control")
        wait_for(lambda: hmi.status()["device"]["bad_control_frames"] == 1, what="the count")
        media(video, 10)
        assert len(fake.starts()) == 1


def test_events_this_driver_does_not_know_are_ignored(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        fake.op(op="event", event={"event": "from-the-future", "x": 1})
        fake.op(op="event", event={"event": "hid", "warning": "two characters were skipped"})
        media(video, 10)
        assert hmi.status()["state"] == "running"


def test_lost_pictures_make_the_viewers_wait_for_the_next_key_picture(fake, make):
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 3)
        before = hmi.status()["stream"]["idrs"]
        fake.op(op="stats", dropped_units=3)
        wait_for(lambda: hmi.status()["stream"]["idrs"] > before, what="a key picture after the loss")
        assert len(media(video, 10)) == 10  # and the stream goes on


def test_a_scrios_that_floods_stderr_costs_nothing_and_only_its_last_lines_are_kept(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_STDERR_FLOOD", "1")
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 60)
        log = hmi.status()["device"]["log"]
        assert 0 < len(log) <= 5 and all(len(line) <= 1000 for line in log)


# -- a scrios that stops reading, and the final close ----------------------------------------------------------------


def test_a_scrios_that_never_reads_stdin_does_not_freeze_the_exporter(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_IGNORE_STDIN", "1")
    driver = make(stop_deadline_s=3.0, stop_grace_s=0.3)
    with serve(driver) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        sink = sink_of(driver)
        # more than a pipe holds, and more commands than the queue does: the writer is stuck in its write for good
        for _ in range(80):
            stream.text("x" * 16000)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and run_of(driver).writer.stats()["queued"] < 200:
            time.sleep(0.05)
        assert run_of(driver).writer.stats()["queued"] >= 200
        # the pictures keep coming, the status can be read, and the touch and key handlers return at once
        assert len(media(video, 30)) == 30
        began = time.monotonic()
        assert hmi.status()["state"] == "running"
        refused = 0
        for n in range(200):
            try:
                sink.touch("down" if n == 0 else "move", n % 500, n % 700, (1206, 2622))
                sink.key("HOME", "short")
                sink.keyboard("KeyA", n % 2 == 0)
            except Exception:  # noqa: BLE001 - the queue is full: refused at once is all that is asked
                refused += 1
        for _ in range(1000):
            source_of(driver).request_keyframe()  # never blocks either, whatever the state of the queue
        assert time.monotonic() - began < 2.0
        assert refused > 0
        assert len(media(video, 30)) == 30
    began = time.monotonic()
    driver.close()
    assert time.monotonic() - began < 3.0 + 2.0
    wait_for(lambda: not alive(fake.pid()), what="scrios to be ended")


def test_final_close_escalates_to_sigkill(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_IGNORE_STOP", "1")
    monkeypatch.setenv("FAKE_SCRIOS_IGNORE_TERM", "1")
    driver = make(stop_deadline_s=3.0, stop_grace_s=0.5)
    driver.start()
    pid = fake.pid()
    began = time.monotonic()
    driver.close()
    took = time.monotonic() - began
    assert took < 3.0 + 1.5  # within the deadline, whatever scrios did
    assert took > 0.5  # it was asked politely first, and then terminated
    assert not alive(pid)
    assert fake.commands("stop") == ["stop"]  # it was asked
    assert source_of(driver).describe()["last_exit"]["returncode"] == -9


def test_a_scrios_that_ignores_stop_but_not_sigterm_is_terminated_not_killed(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_IGNORE_STOP", "1")
    driver = make(stop_deadline_s=4.0, stop_grace_s=0.5)
    driver.start()
    driver.close()
    assert source_of(driver).describe()["last_exit"]["returncode"] == -15


def test_stopping_a_stream_that_is_not_running_is_nothing(make):
    driver = make()
    assert driver.stop()["state"] == "stopped"
    source_of(driver).stop(1.0)
    assert not source_of(driver).alive and source_of(driver).error is None


def test_starting_twice_is_one_scrios(fake, make):
    driver = make()
    driver.start()
    driver.start()
    assert len(fake.starts()) == 1


def test_the_status_says_which_scrios_runs_and_what_it_said(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_ROTATION", "90")
    monkeypatch.setenv("FAKE_SCRIOS_POINT_SCALE", "3.0")
    monkeypatch.setenv("FAKE_SCRIOS_CURRENT_ORIENTATION", "rot90")
    monkeypatch.setenv("FAKE_SCRIOS_STATS_EVERY", "5")
    with serve(make()) as hmi, hmi.video() as video:
        media(video, 20)
        device = wait_for(lambda: (d := hmi.status()["device"])["stats"] and d, what="a stats event")
        assert device["pid"] == fake.pid() and device["program"] == str(fake.path) and device["starts"] == 1
        assert device["hid"] == {"ready": True, "keyboard": True, "mapping": "auto"}
        assert device["picture"]["current_orientation"] == "rot90" and device["point_scale"] == 3.0
        assert device["stats"]["access_units"] > 0
        assert hmi.status()["picture"]["rotation"] == 90
        assert isinstance(hmi, HmiClient)


# -- a stop that comes while the stream is starting --------------------------------------------------------------------


class RecordingSink:
    """What a source is given to publish into."""

    def __init__(self) -> None:
        self.lines: list[CodecLine] = []
        self.units = 0
        #: every unit as the source gave it: ``(timestamp, access unit, idr, reference)``
        self.pictures: list[tuple[int, bytes, bool | None, bool | None]] = []
        self.ended_with: list[str | None] = []

    def config(self, line: CodecLine) -> None:
        self.lines.append(line)

    def media(self, ts_us: int, access_unit: bytes, *, idr: bool | None = None, reference: bool | None = None) -> None:
        self.units += 1
        self.pictures.append((ts_us, access_unit, idr, reference))

    def discontinuity(self) -> None:
        pass

    def ended(self, error: str | None = None) -> None:
        self.ended_with.append(error)


def start_in_a_thread(source: IosDeviceHmiSource, sink: RecordingSink, timeout_s: float = 20.0):
    """Start the source on a thread of its own; ``outcome`` says how it went once the thread is done."""
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            source.start(sink, None, timeout_s)
            outcome.append(None)
        except BaseException as e:  # noqa: BLE001
            outcome.append(e)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


def test_a_stop_while_scrios_is_still_connecting_leaves_nothing_behind(fake, make, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_START_DELAY", "30")  # it takes its time to say what the picture is
    source = source_of(make())
    thread, outcome = start_in_a_thread(source, RecordingSink())
    wait_for(lambda: fake.starts(), what="scrios to be started")
    pid = fake.pid()
    began = time.monotonic()
    source.stop(4.0)  # safe before start() has returned
    assert time.monotonic() - began < 4.5
    thread.join(10)
    assert not thread.is_alive()
    assert isinstance(outcome[0], RuntimeError) and "stopped while it started" in str(outcome[0])
    assert not alive(pid)
    assert not source.alive
    assert source.describe()["failures_in_a_row"] == 0  # a stop is not a failure


def test_a_stop_that_comes_before_the_program_is_even_run_starts_nothing(fake, make, monkeypatch):
    source = source_of(make())
    release = threading.Event()
    reached = threading.Event()
    real = hmi_module.scrios_version

    def slow_version(program: str) -> str:
        reached.set()
        release.wait(10)
        return real(program)

    monkeypatch.setattr(hmi_module, "scrios_version", slow_version)
    thread, outcome = start_in_a_thread(source, RecordingSink())
    assert reached.wait(10)
    source.stop(2.0)  # there is nothing to stop yet
    release.set()
    thread.join(10)
    assert isinstance(outcome[0], RuntimeError) and "stopped while it started" in str(outcome[0])
    assert fake.starts() == [] and not source.alive
    assert source.describe()["failures_in_a_row"] == 0


def test_a_stop_between_the_run_being_made_and_the_process_being_spawned_starts_nothing(fake, make, monkeypatch):
    source = source_of(make())
    reached, go = threading.Event(), threading.Event()

    class Gated(ManagedProcess):
        def start(self) -> None:
            reached.set()
            go.wait(10)
            super().start()

    monkeypatch.setattr(hmi_module, "ManagedProcess", Gated)
    thread, outcome = start_in_a_thread(source, RecordingSink())
    assert reached.wait(10)
    source.stop(2.0)
    go.set()
    thread.join(10)
    assert isinstance(outcome[0], RuntimeError) and "stopped while it started" in str(outcome[0])
    assert fake.starts() == [] and not source.alive
    assert source.describe()["failures_in_a_row"] == 0
    assert source.describe()["crashes"] == 0


def test_a_stop_while_the_process_is_being_spawned_does_not_leave_it_running(fake, make, monkeypatch):
    source = source_of(make())
    reached, go = threading.Event(), threading.Event()

    class Slow(ManagedProcess):
        def _spawn(self):
            reached.set()
            go.wait(10)
            return super()._spawn()

    monkeypatch.setattr(hmi_module, "ManagedProcess", Slow)
    thread, outcome = start_in_a_thread(source, RecordingSink())
    assert reached.wait(10)
    stopper = threading.Thread(target=source.stop, args=(3.0,), daemon=True)
    stopper.start()
    time.sleep(0.2)  # the stop is waiting for the spawn
    go.set()
    stopper.join(10)
    thread.join(10)
    assert not thread.is_alive() and not stopper.is_alive()
    assert isinstance(outcome[0], RuntimeError)
    if fake.starts():  # it was spawned after all: it must be gone
        wait_for(lambda: not alive(fake.pid()), what="the child to be ended")
    assert not source.alive


def test_a_start_that_fails_in_a_way_nobody_expected_still_leaves_nothing_behind(fake, make, monkeypatch):
    source = source_of(make())
    made: list[ManagedProcess] = []

    class Seen(ManagedProcess):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    def broken(self, run, deadline, timeout_s):
        raise KeyError("something nobody expected")

    monkeypatch.setattr(hmi_module, "ManagedProcess", Seen)
    monkeypatch.setattr(IosDeviceHmiSource, "_wait_ready", broken)
    with pytest.raises(KeyError):
        source.start(RecordingSink(), None, 10.0)
    assert len(made) == 1 and made[0].wait(10) and not made[0].alive
    assert not source.alive


def test_the_input_sink_follows_the_run_that_is_current_and_refuses_while_there_is_none(fake, make):
    driver = make()
    source = source_of(driver)
    sink = source.input_sink()  # asked for again at every event: this one works for every run
    assert sink is source.input_sink() and sink is not None
    with pytest.raises(InputError, match="not running"):
        sink.key("HOME", "short")  # nothing is running yet
    driver.start()
    sink.key("HOME", "short")
    driver.stop()
    with pytest.raises(InputError, match="not running"):
        sink.touch("down", 1, 1, (1206, 2622))
    driver.start()
    sink.key("LOCK", "short")
    wait_for(lambda: "key lock short" in fake.commands(), what="the key on the second run")
    assert len(fake.starts()) == 2


@pytest.mark.parametrize("key", ["LOCK", "POWER", "power"])
def test_lock_and_power_are_refused_on_the_input_stream_unless_listed(fake, make, key):
    with serve(make()) as hmi, hmi.video() as video, hmi.input() as stream:
        media(video, 3)
        stream.key(key, "short")
        errors = errors_of(stream, 5.0)
        assert errors and "no key" in errors[0]
        stream.key("HOME", "short")
        wait_for(lambda: "key home short" in fake.commands(), what="the next, allowed key")
        assert not [c for c in fake.commands() if "lock" in c or "power" in c]


# -- HEVC --------------------------------------------------------------------------------------------------------------


def hevc_source(make, **config) -> tuple[IosDeviceHmiSource, RecordingSink]:
    """The source of a HMI, started for HEVC on its own (the hub is not in the loop), and what it published."""
    source, sink = source_of(make(**config)), RecordingSink()
    source.start(sink, None, 20.0, codec="hevc")
    return source, sink


def keys_of(sink: RecordingSink) -> list[tuple[int, bytes, bool | None, bool | None]]:
    return [picture for picture in sink.pictures if picture[2]]


def test_hevc_pictures_are_published_with_scrios_key_flags_and_a_codec_line_that_says_hevc(fake, make):
    source, sink = hevc_source(make)
    try:
        wait_for(lambda: len(sink.pictures) >= 40, what="the pictures")
        assert source.describe()["codec"] == "hevc"
    finally:
        source.stop(4.0)
    argv = fake.argv()
    assert argv[argv.index("--video-codec") + 1] == "h265"
    line = sink.lines[0]
    assert line.codec.startswith("hev1.") and line.family == "hevc" and line.format == "annexb"
    assert (line.width, line.height) == (240, 400)  # the pattern the fake streams
    first = sink.pictures[0][1]
    assert line.codec == codec_string_of(first, "hevc")  # the string is the one the unit's own SPS gives
    for _ts, unit, idr, reference in sink.pictures:
        assert idr == has_idr(unit, "hevc")  # the flag scrios sent is what the unit is
        assert reference == is_reference(unit, "hevc")  # read as HEVC, not as H.264
    assert [idr for _ts, _unit, idr, _ref in sink.pictures[:3]] == [True, False, False]  # a key picture, then deltas
    assert len(keys_of(sink)) == 1  # scrios makes another only when asked
    scan = scan_unit(first, "hevc")
    assert scan.vps and scan.sps and scan.pps  # the parameter sets of HEVC, VPS included, are in front of it
    assert HEVC_VPS in nal_types(first, "hevc") and HEVC_SPS in nal_types(first, "hevc")
    assert HEVC_PPS in nal_types(first, "hevc")


def test_a_key_picture_is_asked_of_an_hevc_scrios_and_comes_with_its_parameter_sets(fake, make):
    source, sink = hevc_source(make)
    try:
        wait_for(lambda: len(sink.pictures) >= 5, what="the first pictures")
        assert source.request_keyframe()
        wait_for(lambda: len(keys_of(sink)) >= 2, what="a second key picture")
    finally:
        source.stop(4.0)
    assert fake.commands("keyframe") == ["keyframe"]
    second = keys_of(sink)[1][1]
    assert has_idr(second, "hevc") and scan_unit(second, "hevc").vps


def test_an_hevc_key_picture_without_parameter_sets_is_counted_and_published_as_a_key_picture(fake, make):
    source, sink = hevc_source(make)
    try:
        wait_for(lambda: len(sink.pictures) >= 5, what="the first pictures")
        fake.op(op="bare_key")
        wait_for(lambda: len(keys_of(sink)) >= 2, what="the bare key picture")
        bare = keys_of(sink)[1][1]
        assert has_idr(bare, "hevc") and not scan_unit(bare, "hevc").sps  # no parameter sets of its own
        assert source.describe()["keys_without_params"] == 1  # the hub gives it the ones seen before
    finally:
        source.stop(4.0)


def test_an_hevc_stream_through_the_driver_says_hevc_to_everyone(fake, make):
    driver = make(codec="hevc")
    driver.start()
    assert driver.status()["state"] == "running"
    assert driver.hmi_info()["codec"] == "hevc" and driver.hmi_info()["codecs"] == ["h264", "hevc"]
    assert driver.status()["picture"]["codec"].startswith("hev1.")
    assert driver.status()["device"]["codec"] == "hevc"


def test_an_hevc_stream_reaches_a_viewer_from_a_key_picture_with_its_parameter_sets(fake, make):
    with serve(make(codec="hevc")) as hmi, hmi.video() as video:
        assert video.header["codec"].startswith("hev1.") and video.header["format"] == "annexb"
        first = media(video)[0][8:]  # after the timestamp
        assert has_idr(first, "hevc")
        kinds = nal_types(first, "hevc")
        assert kinds.index(HEVC_VPS) < kinds.index(HEVC_SPS) < kinds.index(HEVC_PPS)


# -- the fake against the protocol -------------------------------------------------------------------------------------


def talk(fake: FakeScrios, lines=(), *, args=(), units: int = 0, timeout: float = 30.0):
    """Run the fake the way the driver does, give it ``lines`` on stdin, wait for ``units`` pictures, then stop it.

    Returns ``(exit status, what it wrote as items, what it wrote to stderr)``.
    """
    argv = [str(fake.path), "stream", "--format", "framed", "--udid", UDID, *args]
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stream, items = ScriosStream(), []
    try:
        assert proc.stdin is not None and proc.stdout is not None
        for line in lines:
            proc.stdin.write(f"{line}\n".encode())
        proc.stdin.flush()
        deadline = time.monotonic() + timeout
        while sum(isinstance(i, Unit) for i in items) < units and time.monotonic() < deadline:
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            items += stream.feed(chunk)
        out, err = proc.communicate(b"stop\n", timeout=timeout)
        items += stream.feed(out)
    finally:
        if proc.poll() is None:
            proc.kill()  # only the one this test started
    return proc.returncode, items, err.decode(errors="replace")


def hid_messages(items, key: str = "message") -> list[str]:
    return [e.data[key] for e in items if isinstance(e, Event) and e.name == "hid" and key in e.data]


def test_the_fake_streams_what_scrios_is_asked_to_in_h264_by_default_and_in_hevc_for_h265(fake):
    status, items, _ = talk(fake, units=20)
    assert status == 0
    config = items[0]
    assert isinstance(config, Config) and config.line.codec.startswith("avc1.")
    assert (config.line.width, config.line.height) == (1206, 2622)
    status, items, _ = talk(fake, args=["--video-codec", "h265"], units=20)
    config = items[0]
    assert isinstance(config, Config) and config.line.codec.startswith("hev1.") and config.line.family == "hevc"
    units = [i for i in items if isinstance(i, Unit)]
    assert units[0].key and units[0].params and has_idr(units[0].payload, "hevc")
    assert not any(u.key for u in units[1:])
    assert all(has_idr(u.payload, "hevc") == u.key for u in units)
    assert items[-1] == Event("stopped", {"event": "stopped"})


@pytest.mark.parametrize("codec", ["hevc", "H265", "mjpeg", ""])
def test_the_fake_refuses_the_codec_names_scrios_does_not_have(fake, codec):
    status, items, error = talk(fake, args=["--video-codec", codec])
    assert status == 2 and not items
    assert "possible values: h264, h265" in error


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("kb down zz", "not a hex number"),
        ("kb down 4g", "not a hex number"),
        ("kb down -4", "not a hex number"),
        ("kb down 12345", "not a hex number"),
        ("kb down 00", "out of range"),
        ("kb down e8", "out of range"),
        ("kb up ff", "out of range"),
        ("kb down", "usage: kb"),
        ("kb down 04 05", "usage: kb"),
        ("kb press 04", "usage: kb"),
        ("kb type !!!", "not valid base64"),
        ("kb type /w==", "not valid UTF-8"),
        ("touch down 1 2 1", "only finger 0"),
        ("key nothing", "unknown key"),
    ],
)
def test_the_fake_answers_a_command_that_is_wrong_with_a_hid_event_that_says_why(fake, line, problem):
    _, items, _ = talk(fake, [line], units=3)
    assert [problem in m for m in hid_messages(items)] == [True], hid_messages(items)
    assert [e.data["ok"] for e in items if isinstance(e, Event) and "ok" in e.data] == [False]


def test_the_fake_takes_the_keys_and_text_it_should_without_a_word(fake):
    text = base64.b64encode(b"x" * 4096).decode()
    lines = ["kb down 04", "kb down 0x05", "kb up e1", "kb down E1", "kb type aGVsbG8=", f"kb type {text}"]
    _, items, _ = talk(fake, lines, units=3)
    assert hid_messages(items) == []


def test_the_fake_says_when_the_text_has_characters_the_us_layout_has_no_key_for(fake):
    text = base64.b64encode("hé✓llo\n".encode()).decode()
    _, items, _ = talk(fake, [f"kb type {text}"], units=3)
    assert hid_messages(items, "warning") == ["kb type: 2 characters are not in the US layout table and were skipped"]
    assert not hid_messages(items)  # a warning is not a failure


def test_the_fake_has_the_keyboard_fail_until_it_works_and_the_ready_event_says_so(fake, monkeypatch):
    monkeypatch.setenv("FAKE_SCRIOS_HID_KEYBOARD", "false")
    _, items, _ = talk(fake, ["kb down 04", "kb type aGk=", "touch down 1 2 0", "key home"], units=3)
    ready = [e.data for e in items if isinstance(e, Event) and e.data.get("ready") is True]
    assert ready and ready[0]["keyboard"] is False
    assert hid_messages(items) == ["keyboard: mainKeyboard transport is unavailable"] * 2  # touch and buttons are fine


def test_the_fake_lets_go_of_the_keys_that_are_down_when_it_stops_and_says_which(fake):
    _, _, _ = talk(fake, ["kb down 04", "kb down e1", "kb down 04", "kb down 05", "kb up 05", "kb up 09"], units=3)
    assert fake.events("keys_released")[0]["keys"] == ["04", "e1"]
