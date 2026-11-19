import threading
import time
from typing import cast

import pytest

from .client import HmiClient, HmiStreamClosedError
from .common import CodecLine, HardKey
from .driver import MockHmi, _Session
from .hub import Hub
from .hub.codecs import NAL_IDR, nal_types
from .testing import FakeSource, HmiConformance
from .testing.conformance import media, wait_for
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

#: An SPS, a PPS and an IDR slice: a key access unit.
KEY_UNIT = (
    b"\x00\x00\x00\x01\x67\x42\xc0\x1f\xac\xd9\x40\x10\x00\x00\x00\x01\x68\xce\x3c\x80\x00\x00\x00\x01\x65\x88\x84aaaa"
)


class TestMockHmi(HmiConformance):
    """The mock screen is a hmi: it passes the suite every implementation passes."""

    def make_driver(self):
        return MockHmi(width=240, height=400, fps=60, gop=30, idle_stop_s=0)

    def input_events(self, driver):
        return source(driver).sink_input.snapshot()

    def change_picture(self, driver):
        source(driver).change_picture(200, 300, rotation=90)
        return True


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


# -- what the screen says about itself ---------------------------------------------------------------------------------


def test_hmi_info_before_the_stream_has_a_picture(hmi):
    info = hmi.hmi_info()
    assert info["width"] is None
    assert info["input"]["touch"] is True
    assert info["input"]["keys"] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN"]


def test_hmi_info_after_the_stream_started_has_the_shown_size(hmi):
    with hmi.video() as video:
        media(video)
        assert (hmi.hmi_info()["width"], hmi.hmi_info()["height"]) == (240, 400)


def test_a_view_only_hmi_has_no_input():
    driver = MockHmi(control=False)
    try:
        with serve(driver) as client:
            info = client.hmi_info()
            assert info["input"] == {"touch": False, "max_fingers": 0, "text": False, "keys": [], "hard_keys": []}
            with pytest.raises(HmiStreamClosedError), client.input():
                pass
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


def test_power_and_lock_are_not_offered_by_default():
    """Pressing them can lock the user out of a device whose only way in is its hmi: they are opt-in."""
    driver = MockHmi()
    try:
        with serve(driver) as client:
            keys = client.hmi_info()["input"]["keys"]
            assert "POWER" not in keys and "LOCK" not in keys
            assert not {"POWER", "LOCK"} & {k["key"] for k in client.hmi_info()["input"]["hard_keys"]}
            with client.video() as video, client.input() as stream:
                media(video)
                stream.key("power")
                stream.key("lock")
                errors: list[str] = []
                wait_for(lambda: errors.extend(stream.poll_errors(0.3)) or len(errors) >= 2, what="two error lines")
            assert not [e for e in source(driver).sink_input.snapshot() if e[0] == "key"]
    finally:
        driver.close()


def test_an_exporter_can_add_a_power_or_lock_key_by_listing_it():
    driver = MockHmi(hard_keys=["HOME", "LOCK"])
    try:
        with serve(driver) as client:
            info = client.hmi_info()["input"]
            assert "LOCK" in info["keys"] and "POWER" not in info["keys"]
            assert [k["key"] for k in info["hard_keys"]] == ["HOME", "LOCK"]
            with client.video() as video, client.input() as stream:
                media(video)
                stream.key("lock")
                stream.key("power")  # only what is listed
                wait_for(lambda: ("key", "LOCK", "short") in source(driver).sink_input.snapshot(), what="the lock key")
                assert len(stream.poll_errors(5.0)) == 1
    finally:
        driver.close()


def test_a_listed_key_the_source_cannot_send_is_still_refused():
    driver = MockHmi(hard_keys=["HOME", "TELEPORT"])
    try:
        assert "TELEPORT" not in driver._caps().keys
    finally:
        driver.close()


def test_the_hard_keys_default_to_the_sources():
    driver = MockHmi()
    try:
        assert [k.key for k in driver._caps().hard_keys] == ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP"]
        assert HardKey("HOME", "Home") in driver._caps().hard_keys
    finally:
        driver.close()


# -- input -------------------------------------------------------------------------------------------------------------


def test_keys_text_and_touch_reach_the_source_in_order(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.key("recents")
        stream.touch("down", 10.4, 20.6, size=(240, 400))
        stream.touch("move", 11, 21)
        stream.touch("up", 11, 21)
        stream.key("home", "long")
        stream.text("héllo")
        wait_for(lambda: len(source(driver).sink_input.snapshot()) >= 6, what="the events")
        assert stream.poll_errors(0.2) == []
    assert (
        source(driver).sink_input.snapshot()
        == [
            ("key", "APP_SWITCH", "short"),
            ("touch", "down", 10, 21, (240, 400)),
            ("touch", "move", 11, 21, (240, 400)),
            ("touch", "up", 11, 21, (240, 400)),
            ("key", "HOME", "long"),
            ("text", "héllo"),
            ("release",),
        ][:6]
        + source(driver).sink_input.snapshot()[6:]
    )


def test_coordinates_are_clamped_to_the_picture(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.touch("down", -5, 9999)
        stream.touch("up", 9999, -5)
        wait_for(lambda: len(source(driver).sink_input.snapshot()) >= 2, what="the events")
    events = source(driver).sink_input.snapshot()
    assert events[0] == ("touch", "down", 0, 399, (240, 400))
    assert events[1] == ("touch", "up", 239, 0, (240, 400))


def test_a_move_without_a_finger_down_is_nothing(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.touch("move", 5, 5)
        stream.key("home")
        wait_for(lambda: source(driver).sink_input.snapshot(), what="the key")
    assert source(driver).sink_input.snapshot()[0] == ("key", "HOME", "short")


def test_a_second_down_is_the_same_finger_moving(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.touch("down", 5, 5)
        stream.touch("down", 6, 6)
        stream.touch("up", 6, 6)
        wait_for(lambda: len(source(driver).sink_input.snapshot()) >= 3, what="the events")
    assert [e[1] for e in source(driver).sink_input.snapshot()[:3]] == ["down", "move", "up"]


def test_a_picture_that_changed_refuses_the_touch_aimed_at_the_old_one(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        source(driver).change_picture(200, 300)
        wait_for(lambda: any(isinstance(f, str) for f in [video.read_frame(5.0)]), what="the new codec line")
        stream.touch("down", 5, 5, size=(240, 400))
        errors = stream.poll_errors(5.0)
        assert errors
        assert "200x300" in errors[0]
        assert source(driver).sink_input.snapshot() == []


def test_a_rotated_picture_is_touched_in_the_shown_orientation(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        source(driver).change_picture(240, 400, rotation=90)
        wait_for(lambda: isinstance(video.read_frame(5.0), str), what="the new codec line")
        stream.touch("down", 390, 230, size=(400, 240))
        wait_for(lambda: source(driver).sink_input.snapshot(), what="the touch")
    assert source(driver).sink_input.snapshot()[0] == ("touch", "down", 390, 230, (400, 240))


def test_an_input_the_source_refuses_is_answered_and_the_stream_goes_on(hmi, driver):
    source(driver).sink_input.refuse = "the device is busy"
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream.key("home")
        assert stream.poll_errors(5.0) == ["the device is busy"]
        source(driver).sink_input.refuse = None
        stream.key("back")
        wait_for(lambda: ("key", "BACK", "short") in source(driver).sink_input.snapshot(), what="the next key")


def test_a_client_that_never_reads_its_errors_does_not_stop_the_input(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        for _ in range(200):
            stream.key("NO_SUCH_KEY")  # each is an error line nobody reads
        stream.key("home")
        wait_for(
            lambda: ("key", "HOME", "short") in source(driver).sink_input.snapshot(), what="the key after the errors"
        )


def test_a_line_that_is_too_long_is_an_error_and_the_next_is_read(hmi, driver):
    with hmi.video() as video, hmi.input() as stream:
        media(video)
        stream._raw.send(b"x" * 200_000 + b"\n")
        stream.key("home")
        wait_for(
            lambda: ("key", "HOME", "short") in source(driver).sink_input.snapshot(), what="the key after the long line"
        )
        assert any("longer" in e for e in stream.poll_errors(1.0))


def test_text_needs_a_source_that_takes_it():
    driver = MockHmi()
    source(driver)._text = False
    try:
        with serve(driver) as client, client.video() as video, client.input() as stream:
            media(video)
            stream.text("hi")
            assert stream.poll_errors(5.0) == ["this screen does not take text"]
    finally:
        driver.close()


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


# -- findings of the review: stops that overtake starts, a loop that must not wait, restarts under an open input -------


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
        assert driver._caps().max_fingers == 1
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
