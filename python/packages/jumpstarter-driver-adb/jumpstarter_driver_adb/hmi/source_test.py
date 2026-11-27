"""The scrcpy source on its own, against a fake adb and a fake server: what it says it is (the subset of the HMI
interface that scrcpy has), how it starts, restarts and stops, which codec it asks for and which encoder it names, and
how it judges a display that has gone quiet."""

from __future__ import annotations

import hashlib
import re
import socket
import threading
import time

import pytest
from jumpstarter_driver_hmi.common import InputError, KeyboardCaps, PointerCaps, QualityCaps, canonical_key
from jumpstarter_driver_hmi.hub.codecs import has_idr, has_pps, has_sps, nal_types
from jumpstarter_driver_hmi.hub.quality import Rung
from jumpstarter_driver_hmi.testing.conformance import wait_for

from . import server_binary
from .fakes import CODEC, HEVC_CODEC, FakeAdb, FakeScrcpyServer
from .input import KEYBOARD_CODES
from .session import Settings
from .source import CODECS, DEFAULT_HARD_KEYS, ScrcpySource

JAR = b"PK\x03\x04 not really a server"
#: What a source says when the server's sockets close under it.
GONE = r"(video stream|control connection) ended"


class RecordingSink:
    """The hub's side: the codec lines and pictures the source published, and how the capture ended."""

    def __init__(self) -> None:
        self.lines: list = []
        self.units: list[bytes] = []
        self.ended_with: list[str | None] = []

    def config(self, line):
        self.lines.append(line)

    def media(self, ts_us, access_unit, *, idr=None, reference=None):
        self.units.append(access_unit)

    def discontinuity(self):
        pass

    def ended(self, error=None):
        self.ended_with.append(error)


class Silent:
    """A server that accepts nothing: the start waits for it."""

    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(1)
        self.port = self.listener.getsockname()[1]

    def close(self):
        self.listener.close()


class Refusing:
    """A server whose port nothing listens on."""

    port = 1

    def close(self):
        pass


@pytest.fixture
def adb():
    fake = FakeAdb()
    yield fake
    fake.close()


@pytest.fixture
def jar(tmp_path):
    path = tmp_path / "scrcpy-server"
    path.write_bytes(JAR)
    return path


@pytest.fixture
def make(adb, jar):
    """``make(**options)`` is a source on the fake adb; ``serial_of`` and ``sleep`` are the test's."""
    made: list[ScrcpySource] = []

    def build(**options):
        options.setdefault("serial_of", lambda: "FAKE123")
        options.setdefault("settings", Settings())
        options.setdefault("server_path", str(jar))
        options.setdefault("server_sha256", hashlib.sha256(JAR).hexdigest())
        options.setdefault("sleep", lambda s: None)
        source = ScrcpySource(adb=adb, scid=0x1234, **options)
        made.append(source)
        return source

    yield build
    for source in made:
        source.stop(3)


def spawn_command(adb: FakeAdb, index: int = -1) -> str:
    return [c[1] for c in adb.calls if c[0] == "spawn"][index]


def start(source: ScrcpySource, rung: Rung | None = None, timeout_s: float = 5) -> RecordingSink:
    sink = RecordingSink()
    source.start(sink, rung, timeout_s)
    return sink


# -- what it says it is --------------------------------------------------------------------------------------------


def test_the_capabilities_are_the_subset_of_the_interface_that_scrcpy_has(make):
    caps = make().capabilities()
    assert caps.codecs == ("h264", "hevc") == CODECS  # H.264 is the default; there is no MJPEG
    assert caps.pointer == PointerCaps.touch(1)
    assert caps.pointer.info() == {
        "kinds": ["touch"],
        "absolute": True,
        "relative": False,
        "buttons": False,
        "wheel": False,
        "max_contacts": 1,
    }
    assert caps.keyboard == KeyboardCaps(codes=tuple(KEYBOARD_CODES), text=True, layout="us")
    assert caps.clipboard is False and caps.rotation is False
    assert caps.quality == QualityCaps() and caps.quality.levels == ("minimal", "low", "medium", "high")
    assert caps.quality.auto is True and caps.keyframe_cost == "restart"
    common = {"HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN", "MUTE", "ASSIST"}
    assert common <= set(caps.keys)
    assert all(canonical_key(key) == key for key in caps.keys)


def test_the_keyboard_declares_exactly_the_keys_that_have_an_android_key_code(make):
    codes = make().capabilities().keyboard.codes
    assert isinstance(codes, tuple) and len(set(codes)) == len(codes)
    assert set(codes) == set(KEYBOARD_CODES)
    assert make().capabilities().keyboard.allows("KeyA") and not make().capabilities().keyboard.allows("F13")


def test_the_default_buttons_are_keys_it_accepts(make):
    caps = make().capabilities()
    assert [k.key for k in caps.hard_keys] == ["BACK", "HOME", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"]
    assert caps.hard_keys == DEFAULT_HARD_KEYS
    assert {k.key for k in caps.hard_keys} <= set(caps.keys)
    assert all(k.presses == ("short", "long", "down", "up") for k in caps.hard_keys)  # scrcpy holds a key as told


def test_a_view_only_source_takes_no_input_but_still_makes_both_codecs(make, adb):
    source = make(settings=Settings(control=False))
    caps = source.capabilities()
    assert caps.codecs == ("h264", "hevc")
    assert (caps.pointer, caps.keyboard, caps.keys, caps.optional_keys, caps.hard_keys) == (
        PointerCaps(),
        KeyboardCaps(),
        (),
        (),
        (),
    )
    assert not caps.has_input and not caps.pointer.present and not caps.keyboard.present
    assert source.input_sink() is None and source.control is False
    adb.make_server = lambda: FakeScrcpyServer(expect_control=False)
    start(source)
    assert "control=false" in spawn_command(adb)


def test_a_key_picture_is_never_asked_for_because_the_only_way_restarts_everyones_capture(make, adb):
    source = make()
    start(source)
    assert source.request_keyframe() is False
    time.sleep(0.2)
    assert adb.server.resets == 0 and not adb.server.messages_of(17)  # no RESET_VIDEO


def test_the_input_sink_refuses_while_the_capture_is_not_running_and_is_the_same_one_after(make):
    source = make()
    sink = source.input_sink()
    assert sink is not None
    for call in (
        lambda: sink.touch("down", 1, 1, (540, 1200)),
        lambda: sink.keyboard("KeyA", True),
        lambda: sink.key("HOME", "short"),
        lambda: sink.text("x"),
    ):
        with pytest.raises(InputError, match="not running"):
            call()
    start(source)
    assert source.input_sink() is sink  # asked for on every event: the answer follows the capture that runs now
    sink.key("HOME", "short")
    source.stop(5)
    assert source.input_sink() is sink
    with pytest.raises(InputError, match="not running"):
        sink.key("HOME", "short")


def test_a_change_of_quality_is_a_restart(make):
    assert make().apply_rung(Rung(960, 2_000_000, 24)) is False


# -- the codec -----------------------------------------------------------------------------------------------------


def test_h264_is_the_default_and_the_server_is_asked_for_it(make, adb):
    sink = start(make())
    assert "video_codec=h264" in spawn_command(adb) and "h265" not in spawn_command(adb)
    wait_for(lambda: len(sink.units) >= 3, what="pictures")
    assert sink.lines[0].codec == CODEC and sink.lines[0].family == "h264"
    assert has_idr(sink.units[0]) and has_sps(sink.units[0]) and has_pps(sink.units[0])


def test_hevc_is_asked_of_the_server_by_its_name_for_it_and_the_codec_line_names_it(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(width=240, height=400)
    source = make()
    sink = RecordingSink()
    source.start(sink, None, 5, codec="hevc")
    assert "video_codec=h265" in spawn_command(adb) and "video_codec=h264" not in spawn_command(adb)
    wait_for(lambda: len(sink.units) >= 20, what="pictures")
    (line, *_) = sink.lines
    assert line.codec == HEVC_CODEC and line.family == "hevc" and line.format == "annexb"
    assert (line.width, line.height, line.input) == (240, 400, "stream")


def test_hevc_units_are_decodable_key_pictures_have_all_three_parameter_sets_in_front(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(width=240, height=400)
    source = make()
    sink = RecordingSink()
    source.start(sink, None, 5, codec="hevc")
    wait_for(lambda: len(sink.units) >= 40, what="pictures")
    keys = [unit for unit in sink.units if has_idr(unit, "hevc")]
    assert keys and has_idr(sink.units[0], "hevc")  # the first picture is one a decoder can start at
    for key in keys:
        assert nal_types(key, "hevc")[:3] == [32, 33, 34]  # VPS, SPS, PPS: scrcpy sends them once, they are put back
    deltas = [unit for unit in sink.units if not has_idr(unit, "hevc")]
    assert deltas and not any(has_sps(unit, "hevc") for unit in deltas)
    assert len(keys) < len(sink.units) / 5  # a key picture every fifteen


def test_a_restart_can_change_the_codec_and_the_new_server_is_asked_for_it(make, adb):
    source = make()
    sink = start(source)
    source.stop(5)
    source.start(sink, None, 5, codec="hevc")
    source.stop(5)
    source.start(sink, None, 5)
    assert [c.split("video_codec=")[1].split()[0] for c in [c[1] for c in adb.calls if c[0] == "spawn"]] == [
        "h264",
        "h265",
        "h264",
    ]
    wait_for(lambda: len(sink.lines) >= 3, what="a codec line for each run")
    assert [line.family for line in sink.lines[:3]] == ["h264", "hevc", "h264"]


def test_the_hardware_encoder_named_is_the_one_of_the_codec_in_use_and_not_an_alias(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(width=240, height=400)
    adb.encoder_listing = (
        "    --video-codec=h264 --video-encoder=c2.example.avc.encoder  (hw) [vendor]\n"
        "    --video-codec=h265 --video-encoder=OMX.example.video.encoder.hevc  (hw) [vendor] "
        "(alias for c2.example.hevc.encoder)\n"
        "    --video-codec=h265 --video-encoder=c2.android.hevc.encoder  (sw)\n"
        "    --video-codec=h265 --video-encoder=c2.example.hevc.encoder  (hw) [vendor]\n"
    )
    source = make()
    source.start(RecordingSink(), None, 5, codec="hevc")
    assert "video_encoder=c2.example.hevc.encoder" in spawn_command(adb) and "avc" not in spawn_command(adb)
    encoder = {"name": "c2.example.hevc.encoder", "hardware": True, "chosen_by": "hardware"}
    assert source.describe()["encoder"] == encoder
    source.stop(5)
    source.start(RecordingSink(), None, 5, codec="h264")
    assert "video_encoder=c2.example.avc.encoder" in spawn_command(adb) and "hevc" not in spawn_command(adb)


def test_a_device_with_no_hardware_encoder_of_the_codec_gets_the_servers_choice_of_that_codec(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(width=240, height=400)
    adb.encoder_listing = (
        "    --video-codec=h264 --video-encoder=c2.example.avc.encoder  (hw) [vendor]\n"
        "    --video-codec=h265 --video-encoder=c2.android.hevc.encoder  (sw)\n"
    )
    source = make()
    source.start(RecordingSink(), None, 5, codec="hevc")
    assert "video_encoder" not in spawn_command(adb) and "video_codec=h265" in spawn_command(adb)
    assert source.describe()["encoder"]["chosen_by"] == "server default"


def test_a_codec_scrcpy_does_not_make_is_refused_before_anything_is_touched(make, adb):
    for codec in ("mjpeg", "av1", "vp9"):
        with pytest.raises(RuntimeError, match="h264 and hevc"):
            make().start(RecordingSink(), None, 5, codec=codec)
    assert adb.calls == []


def test_a_server_that_makes_another_codec_than_asked_is_an_error_and_the_device_is_left_clean(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(codec="h264")  # started for h265, it makes H.264
    source = make()
    with pytest.raises(RuntimeError, match=r"codec id 0x68323634 \(h264\).*not the hevc"):
        source.start(RecordingSink(), None, 5, codec="hevc")
    assert not source.alive and "forward_remove" in adb.names()


def test_a_codec_the_hmi_does_not_take_is_said_as_such(make, adb):
    adb.make_server = lambda: FakeScrcpyServer(codec="av1")
    with pytest.raises(RuntimeError, match=r"codec id 0x617631, not the h264 that was asked for"):
        make().start(RecordingSink(), None, 5)


# -- starting, stopping, restarting --------------------------------------------------------------------------------


def test_start_publishes_the_stream_and_stop_ends_it(make, adb):
    source = make()
    assert not source.alive
    sink = start(source)
    wait_for(lambda: len(sink.units) >= 3, what="pictures")
    assert source.alive and sink.lines[0].codec == CODEC and sink.lines[0].input == "stream"
    source.stop(5)
    assert not source.alive and source.error is None
    assert "forward_remove" in adb.names() and "kill_server_process" in adb.names()
    assert sink.ended_with == []
    source.stop(5)  # idempotent


def test_the_rung_is_the_size_the_bit_rate_and_the_frame_rate_of_the_server(make, adb):
    start(make(), Rung(960, 2_000_000, 24))
    command = spawn_command(adb)
    assert "max_size=960" in command and "video_bit_rate=2000000" in command and "max_fps=24" in command


def test_without_a_rung_the_sources_own_settings_are_used(make, adb):
    start(make(settings=Settings(max_size=1280, bit_rate=3_000_000, max_fps=15)))
    command = spawn_command(adb)
    assert "max_size=1280" in command and "video_bit_rate=3000000" in command and "max_fps=15" in command


def test_a_restart_with_another_rung_starts_a_new_server_and_the_input_goes_on_to_it(make, adb):
    source = make()
    sink = start(source, Rung(0, 4_000_000, 30))
    first_sink = source.input_sink()
    assert first_sink is not None
    first_sink.touch("down", 10, 10, (540, 1200))
    wait_for(lambda: adb.server.touches(), what="the touch at the first server")
    source.stop(5)
    source.start(sink, Rung(640, 1_000_000, 15), 5)
    command = spawn_command(adb)
    assert len(adb.servers) == 2 and "max_size=640" in command and "video_bit_rate=1000000" in command
    assert source.input_sink() is first_sink  # one sink for the life of the source: an open input stream goes on
    first_sink.touch("up", 10, 10, (540, 1200))
    wait_for(lambda: adb.server.touches(), what="the touch at the second server")
    assert adb.servers[0].touches() == [("touch", "down", 10, 10)]
    assert adb.server.touches() == [("touch", "up", 10, 10)]
    wait_for(lambda: len(sink.lines) >= 2, what="the second run's codec line")


def test_a_restart_forgets_the_keyboard_keys_held_and_the_new_capture_never_hears_of_them(make, adb):
    source = make()
    sink = start(source)
    keys = source.input_sink()
    assert keys is not None
    keys.keyboard("ShiftLeft", True)
    keys.keyboard("KeyA", True)
    wait_for(lambda: len(adb.server.key_messages()) == 2, what="the keys at the first server")
    source.stop(5)  # a change of quality or codec: the capture ends, and what was held in it is gone
    source.start(sink, None, 5)
    keys.keyboard("KeyA", True)  # the same sink, the new capture: no shift is carried into it
    keys.keyboard("ShiftLeft", False)  # (a late up of one the new capture never saw is nothing)
    keys.release()
    wait_for(lambda: len(adb.servers[1].key_messages()) == 2, what="the keys at the second server")
    num = 0x200000
    assert adb.servers[0].key_messages() == [(0, 59, 0, 0x1 | 0x40 | num), (0, 29, 0, 0x1 | 0x40 | num)]
    assert adb.servers[1].key_messages() == [(0, 29, 0, num), (1, 29, 0, num)]  # A down, and let go by release()


def test_a_start_while_a_run_is_still_there_ends_that_one_first(make, adb):
    source = make()
    start(source)
    first = adb.process
    start(source)
    assert first is not None and first.returncode is not None and len(adb.servers) == 2
    assert source.alive


def test_a_stop_during_the_start_reaches_the_run_that_is_starting(make, adb):
    adb.make_server = Silent
    source = make()
    failure: list[BaseException] = []

    def start_it():
        try:
            start(source, timeout_s=20)
        except RuntimeError as e:
            failure.append(e)

    thread = threading.Thread(target=start_it)
    thread.start()
    wait_for(lambda: "spawn" in adb.names(), what="the server to be started")
    began = time.monotonic()
    source.stop(5)
    thread.join(10)
    assert not thread.is_alive() and failure and time.monotonic() - began < 8  # not the twenty seconds of the start
    assert not source.alive


@pytest.mark.parametrize("moment", ["push", "drop_stale_forwards", "forward", "spawn"])
def test_a_stop_that_overtakes_the_start_leaves_nothing_behind_and_the_next_start_works(make, adb, moment):
    source = make(settings=Settings(stay_awake=True), encoder_policy="default")  # a file of its own, to be removed
    adb.hooks[moment] = lambda: source.stop(5)  # the stop (from the driver, another thread) is done by this step
    with pytest.raises(RuntimeError, match="stopped"):
        start(source)
    assert not source.alive and source.input_sink() is not None
    assert adb.device_files == {}
    assert adb.process is None or adb.process.returncode is not None
    if adb.servers:
        assert ("forward_remove", adb.server.port) in adb.calls
    adb.hooks.clear()
    sink = start(source)  # the stop was of that start only
    wait_for(lambda: len(sink.units) >= 2, what="pictures")
    assert source.alive


def test_a_stop_before_the_start_is_nothing_and_a_stop_after_the_start_ends_it(make, adb):
    source = make()
    source.stop(1)
    source.stop(1)
    start(source)
    assert source.alive
    source.stop(5)
    assert not source.alive


def test_a_stop_that_comes_while_the_encoders_are_listed_gives_up_before_the_server_is_started(make, adb):
    source = make()
    adb.hooks["push"] = lambda: source.stop(5)  # the encoder list is asked for with the server file pushed
    with pytest.raises(RuntimeError, match="stopped"):
        start(source)
    assert "spawn" not in adb.names() and "forward" not in adb.names()


# -- what can go wrong at the start --------------------------------------------------------------------------------


def test_a_missing_server_file_is_said_with_how_to_get_it(make, tmp_path):
    source = make(server_path=str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="not found.*hmi.fetch"):
        start(source)
    assert not source.alive


def test_an_altered_server_file_never_reaches_the_device(make, adb, tmp_path):
    other = tmp_path / "altered"
    other.write_bytes(b"something else")
    with pytest.raises(RuntimeError, match="not the pinned"):
        start(make(server_path=str(other)))
    assert "push" not in adb.names() and "spawn" not in adb.names()


def test_the_file_is_checked_at_every_start_and_what_is_pushed_is_what_was_checked(make, adb, jar):
    source = make()
    start(source)
    source.stop(5)
    jar.write_bytes(b"PK\x03\x04 something else, written after the check")
    with pytest.raises(RuntimeError, match="not the pinned"):
        start(source)
    assert [c[3] for c in adb.calls if c[0] == "push"] == [b"PK\x03\x04"]  # one push, of the first run


def test_a_device_that_is_not_ready_is_said_and_nothing_is_pushed(make, adb):
    def not_ready():
        raise RuntimeError("no device on USB port usb:1-4.2")

    with pytest.raises(RuntimeError, match="no device on USB port usb:1-4.2"):
        start(make(serial_of=not_ready))
    assert adb.calls == []


def test_a_server_that_never_connects_is_an_error_and_the_device_is_left_clean(make, adb):
    adb.make_server = Silent
    source = make()
    with pytest.raises(RuntimeError, match="did not accept a connection|no picture"):
        start(source, timeout_s=1.5)
    assert "forward_remove" in adb.names() and not source.alive


# -- the encoder ---------------------------------------------------------------------------------------------------


def test_the_hardware_encoder_is_named_not_its_alias_and_not_the_software_one(make, adb):
    source = make()
    start(source)
    assert "video_encoder=c2.example.avc.encoder" in spawn_command(adb)
    assert source.describe()["encoder"] == {"name": "c2.example.avc.encoder", "hardware": True, "chosen_by": "hardware"}


def test_an_operators_encoder_is_used_as_named(make, adb):
    source = make(settings=Settings(video_encoder="c2.android.avc.encoder"))
    start(source)
    assert "video_encoder=c2.android.avc.encoder" in spawn_command(adb)
    assert source.describe()["encoder"]["chosen_by"] == "configured"
    assert "list_encoders" not in adb.names()


def test_the_default_policy_leaves_the_choice_to_the_server_and_asks_nothing(make, adb):
    start(make(encoder_policy="default"))
    assert "video_encoder" not in spawn_command(adb) and "list_encoders" not in adb.names()


def test_a_device_with_no_hardware_encoder_gets_the_servers_choice(make, adb):
    adb.encoder_listing = (
        "[server] INFO: List of video encoders:\n    --video-codec=h264 --video-encoder=c2.android.avc.encoder  (sw)\n"
    )
    source = make()
    start(source)
    assert "video_encoder" not in spawn_command(adb)
    assert source.describe()["encoder"]["chosen_by"] == "server default"


def test_a_hardware_encoder_that_would_not_start_is_replaced_once_by_the_servers_choice(make, adb):
    servers = iter([Refusing(), FakeScrcpyServer()])
    adb.make_server = lambda: next(servers)
    source = make()
    start(source, timeout_s=1.2)
    first, second = [c[1] for c in adb.calls if c[0] == "spawn"]
    assert "video_encoder=c2.example.avc.encoder" in first and "video_encoder" not in second
    assert source.alive and "hardware encoder failed" in source.describe()["encoder"]["chosen_by"]


def test_an_encoder_that_was_asked_for_by_name_is_not_retried_without_it(make, adb):
    adb.make_server = Refusing
    source = make(settings=Settings(video_encoder="c2.android.avc.encoder"))
    with pytest.raises(RuntimeError):
        start(source, timeout_s=1.0)
    assert [c[0] for c in adb.calls].count("spawn") == 1


def test_the_encoders_are_listed_through_the_cached_server_file(make, adb):
    source = make()
    encoders = source.list_encoders()
    assert encoders[0]["name"] == "c2.example.avc.encoder" and encoders[1]["alias_of"] == "c2.example.avc.encoder"
    source.list_encoders()
    assert [c[0] for c in adb.calls].count("push") == 1 and not adb.servers  # no stream was started


def test_listing_the_encoders_of_a_device_that_is_not_ready_is_an_error(make):
    def not_ready():
        raise RuntimeError("device FAKE123 is 'offline', not ready")

    with pytest.raises(RuntimeError, match="offline"):
        make(serial_of=not_ready).list_encoders()


# -- what it knows about the device --------------------------------------------------------------------------------


def test_the_display_size_is_asked_of_the_device(make, adb):
    source = make()
    assert source.native_size() == (1080, 2340)
    assert next(c for c in adb.calls if c[0] == "wm_size")[1] == "wm size"
    adb.wm_size = "Physical size: 1080x2340\nOverride size: 720x1560\n"
    assert source.native_size() == (720, 1560)


def test_another_display_is_asked_for_by_its_id(make, adb):
    make(settings=Settings(display_id=2)).native_size()
    assert next(c for c in adb.calls if c[0] == "wm_size")[1] == "wm size -d 2"


def test_a_display_size_that_cannot_be_read_is_none_not_an_error(make, adb):
    adb.wm_size = "no such command\n"
    assert make().native_size() is None

    def not_ready():
        raise RuntimeError("no device")

    assert make(serial_of=not_ready).native_size() is None


def test_describe_says_the_device_the_encoder_the_server_and_the_settings(make, adb, jar):
    source = make(settings=Settings(display_id=0, temporal_layers=2))
    assert source.describe()["name"] is None and source.describe()["encoder"] is None
    start(source, Rung(960, 2_000_000, 24))
    described = source.describe()
    assert described["name"] == "Fake Phone" and described["model"] == "Fake Model"
    assert (described["android"], described["sdk"], described["serial"]) == ("15", "35", "FAKE123")
    assert described["server"] == {
        "version": "4.1",
        "sha256": hashlib.sha256(JAR).hexdigest(),
        "path": str(jar),
        "cached": True,
        "pushed": False,  # the encoder list had pushed it already
    }
    assert described["settings"]["max_size"] == 960 and described["settings"]["bit_rate"] == 2_000_000
    assert described["settings"]["temporal_layers"] == 2 and described["settings"]["control"] is True
    source.reset_lease()
    assert source.describe().get("model") is None and source.describe()["encoder"] is None


def test_the_server_path_is_what_was_configured_until_a_file_was_found(make):
    assert make(server_path="/somewhere/scrcpy-server").describe()["server"]["path"] == "/somewhere/scrcpy-server"
    pinned = server_binary.PINNED_SHA256
    assert make(server_sha256=pinned).describe()["server"]["sha256"] == pinned


# -- why a capture ended -------------------------------------------------------------------------------------------


def test_a_server_that_goes_away_is_the_sources_error_until_the_next_start(make, adb):
    source = make()
    sink = start(source)
    adb.server.close()
    wait_for(lambda: not source.alive, what="the end to be noticed")
    assert re.search(GONE, source.error or "")  # whichever of the two sockets noticed first
    assert sink.ended_with and re.search(GONE, sink.ended_with[0] or "")
    source.stop(5)  # what the driver does with a capture that ended
    assert re.search(GONE, source.error or "")
    start(source)
    assert source.error is None and source.alive


# -- a display that has gone quiet ----------------------------------------------------------------------------------


@pytest.fixture
def running(make):
    source = make()
    sleeps: list[float] = []
    source._sleep = sleeps.append
    start(source)
    source.sleeps = sleeps
    return source


def test_a_still_hmi_is_not_a_stall_while_the_server_is_there(running, adb):
    adb.server_is_alive = True
    assert running.on_stall(20.0, 1) == "wait"
    assert (
        running.on_stall(40.0, 2) == "wait"
    )  # and no restart however long it is quiet: the encoder has nothing to say
    assert [c[0] for c in adb.calls].count("server_alive") == 2
    assert next(c for c in adb.calls if c[0] == "server_alive")[1] == "scid=00001234"


def test_a_server_that_is_gone_ends_the_stream(running, adb):
    adb.server_is_alive = False
    assert running.on_stall(20.0, 1) == "end"
    assert [c[0] for c in adb.calls].count("server_alive") == 1 and running.sleeps == []


def test_a_device_that_does_not_answer_is_asked_twice_before_it_is_believed_gone(running, adb):
    adb.server_is_alive = None
    assert running.on_stall(20.0, 1) == "end"
    assert [c[0] for c in adb.calls].count("server_alive") == 2 and running.sleeps == [1.0]


def test_a_device_that_answers_the_second_time_is_believed(running, adb):
    answers = iter([None, True])
    adb.server_alive = lambda serial, marker: next(answers)
    assert running.on_stall(20.0, 1) == "wait"


def test_with_no_run_there_is_nothing_to_wait_for(make):
    assert make().on_stall(20.0, 1) == "end"


def test_power_and_lock_are_opt_in_not_defaults(make):
    """Pressing them can lock the user out of a device whose only way in is its screen."""
    caps = make().capabilities()
    assert not {"POWER", "LOCK", "SLEEP"} & set(caps.keys)
    assert {"POWER", "LOCK", "SLEEP"} <= set(caps.optional_keys)
    assert not {"POWER", "LOCK", "SLEEP"} & {k.key for k in caps.hard_keys}
    assert make(settings=Settings(control=False)).capabilities().optional_keys == ()
