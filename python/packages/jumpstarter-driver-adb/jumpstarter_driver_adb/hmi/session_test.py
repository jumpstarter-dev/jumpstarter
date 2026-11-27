"""One run of the server: what is pushed and forwarded, what the session publishes, how it ends, and who writes the
control socket (only the writer's thread writes it, so a device that stops reading stalls nothing else)."""

from __future__ import annotations

import hashlib
import re
import socket
import struct
import subprocess
import threading
import time

import pytest
from jumpstarter_driver_hmi.common import CodecLine, InputError
from jumpstarter_driver_hmi.hub.codecs import (
    HEVC_PPS,
    HEVC_SPS,
    HEVC_VPS,
    NAL_IDR,
    NAL_PPS,
    NAL_SPS,
    has_idr,
    has_picture,
    nal_types,
)
from jumpstarter_driver_hmi.testing.conformance import wait_for

from . import protocol
from . import session as session_module
from .adb import AdbError
from .fakes import CODEC, HEVC_CODEC, FakeAdb, FakeScrcpyServer
from .session import ScrcpySession, Settings, _TimestampMap, ensure_device_file, list_encoders

#: What a session says when the server's sockets close under it.
GONE = r"(video stream|control connection) ended"
SERVER = b"PK\x03\x04 the server"
SHA = hashlib.sha256(SERVER).hexdigest()


class RecordingSink:
    """The hub's side of the session: what it was given, in order."""

    def __init__(self) -> None:
        self.lines: list[CodecLine] = []
        self.units: list[tuple[int, bytes, object, object]] = []
        self.ended_with: list[str | None] = []
        self.discontinuities = 0

    def config(self, line):
        self.lines.append(line)

    def media(self, ts_us, access_unit, *, idr=None, reference=None):
        self.units.append((ts_us, access_unit, idr, reference))

    def discontinuity(self):
        self.discontinuities += 1

    def ended(self, error=None):
        self.ended_with.append(error)


@pytest.fixture
def adb():
    fake = FakeAdb()
    yield fake
    fake.close()


def make(adb, *, sink=None, scid=0x1234, **settings) -> tuple[ScrcpySession, RecordingSink]:
    sink = sink or RecordingSink()
    session = ScrcpySession(
        adb=adb,
        serial="FAKE123",
        server_bytes=SERVER,
        server_sha256=SHA,
        settings=Settings(**settings),
        sink=sink,
        scid=scid,
    )
    return session, sink


# -- the settings: what the server's command line says -------------------------------------------------------------


def test_arguments_are_what_the_server_takes():
    args = Settings(
        max_size=960, bit_rate=2_000_000, max_fps=24, display_id=2, video_encoder="c2.android.avc.encoder"
    ).arguments(0x1234)
    assert "scid=00001234" in args and "audio=false" in args and "tunnel_forward=true" in args
    assert "max_size=960" in args and "video_bit_rate=2000000" in args and "max_fps=24" in args
    assert "display_id=2" in args and "video_encoder=c2.android.avc.encoder" in args
    assert "video_codec_options=i-frame-interval:int=2" in args
    assert "control=true" in args and "clipboard_autosync=false" in args and "cleanup=false" in args


def test_a_view_only_stream_has_no_control_channel():
    assert "control=false" in Settings(control=False).arguments(1)


def test_the_codec_is_h264_unless_it_is_asked_otherwise_and_the_server_calls_hevc_h265():
    assert Settings().codec == "h264" and "video_codec=h264" in Settings().arguments(1)
    hevc = Settings(codec="hevc").arguments(1)
    assert "video_codec=h265" in hevc and "video_codec=h264" not in hevc and "video_codec=hevc" not in hevc
    assert sum(arg.startswith("video_codec=") for arg in hevc) == 1  # video_codec_options is another option


@pytest.mark.parametrize("codec", ["mjpeg", "h265", "av1", "", None])
def test_a_codec_that_is_not_one_of_the_two_is_refused(codec):
    with pytest.raises(ValueError, match="codec must be one of h264, hevc"):
        Settings(codec=codec).validate()


def test_temporal_layers_go_into_the_codec_options():
    assert "ts-schema:string=android.generic.2" in " ".join(Settings(temporal_layers=2).arguments(1))
    assert "ts-schema" not in " ".join(Settings(temporal_layers=1).arguments(1))


@pytest.mark.parametrize(
    "bad",
    [
        {"max_size": -1},
        {"max_size": 9000},
        {"bit_rate": 5},
        {"max_fps": 0},
        {"idr_interval_s": 0},
        {"temporal_layers": 9},
        {"display_id": -1},
        {"video_encoder": "x; rm -rf /"},
        {"server_options": (("video_codec", "h265"),)},  # an option the HMI owns
        {"server_options": (("video_codec_options", "i-frame-interval:int=10"),)},
        {"server_options": (("Bad Key", "1"),)},
        {"server_options": (("hmi_off_timeout", "5 && reboot"),)},
        {"server_options": (("hmi_off_timeout", "600\n"),)},  # a trailing newline is not a safe value
        {"video_encoder": "c2.android.avc.encoder\n"},
    ],
)
def test_unsafe_or_owned_settings_are_refused(bad):
    with pytest.raises(ValueError):
        Settings(**bad).validate()


def test_extra_server_options_pass_through_and_those_that_change_the_device_turn_the_cleanup_on():
    plain = Settings(server_options=(("some_option", "1"),))
    assert "some_option=1" in plain.arguments(1) and not plain.needs_cleanup
    changing = Settings(server_options=(("hmi_off_timeout", "600"),))
    assert "hmi_off_timeout=600" in changing.arguments(1) and changing.needs_cleanup
    assert Settings(stay_awake=True).needs_cleanup and Settings(show_touches=True).needs_cleanup
    assert "cleanup=true" in Settings(stay_awake=True).arguments(1)


# -- starting ------------------------------------------------------------------------------------------------------


def test_the_session_publishes_the_codec_line_and_the_pictures(adb):
    session, sink = make(adb, max_fps=24)
    session.start(5)
    try:
        wait_for(lambda: len(sink.units) >= 5, what="pictures")
        (line, *_) = sink.lines
        assert (line.codec, line.width, line.height, line.fps, line.input) == (CODEC, 540, 1200, 24, "stream")
        first = sink.units[0][1]
        kinds = nal_types(first)
        assert kinds[:3] == [NAL_SPS, NAL_PPS, NAL_IDR]  # scrcpy sends SPS/PPS once: they are put in front of the IDR
        stamps = [u[0] for u in sink.units]
        assert stamps == sorted(stamps) and len(set(stamps)) == len(stamps)
        assert all(idr is None and reference is None for _, _, idr, reference in sink.units)  # the hub finds them
        assert session.alive and session.codec == CODEC and session.size == (540, 1200)
        assert session.device_name == "Fake Phone"
    finally:
        session.stop()


def test_a_session_for_hevc_asks_for_it_and_publishes_hev1_units_with_the_parameter_sets_in_front(adb):
    adb.make_server = lambda: FakeScrcpyServer(width=240, height=400)
    session, sink = make(adb, codec="hevc", max_fps=15)
    session.start(5)
    try:
        wait_for(lambda: len(sink.units) >= 20, what="pictures")
        assert "video_codec=h265" in next(c[1] for c in adb.calls if c[0] == "spawn")
        (line, *_) = sink.lines
        assert (line.codec, line.width, line.height, line.fps, line.input) == (HEVC_CODEC, 240, 400, 15, "stream")
        assert line.codec.startswith("hev1.") and line.family == "hevc" and line.format == "annexb"
        first = sink.units[0][1]
        assert has_idr(first, "hevc") and nal_types(first, "hevc")[:4] == [HEVC_VPS, HEVC_SPS, HEVC_PPS, 20]
        assert all(has_picture(unit, "hevc") for _, unit, _, _ in sink.units)
        assert [unit for _, unit, _, _ in sink.units if has_idr(unit, "hevc")]
        assert session.codec == HEVC_CODEC and session.size == (240, 400)
        assert all(idr is None and reference is None for _, _, idr, reference in sink.units)  # the hub finds them
    finally:
        session.stop()


def test_a_session_that_is_given_the_other_codec_than_it_asked_for_fails_and_says_which(adb):
    adb.make_server = lambda: FakeScrcpyServer(codec="h265")  # asked for H.264, the server makes HEVC
    session, _ = make(adb)  # codec h264
    with pytest.raises(AdbError, match=r"codec id 0x68323635 \(hevc\), not the h264 that was asked for"):
        session.start(5)
    assert not session.alive and "forward_remove" in adb.names()


def test_a_view_only_session_has_no_control_channel_and_says_so_in_its_codec_line(adb):
    adb.make_server = lambda: FakeScrcpyServer(expect_control=False)
    session, sink = make(adb, control=False)
    session.start(5)
    try:
        assert session.writer is None and sink.lines[0].input is None
        with pytest.raises(AdbError, match="control connection is not open"):
            session.send(b"\x11")
    finally:
        session.stop()


def test_the_server_is_pushed_forwarded_and_started_in_that_order(adb):
    session, _ = make(adb)
    session.start(5)
    try:
        names = adb.names()
        assert names.index("sha256") < names.index("push") < names.index("move") < names.index("drop_stale")
        assert names.index("drop_stale") < names.index("forward") < names.index("spawn")
        command = next(c[1] for c in adb.calls if c[0] == "spawn")
        assert "com.genymobile.scrcpy.Server 4.1 " in command and "scid=00001234" in command
        assert "cleanup=false" in command  # nothing of ours but the server runs on the device
        assert session.pushed
    finally:
        session.stop()


def test_a_file_the_device_has_is_checked_by_its_hash_and_not_pushed_again(adb):
    for _ in range(2):
        session, _ = make(adb)
        session.start(5)
        session.stop()
    assert [c[0] for c in adb.calls].count("push") == 1
    assert len(adb.device_files) == 1 and next(iter(adb.device_files)).startswith(
        "/data/local/tmp/jmp-scrcpy-server-4.1-"
    )


def test_a_cached_file_with_another_hash_is_replaced(adb):
    session, _ = make(adb)
    session.start(5)
    session.stop()
    (path,) = adb.device_files
    adb.device_files[path] = "0" * 64  # something else is there now
    session, _ = make(adb)
    session.start(5)
    session.stop()
    assert [c[0] for c in adb.calls].count("push") == 2 and adb.device_files[path] == SHA


def test_with_the_cleanup_helper_each_run_has_its_own_file_which_is_removed(adb):
    for _ in range(2):
        session, _ = make(adb, stay_awake=True)  # a setting to restore: the server's helper deletes its own file
        session.start(5)
        session.stop()
    pushed = [c[2] for c in adb.calls if c[0] == "push"]
    assert len(pushed) == 2 and pushed[0] != pushed[1]  # one run's cleanup cannot delete the next one's server
    assert "cleanup=true" in next(c[1] for c in adb.calls if c[0] == "spawn")
    assert [c[0] for c in adb.calls].count("remove") == 2 and not adb.device_files


def test_ensure_device_file_pushes_to_a_name_of_its_own_and_moves_it_into_place(adb):
    assert ensure_device_file(adb, "S1", SERVER, SHA, "/data/local/tmp/a.jar") is True
    push = next(c for c in adb.calls if c[0] == "push")
    assert push[2].startswith("/data/local/tmp/a.jar.") and push[2] != "/data/local/tmp/a.jar"
    assert ("move", push[2], "/data/local/tmp/a.jar") in adb.calls
    assert ensure_device_file(adb, "S1", SERVER, SHA, "/data/local/tmp/a.jar") is False


def test_the_encoders_the_server_lists_are_read(adb):
    encoders = list_encoders(adb, "S1", SERVER, SHA)
    assert [e["name"] for e in encoders if e["codec"] == "h264"] == [
        "c2.example.avc.encoder",
        "OMX.example.video.encoder.avc",
        "c2.android.avc.encoder",
    ]
    assert encoders[0]["hardware"] is True and encoders[2]["hardware"] is False
    assert encoders[1]["alias_of"] == "c2.example.avc.encoder" and encoders[0]["alias_of"] is None
    assert "list_encoders=true" in next(c[1] for c in adb.calls if c[0] == "list_encoders")


def test_a_server_that_never_connects_is_an_error_not_a_hang_and_the_device_is_left_clean(adb):
    class Silent:
        def __init__(self):
            self.listener = socket.socket()
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(1)
            self.port = self.listener.getsockname()[1]

        def close(self):
            self.listener.close()

    adb.make_server = Silent
    session, _ = make(adb)
    began = time.monotonic()
    with pytest.raises(AdbError, match="did not accept a connection|no picture"):
        session.start(1.5)
    assert time.monotonic() - began < 10
    assert "forward_remove" in adb.names() and "kill_server_process" in adb.names()  # a failed start is cleaned up too
    assert not session.alive


def test_a_server_that_exits_before_it_connects_says_what_it_said(adb):
    class Refusing:
        port = 1  # nothing listens

        def close(self):
            pass

    adb.make_server = Refusing
    session, _ = make(adb)
    original = adb.spawn

    def spawn(serial, command):
        process = original(serial, command)
        process.returncode = 1  # it has exited already
        return process

    adb.spawn = spawn
    with pytest.raises(AdbError, match="exited with 1 before it connected"):
        session.start(3)


# -- ending --------------------------------------------------------------------------------------------------------


def test_stop_ends_the_server_removes_the_forward_and_leaves_the_cached_file(adb):
    session, sink = make(adb)
    session.start(5)
    port = adb.server.port
    session.stop()
    assert not session.alive and session.ended.is_set()
    assert adb.process is not None and adb.process.returncode is not None
    assert ("forward_remove", port) in adb.calls and ("kill_server_process", "scid=00001234") in adb.calls
    assert "remove" not in adb.names()  # the server file stays on the device: it is the cache of the next run
    assert sink.ended_with == []  # an ordinary stop is not "the capture ended by itself"


def test_stop_can_be_repeated_and_from_any_thread(adb):
    session, _ = make(adb)
    session.start(5)
    threads = [threading.Thread(target=session.stop) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    session.stop()
    assert [c[0] for c in adb.calls].count("forward_remove") == 1


def test_stop_is_bounded_by_its_deadline_even_when_the_process_will_not_end(adb, monkeypatch):
    class Stubborn:
        """An ``adb shell`` that ignores ``terminate`` and ends only when it is killed."""

        returncode = None
        killed = False

        def __init__(self):
            self._gone = threading.Event()
            self.stdout = self._lines()

        def _lines(self):
            yield "[server] INFO: Device: Fake Phone\n"
            self._gone.wait()

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def kill(self):
            self.killed = True
            self.returncode = -9
            self._gone.set()

        def wait(self, timeout=None):
            if not self.killed:
                time.sleep(min(timeout or 0, 0.3))
                raise subprocess.TimeoutExpired("adb", timeout or 0)
            return self.returncode

    process = Stubborn()
    original = adb.spawn
    adb.spawn = lambda serial, command: (original(serial, command), process)[1]
    monkeypatch.setattr(session_module, "PROCESS_WAIT_S", 0.3)
    session, _ = make(adb)
    session.start(5)
    began = time.monotonic()
    session.stop(deadline_s=5)
    assert process.killed and time.monotonic() - began < 3


def test_a_stop_with_no_time_left_does_not_start_more_work(adb):
    session, _ = make(adb)
    session.start(5)
    before = list(adb.calls)
    session.stop(deadline_s=0)
    assert adb.calls == before  # no command is run past the deadline: the forward is left for the next start to drop
    assert session.ended.is_set() and not session.alive


def test_a_server_that_goes_away_ends_the_session_and_says_why(adb):
    session, sink = make(adb)
    session.start(5)
    adb.server.close()
    wait_for(lambda: session.ended.is_set(), what="the end to be noticed")
    assert not session.alive and re.search(GONE, session.error or "")  # whichever of the two sockets noticed first
    assert sink.ended_with and re.search(GONE, sink.ended_with[0] or "")
    wait_for(lambda: "forward_remove" in adb.names(), what="the cleanup that follows")


def test_a_server_process_that_ends_ends_the_session(adb):
    session, sink = make(adb)
    session.start(5)
    assert adb.process is not None
    adb.process.terminate()
    wait_for(lambda: session.ended.is_set(), what="the end to be noticed")
    assert "process ended" in (session.error or "") and sink.ended_with


def test_an_outside_failure_ends_the_session(adb):
    session, sink = make(adb)
    session.start(5)
    session.fail("the video stream stalled")
    assert session.error == "the video stream stalled" and sink.ended_with == ["the video stream stalled"]
    wait_for(lambda: not session.alive and "kill_server_process" in adb.names(), what="the session to be stopped")


# -- a stop that comes while the start is still making things ------------------------------------------------------


@pytest.mark.parametrize("moment", ["push", "drop_stale_forwards", "forward", "spawn"])
def test_a_stop_that_passes_while_the_start_makes_things_leaves_nothing_behind(adb, moment):
    """The stop (from another thread) finds nothing, or only some of what the start goes on to make: forward, process,
    file. The start gives up at its next step and undoes what exists by then."""
    session, sink = make(adb, stay_awake=True)  # its own file on the device, which is not the cache of a next start
    adb.hooks[moment] = lambda: session.stop(5)  # the stop, done by the time this step is
    with pytest.raises(AdbError, match="stopped before it was up"):
        session.start(5)
    assert not session.alive and session.ended.is_set()
    assert adb.device_files == {}  # the file that was pushed is removed
    if adb.servers:  # the forward that was made is removed
        assert ("forward_remove", adb.server.port) in adb.calls
    assert adb.process is None or adb.process.returncode is not None  # the process that was started is ended
    assert sink.units == [] and sink.ended_with == []


def test_a_stop_from_another_thread_while_the_start_is_blocked_leaves_nothing_behind(adb):
    gate, reached = threading.Event(), threading.Event()

    def blocked():
        reached.set()
        gate.wait(10)

    adb.hooks["forward"] = blocked
    session, _ = make(adb, stay_awake=True)
    failure: list[BaseException] = []

    def start():
        try:
            session.start(20)
        except AdbError as e:
            failure.append(e)

    thread = threading.Thread(target=start)
    thread.start()
    assert reached.wait(10)
    began = time.monotonic()
    session.stop(5)  # before the forward exists
    assert time.monotonic() - began < 5
    gate.set()  # the start goes on: its forward is made now
    thread.join(10)
    assert not thread.is_alive() and failure and "stopped before it was up" in str(failure[0])
    assert ("forward_remove", adb.server.port) in adb.calls
    assert adb.device_files == {} and "spawn" not in adb.names()  # it gave up before it started the server


def test_a_session_that_was_stopped_does_not_start(adb):
    session, _ = make(adb)
    session.stop(1)
    before = list(adb.calls)
    with pytest.raises(AdbError, match="stopped before it was up"):
        session.start(5)  # a session is one run: once stopped it is over
    assert adb.calls == before and "push" not in adb.names() and "spawn" not in adb.names()


# -- the control socket --------------------------------------------------------------------------------------------


def test_control_messages_reach_the_server_in_the_order_they_were_sent(adb):
    session, _ = make(adb)
    session.start(5)
    try:
        session.send(protocol.inject_text("hi"))
        session.send(protocol.inject_keycode(protocol.ACTION_DOWN, 3))
        session.send(protocol.inject_touch(protocol.ACTION_DOWN, 0, 1, 2, 540, 1200, 1.0))
        wait_for(lambda: len(adb.server.control_messages) == 3, what="the messages")
        assert [m[0] for m in adb.server.control_messages] == [1, 0, 2]
    finally:
        session.stop()


def test_the_clipboard_is_set_and_read_back_and_set_waits_for_the_devices_acknowledgement(adb):
    session, _ = make(adb)
    session.start(5)
    try:
        assert session.get_clipboard(timeout=0.3) == ""  # nothing on it: the server says nothing
        assert session.set_clipboard("héllo 世界") is True
        assert adb.server.clipboard == "héllo 世界"
        assert session.get_clipboard() == "héllo 世界"
        assert session.set_clipboard("pasted", paste=True) is True
        assert [struct.unpack_from(">BQ?I", m)[2] for m in adb.server.messages_of(9)] == [False, True]
    finally:
        session.stop()


def test_a_clipboard_the_device_does_not_acknowledge_is_not_done(adb):
    session, _ = make(adb)
    session.start(5)
    try:
        adb.server.ack_clipboard = False
        assert session.set_clipboard("x", timeout=0.3) is False
    finally:
        session.stop()


def test_a_waiting_clipboard_call_ends_when_the_session_does(adb):
    session, _ = make(adb)
    session.start(5)
    adb.server.ack_clipboard = False
    result: list[bool] = []
    thread = threading.Thread(target=lambda: result.append(session.set_clipboard("x", timeout=30)))
    thread.start()
    wait_for(lambda: adb.server.messages_of(9), what="the clipboard message")
    began = time.monotonic()
    session.stop()
    thread.join(5)
    assert result == [False] and time.monotonic() - began < 5


def test_an_app_is_started_by_name_over_the_control_socket(adb):
    session, _ = make(adb)
    session.start(5)
    try:
        session.start_app("com.example.app")
        wait_for(lambda: adb.server.messages_of(16), what="the app start")
        assert adb.server.messages_of(16)[0][2:] == b"com.example.app"
    finally:
        session.stop()


def test_a_write_that_fails_ends_the_session_cleanly(adb):
    session, sink = make(adb)
    session.start(5)
    assert session._control is not None
    session._control.shutdown(socket.SHUT_WR)  # the next write fails; reading still works
    session.send(protocol.simple(protocol.TYPE_COLLAPSE_PANELS))
    wait_for(lambda: session.ended.is_set(), what="the failed write to end the session")
    assert "writing to the scrcpy server failed" in (session.error or "")
    assert sink.ended_with and "writing to the scrcpy server failed" in (sink.ended_with[0] or "")
    wait_for(lambda: "forward_remove" in adb.names(), what="the cleanup")
    with pytest.raises(AdbError):
        session.send(b"\x11")  # a session that is over takes nothing


def test_a_device_that_stops_reading_its_control_socket_stalls_nobody_and_stop_is_bounded(adb):
    """The control socket is written by the writer's thread only. A device that stops reading it makes
    ``send`` refuse (the queue is full) and nothing else wait: not the caller, not the reader of the video, not
    ``stop``."""
    session, sink = make(adb)
    session.start(5)
    adb.server.stop_reading_control()
    message = protocol.inject_text("x" * 300)
    refused = None
    slowest = 0.0
    for _ in range(300_000):
        began = time.monotonic()
        try:
            session.send(message)
        except InputError as e:
            refused = str(e)
            break
        finally:
            slowest = max(slowest, time.monotonic() - began)
    assert refused is not None and "busy" in refused, "the queue never filled: the writer never blocked"
    assert slowest < 1.0  # no call waited for the device
    assert session.writer is not None and session.writer.stats()["queued"] > 0
    # the video goes on being read and published while the writer is stuck in its write
    seen = len(sink.units)
    wait_for(lambda: len(sink.units) >= seen + 10, what="pictures while the control socket is blocked")
    began = time.monotonic()
    session.stop(deadline_s=5)
    assert time.monotonic() - began < 4 and session.ended.is_set()
    wait_for(lambda: session.writer is not None and not session.writer.alive, what="the writer's thread to end")


# -- the timestamps ------------------------------------------------------------------------------------------------


def test_timestamps_start_at_zero_and_never_go_back():
    stamp = _TimestampMap()
    assert [stamp(p) for p in (5_000_000, 5_033_000, 5_033_000, 5_066_000)] == [0, 33_000, 33_001, 66_000]


def test_a_restarted_encoder_clock_goes_on_from_where_the_stream_was():
    stamp = _TimestampMap()
    stamp(1_000_000)
    last = stamp(1_500_000)
    after = [stamp(200), stamp(33_200)]  # the encoder's clock started again from nothing
    assert after[0] > last and after[1] - after[0] == 33_000
