"""``AdbDeviceHmi`` through Jumpstarter's own client and router, against a fake device, a fake adb and a fake
scrcpy-server: the interface's conformance suite, then what is the Android HMI's own."""

from __future__ import annotations

import contextlib
import json
import struct
import time

import pytest
from jumpstarter_driver_hmi.client import HmiClient, HmiStreamClosedError
from jumpstarter_driver_hmi.hub.codecs import NAL_IDR, nal_types
from jumpstarter_driver_hmi.hub.quality import auto_bit_rate, scaled
from jumpstarter_driver_hmi.testing import HmiConformance
from jumpstarter_driver_hmi.testing.conformance import media, wait_for

from .client import AdbDeviceHmiClient
from .driver import AdbDeviceHmi, validate_hmi_config
from .fakes import CODEC, HEVC_CODEC, FakeAdb, FakeDevice, FakeScrcpyServer, build_hmi
from .input import KEYBOARD_CODES
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve


def ends(video, timeout: float = 15.0) -> bool:
    """Whether the viewer's stream ends within ``timeout`` (whatever is still queued is read first)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if video.read_frame(1.0) is None:
                return True
        except TimeoutError:
            continue
    return False


def spawns(adb: FakeAdb) -> list[str]:
    return [c[1] for c in adb.calls if c[0] == "spawn"]


@pytest.fixture
def hmi_for(tmp_path):
    """``hmi_for(**config)`` is ``(driver, fake adb)``; every fake server is closed at the end."""
    made: list[FakeAdb] = []
    drivers: list[AdbDeviceHmi] = []

    def build(**config):
        driver, adb = build_hmi(tmp_path, **config)
        made.append(adb)
        drivers.append(driver)
        return driver, adb

    yield build
    for driver in drivers:
        driver.close()
    for adb in made:
        adb.close()


# -- the interface's conformance suite -----------------------------------------------------------------------------


class _Conformance(HmiConformance):
    """What every HMI must do, run against a fake device. Subclasses say which kind of HMI it is."""

    def options(self) -> dict:
        """The HMI's options for this kind of HMI."""
        return {}

    def server(self):
        return FakeScrcpyServer(fps=60)

    @pytest.fixture(autouse=True)
    def _fakes(self, tmp_path):
        self.tmp_path = tmp_path
        self.adbs: list[FakeAdb] = []
        yield
        for adb in self.adbs:
            adb.close()

    def make_driver(self):
        driver, adb = build_hmi(self.tmp_path, adb_server=self.server, **self.options())
        self.adbs.append(adb)
        return driver

    def input_events(self, driver):
        servers = driver._adb.servers
        return servers[-1].touches() if servers else []

    def change_picture(self, driver):
        driver._adb.server.change_picture(480, 1000)
        return True


class TestAdbDeviceHmi(_Conformance):
    """The Android HMI is an HMI: it passes the suite every implementation passes."""


class TestAdbDeviceHmiViewOnly(_Conformance):
    """So is the same HMI with no control channel: the suite skips what a view-only HMI does not take."""

    def options(self):
        return {"control": False}

    def server(self):
        return FakeScrcpyServer(fps=60, expect_control=False)


class TestAdbDeviceHmiCapped(_Conformance):
    """And with the operator's ceilings on size, bit rate and frame rate."""

    def options(self):
        return {"max_size": 960, "bit_rate": 2_000_000, "max_fps": 30, "stall_s": 0}


# -- the HMI of a device ----------------------------------------------------------------------------------------


def test_the_client_is_a_hmi_client_with_the_android_extras(hmi_for):
    driver, _ = hmi_for()
    assert AdbDeviceHmi.client() == "jumpstarter_driver_adb.hmi.client.AdbDeviceHmiClient"
    assert driver.driver_type == "hmi"
    with serve(driver) as client:
        assert isinstance(client, AdbDeviceHmiClient) and isinstance(client, HmiClient)
        assert client.HMI_STREAM_VERSION == 1


def test_video_starts_the_server_and_serves_the_streams_frames(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10) as video:
        header = video.header
        assert header["codec"] == CODEC and (header["width"], header["height"]) == (540, 1200)
        assert header["format"] == "annexb" and header["timestamp"] == "us" and header["v"] == 1
        assert header["input"] == "stream" and header["fps"] == 30
        first = media(video, 12)[0]
        assert NAL_IDR in nal_types(first[8:]) and first[8:12] == b"\x00\x00\x00\x01"
        status = client.status()
        assert status["state"] == "running" and status["picture"]["width"] == 540
    names = adb.names()
    assert names.index("sha256") < names.index("push") < names.index("move") < names.index("forward")
    command = spawns(adb)[0]
    assert "com.genymobile.scrcpy.Server 4.1 " in command and "tunnel_forward=true" in command
    assert "audio=false" in command and "cleanup=false" in command and "control=true" in command


def test_the_device_is_the_parents_and_nothing_is_asked_of_it_until_the_stream_starts(hmi_for):
    driver, adb = hmi_for()
    assert driver._source is None and adb.calls == []
    assert driver.status()["state"] == "stopped" and driver.hmi_info()["input"]["pointer"]["kinds"] == ["touch"]
    assert adb.calls == [] and driver.device.adb_env_calls == 0


def test_the_stream_uses_the_serial_the_parent_gives_now(hmi_for):
    driver, adb = hmi_for()
    driver.device.present_serial = "OTHER99"
    seen: list[str] = []
    original = adb.forward
    adb.forward = lambda serial, name: (seen.append(serial), original(serial, name))[1]
    with serve(driver) as client:
        assert client.start()["device"]["serial"] == "OTHER99"
    assert seen == ["OTHER99"]


def test_a_device_that_is_not_ready_is_an_error_that_says_why_and_the_next_try_works(hmi_for):
    driver, adb = hmi_for()
    driver.device.error = "no device on USB port usb:1-4.2. It may be powered off"
    with serve(driver) as client:
        with pytest.raises(Exception, match="no device on USB port usb:1-4.2"):
            client.start()
        status = client.status()
        assert status["state"] == "error" and "powered off" in status["error"]
        assert "push" not in adb.names()
        driver.device.error = None
        assert client.start()["state"] == "running"


def test_status_says_the_device_the_encoder_and_the_server(hmi_for, tmp_path):
    driver, _ = hmi_for()
    with serve(driver) as client:
        device = client.start()["device"]
    assert device["model"] == "Fake Model" and device["android"] == "15" and device["name"] == "Fake Phone"
    assert device["encoder"] == {"name": "c2.example.avc.encoder", "hardware": True, "chosen_by": "hardware"}
    assert device["server"]["version"] == "4.1" and device["server"]["path"] == str(tmp_path / "scrcpy-server")
    assert device["settings"]["control"] is True and device["settings"]["idr_interval_s"] == 2


# -- the server on the device --------------------------------------------------------------------------------------


def test_stop_cleans_up_the_device_and_ends_viewers(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10) as video:
        media(video, 3)
        assert client.stop()["state"] == "stopped"
        assert adb.process is not None and adb.process.returncode is not None
        assert "forward_remove" in adb.names() and "kill_server_process" in adb.names()
        assert "remove" not in adb.names()  # the server file stays on the device: it is the cache of the next start
        assert len(adb.device_files) == 1 and next(iter(adb.device_files)).startswith("/data/local/tmp/jmp-scrcpy-")
        assert ends(video)


def test_the_end_of_the_lease_stops_the_server_and_removes_the_forward(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.start()
    assert adb.process is not None and adb.process.returncode is not None and "forward_remove" in adb.names()


def test_a_server_that_dies_ends_the_viewers_and_the_next_join_starts_a_new_one(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        with client.video(timeout=10) as video:
            media(video, 3)
            adb.server.close()  # the device went away
            assert ends(video)
        wait_for(lambda: client.status()["state"] in ("error", "stopped"), what="the end to be noticed")
        with client.video(timeout=10) as again:
            assert again.header["width"] == 540
        assert len(adb.servers) == 2


def test_a_server_that_never_connects_is_an_error_not_a_hang_and_cleans_up(hmi_for):
    class Silent:
        def __init__(self):
            import socket

            self.listener = socket.socket()
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(1)
            self.port = self.listener.getsockname()[1]

        def close(self):
            self.listener.close()

    driver, adb = hmi_for(adb_server=Silent, start_timeout_s=1.5)
    with serve(driver) as client:
        with pytest.raises(Exception, match="did not accept a connection|no picture"):
            client.start()
        assert client.status()["state"] == "error"
        assert "forward_remove" in adb.names()


def test_idle_stop(hmi_for):
    driver, adb = hmi_for(idle_stop_s=1.0)
    with serve(driver) as client:
        with client.video(timeout=10) as video:
            media(video, 2)
        wait_for(lambda: client.status()["state"] == "stopped", timeout=10, what="the idle stop")
        assert adb.process is not None and adb.process.returncode is not None


def test_a_viewer_that_joins_as_the_stream_stops_is_attached_to_the_new_one(hmi_for):
    driver, _ = hmi_for()
    with serve(driver) as client:
        client.start()
        driver._stop_session(why="a test")  # the stream ended just before the viewer attached
        with client.video(timeout=10) as video:
            assert video.header["width"] == 540
            assert media(video)


def test_a_second_viewer_starts_at_once_from_the_cached_pictures_and_the_encoder_is_left_alone(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10) as first:
        media(first, 5)
        before = client.status()["stream"]["joins"]
        with client.video(timeout=10) as second:
            picture = media(second)[0]
            assert NAL_IDR in nal_types(picture[8:])
        assert len(adb.servers) == 1 and adb.server.resets == 0  # no RESET_VIDEO for a joiner: it restarts everyone
        after = client.status()["stream"]["joins"]
        assert after["replayed"] == before["replayed"] + 1


# -- the server file -----------------------------------------------------------------------------------------------


def test_a_missing_or_altered_server_file_is_a_clear_error_at_first_use(hmi_for, tmp_path):
    driver, _ = hmi_for(server_path=str(tmp_path / "missing"))
    with serve(driver) as client, pytest.raises(Exception, match="not found"):
        client.start()
    bad = tmp_path / "altered"
    bad.write_bytes(b"something else")
    driver2, adb2 = hmi_for(server_path=str(bad), server_sha256="0" * 64)
    with serve(driver2) as client, pytest.raises(Exception, match="not the pinned"):
        client.start()
    assert "push" not in adb2.names()  # nothing altered ever reaches the device


def test_the_server_is_pushed_once_and_checked_by_its_hash_on_the_device_after_that(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        for _ in range(2):
            client.start()
            client.stop()
    assert [c[0] for c in adb.calls].count("push") == 1  # the second start finds the file and its hash


def test_with_a_setting_to_restore_each_start_has_its_own_file_which_is_removed(hmi_for):
    driver, adb = hmi_for(stay_awake=True, encoder_policy="default")
    with serve(driver) as client:
        for _ in range(2):
            client.start()
            client.stop()
    pushed = [c[2] for c in adb.calls if c[0] == "push"]
    assert len(pushed) == 2 and pushed[0] != pushed[1]
    assert "cleanup=true" in spawns(adb)[0] and "stay_awake=true" in spawns(adb)[0]
    assert [c[0] for c in adb.calls].count("remove") == 2 and not adb.device_files


# -- the options ---------------------------------------------------------------------------------------------------


def test_the_server_gets_the_options_of_the_hmi(hmi_for):
    driver, adb = hmi_for(
        display_id=2,
        idr_interval_s=4,
        temporal_layers=2,
        power_on=False,
        show_touches=True,
        video_encoder="c2.android.avc.encoder",
        server_options={"hmi_off_timeout": "600"},
    )
    with serve(driver) as client:
        client.start()
    command = spawns(adb)[0]
    for part in (
        "display_id=2",
        "i-frame-interval:int=4",
        "ts-schema:string=android.generic.2",
        "power_on=false",
        "show_touches=true",
        "video_encoder=c2.android.avc.encoder",
        "hmi_off_timeout=600",
        "cleanup=true",
        "video_codec=h264",
    ):
        assert part in command
    assert "wm size -d 2" in [c[1] for c in adb.calls if c[0] == "wm_size"]


@pytest.mark.parametrize(
    "options",
    [
        {"server_sha256": "abc"},
        {"server_sha256": 5},
        {"encoder_policy": "software"},
        {"max_size": -5},
        {"bit_rate": 5},  # below what the server takes
        {"max_fps": 500},
        {"video_encoder": "a b"},
        {"video_encoder": 7},
        {"idr_interval_s": 0},
        {"idr_interval_s": "2"},
        {"display_id": True},
        {"temporal_layers": 9},
        {"power_on": "yes"},
        {"stay_awake": 1},
        {"show_touches": None},
        {"server_options": ["a=b"]},
        {"server_options": {"video_codec": "h265"}},  # an option the HMI owns
        {"codec": "mjpeg"},  # one the interface has and scrcpy cannot make
        {"codec": "vp9"},  # and one it does not have at all
        {"codec": 264},
        {"server_options": {"hmi_off_timeout": "5 && reboot"}},
        {"server_path": 5},
        {"control": "no"},  # the interface's own options are checked too
        {"idle_stop_s": -1},
    ],
)
def test_a_wrong_option_is_a_configuration_error_at_startup(options):
    with pytest.raises(ConfigurationError):
        AdbDeviceHmi(device=FakeDevice(), **options)  # ty: ignore[invalid-argument-type]


def test_the_options_of_the_hmi_are_checked_by_name():
    validate_hmi_config(None)
    validate_hmi_config({})
    validate_hmi_config({"control": False, "video_encoder": "x", "max_size": 960, "hard_keys": ["HOME"]})
    validate_hmi_config({"codec": "hevc"})
    for bad in (
        {"nope": 1},
        {"device": 1},
        {"children": {}},
        {"uuid": "x"},
        "hmi",
        ["control"],
        {"control": 1, 2: 3},
    ):
        with pytest.raises(ConfigurationError, match="hmi accepts only"):
            validate_hmi_config(bad)


def test_the_socket_name_is_stable_for_a_device_and_a_display(hmi_for):
    first, _ = hmi_for()
    again, _ = hmi_for()
    assert first._scid == again._scid and 0 <= first._scid < 2**31
    other_display, _ = hmi_for(display_id=1)
    assert other_display._scid != first._scid
    other_port = build_hmi_with(usb_port="usb:1-4.3")
    assert other_port._scid != first._scid


def build_hmi_with(**device_attributes) -> AdbDeviceHmi:
    device = FakeDevice()
    for name, value in device_attributes.items():
        setattr(device, name, value)
    return AdbDeviceHmi(device=device)  # ty: ignore[invalid-argument-type]


def test_hard_keys_can_be_replaced_by_the_exporter(hmi_for):
    driver, _ = hmi_for(hard_keys=["HOME", {"key": "POWER", "label": "Sleep", "presses": ["short"]}])
    with serve(driver) as client:
        info = client.hmi_info()
    assert [(k["key"], k["label"], k["presses"]) for k in info["input"]["hard_keys"]] == [
        ("HOME", "Home", ["short", "long"]),
        ("POWER", "Sleep", ["short"]),
    ]
    assert "POWER" in info["input"]["keys"] and "LOCK" not in info["input"]["keys"]  # only what the exporter listed


def test_hmi_info_says_what_a_phone_takes(hmi_for):
    driver, _ = hmi_for()
    with serve(driver) as client:
        info = client.hmi_info()
    assert info["interface"] == "hmi" and info["codecs"] == ["h264", "hevc"] and info["codec"] == "h264"
    assert info["recording"] is True  # MJPEG is the codec that cannot be recorded, and this has none
    assert info["input"]["pointer"] == {
        "kinds": ["touch"],
        "absolute": True,
        "relative": False,
        "buttons": False,
        "wheel": False,
        "max_contacts": 1,
    }
    assert info["input"]["keyboard"] == {"codes": list(KEYBOARD_CODES), "text": True, "layout": "us"}
    assert info["input"]["clipboard"] is False
    codes = set(info["input"]["keyboard"]["codes"])
    assert {"KeyA", "Digit1", "F12", "ArrowUp", "ShiftLeft", "Numpad5", "Enter"} <= codes
    assert not {"F13", "IntlBackslash", "Lang1", "MediaPlayPause"} & codes
    assert [k["key"] for k in info["input"]["hard_keys"]] == [
        "BACK",
        "HOME",
        "APP_SWITCH",
        "VOLUME_UP",
        "VOLUME_DOWN",
    ]
    assert info["quality"] == {"adjustable": True, "auto": True, "levels": ["minimal", "low", "medium", "high"]}


# -- the codec -----------------------------------------------------------------------------------------------------


def test_h264_is_the_default_codec_and_the_server_is_asked_for_it(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        assert client.hmi_info()["codec"] == "h264"
        client.start()
        assert "video_codec=h264" in spawns(adb)[0] and "h265" not in spawns(adb)[0]
        wait_for(lambda: client.status()["picture"]["codec"], what="the codec line")
        assert client.status()["picture"]["codec"] == CODEC and client.hmi_info()["codec"] == "h264"


def test_the_codec_option_reaches_the_server_and_the_codec_line_names_it(hmi_for):
    driver, adb = hmi_for(codec="hevc", adb_server=lambda: FakeScrcpyServer(width=240, height=400))
    with serve(driver) as client:
        assert client.hmi_info()["codec"] == "hevc" and client.hmi_info()["codecs"] == ["h264", "hevc"]
        client.start()
        assert "video_codec=h265" in spawns(adb)[0] and "video_codec=h264" not in spawns(adb)[0]
        wait_for(lambda: client.status()["picture"]["codec"], what="the codec line")
        picture = client.status()["picture"]
        assert picture["codec"] == HEVC_CODEC and picture["codec"].startswith("hev1.")
        assert (picture["width"], picture["height"]) == (240, 400)
        assert client.hmi_info()["codec"] == "hevc" and client.hmi_info()["recording"] is True
        assert client.status()["device"]["encoder"]["name"] == "c2.example.hevc.encoder"  # the HEVC hardware encoder


def test_a_codec_the_device_does_not_have_a_hardware_encoder_for_falls_to_the_servers_choice(hmi_for):
    driver, adb = hmi_for(codec="hevc", adb_server=lambda: FakeScrcpyServer(width=240, height=400))
    adb.encoder_listing = "    --video-codec=h264 --video-encoder=c2.example.avc.encoder  (hw) [vendor]\n"
    with serve(driver) as client:
        client.start()
    assert "video_codec=h265" in spawns(adb)[0]
    assert "video_encoder" not in spawns(adb)[0]  # and not the name of the H.264 encoder
    assert driver.status()["device"]["encoder"]["chosen_by"] == "server default"


def test_start_chooses_the_codec_of_a_stream_and_restarts_a_running_one_with_it(hmi_for):
    driver, adb = hmi_for(adb_server=lambda: FakeScrcpyServer(width=240, height=400))
    with serve(driver) as client:
        client.start()
        wait_for(lambda: client.status()["picture"]["codec"], what="the first codec line")
        assert client.status()["picture"]["codec"] == CODEC
        client.call("start", None, None, None, None, "hevc")
        assert len(adb.servers) == 2 and "video_codec=h265" in spawns(adb)[1]
        wait_for(lambda: client.status()["picture"]["codec"] == HEVC_CODEC, what="the codec line of the new codec")
        assert client.hmi_info()["codec"] == "hevc"
        client.set_quality("low")  # a change of quality restarts the capture: the codec chosen stays chosen
        wait_for(lambda: len(adb.servers) == 3, what="the restart")
        assert "video_codec=h265" in spawns(adb)[2] and "max_size=960" in spawns(adb)[2]
        client.call("start", None, None, None, None, "hevc")  # already there: nothing is restarted
        assert len(adb.servers) == 3
        client.call("start", None, None, None, None, "h264")
        assert len(adb.servers) == 4 and "video_codec=h264" in spawns(adb)[3]


def test_a_codec_scrcpy_cannot_make_is_refused_at_start_and_the_stream_is_not_touched(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        for codec in ("mjpeg", "av1"):
            with pytest.raises(ValueError, match="produces h264, hevc"):
                client.call("start", None, None, None, None, codec)
        assert adb.calls == [] and client.status()["state"] == "stopped"


def test_a_server_that_does_not_make_the_codec_asked_for_is_a_failed_start_that_says_so(hmi_for):
    driver, adb = hmi_for(codec="hevc", adb_server=lambda: FakeScrcpyServer(codec="h264"))
    with serve(driver) as client:
        with pytest.raises(Exception, match=r"codec id 0x68323634 \(h264\), not the hevc"):
            client.start()
        assert client.status()["state"] == "error" and "forward_remove" in adb.names()


# -- input ---------------------------------------------------------------------------------------------------------


def test_input_maps_touch_keys_and_text_to_scrcpy_control_messages(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.touch("down", 270.4, 600, size=(540, 1200))
        hmi.touch("move", 280, 610)
        hmi.touch("up", 280, 610)
        hmi.key("HOME")
        hmi.key("recents", "long")
        hmi.text("hi")
        server = adb.server
        wait_for(lambda: len(server.key_events()) >= 5 and server.messages_of(1), what="the control messages")
        touches = server.messages_of(2)
    assert [t[1] for t in touches] == [0, 2, 1]  # down, move, up
    assert struct.unpack_from(">iiHH", touches[0], 10) == (270, 600, 540, 1200)
    assert touches[0][22:24] == b"\xff\xff" and touches[2][22:24] == b"\x00\x00"  # pressure
    keys = server.key_events()
    assert keys[:2] == [(0, 3, 0), (1, 3, 0)]  # HOME down, up
    assert keys[2:5] == [(0, 187, 0), (0, 187, 1), (1, 187, 0)]  # RECENTS long press: down, the repeat, up
    assert server.messages_of(1)[0][5:] == b"hi"
    assert server.resets == 0


def test_a_text_then_a_touch_reach_the_device_in_that_order(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.text("hello")
        hmi.key("DEL")
        hmi.tap(10, 10)
        wait_for(lambda: len(adb.server.touches()) == 2, what="the tap")
        kinds = [m[0] for m in adb.server.control_messages]
    assert kinds[:5] == [1, 0, 0, 2, 2]  # text, DEL down and up, then the tap's down and up


def test_a_bad_input_line_is_answered_and_the_stream_goes_on(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.send({"touch": {"type": "poke", "x": 1, "y": 1}})
        hmi.send({"key": {"key": "NOT_A_KEY"}})
        hmi.send({"key": {"key": "ROTATE"}})  # the rotation is a call of its own, not a key
        hmi.send({"scroll": {}})
        errors = []
        while len(errors) < 4:
            message = hmi.read_error(5)
            assert message is not None
            errors.append(message)
        hmi.tap(5, 5)
        wait_for(lambda: adb.server.touches(), what="a good touch after the bad ones")
    assert "NOT_A_KEY" in " | ".join(errors) and "ROTATE" in " | ".join(errors)


def test_a_finger_still_down_when_the_input_ends_is_lifted_on_the_current_server(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10):
        with client.input() as hmi:
            hmi.touch("down", 100, 100)
            wait_for(lambda: adb.server.touches(), what="the touch down")
        wait_for(lambda: [t for t in adb.server.touches() if t[1] == "up"], what="the lifted finger")
    assert [t[1] for t in adb.server.touches()] == ["down", "up"]


def test_keys_held_when_the_input_ends_are_released(hmi_for):
    # a hard key takes short and long unless the exporter lists the presses it takes: this one is held
    driver, adb = hmi_for(hard_keys=[{"key": "VOLUME_UP", "presses": ["short", "long", "down", "up"]}])
    with serve(driver) as client, client.video(timeout=10):
        with client.input() as hmi:
            hmi.key("VOLUME_UP", "down")
            wait_for(lambda: adb.server.key_events(), what="the key down")
        wait_for(lambda: len(adb.server.key_events()) == 2, what="the key to be released")
    assert adb.server.key_events() == [(0, 24, 0), (1, 24, 0)]


def keyboard(code: str, kind: str) -> dict:
    return {"keyboard": {"code": code, "type": kind}}


def test_keyboard_keys_reach_the_device_as_key_codes_with_the_modifiers_that_are_down(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        for code, kind in (("ShiftLeft", "down"), ("KeyA", "down"), ("KeyA", "up"), ("ShiftLeft", "up")):
            hmi.send(keyboard(code, kind))
        hmi.send(keyboard("Enter", "down"))
        hmi.send(keyboard("Enter", "up"))
        wait_for(lambda: len(adb.server.key_messages()) == 6, what="the key messages")
        assert hmi.poll_errors(0.3) == []
    shift, num = 0x1 | 0x40, 0x200000  # SHIFT_ON | SHIFT_LEFT_ON, and Num Lock, which is on
    assert adb.server.key_messages() == [
        (0, 59, 0, shift | num),
        (0, 29, 0, shift | num),
        (1, 29, 0, shift | num),
        (1, 59, 0, num),
        (0, 66, 0, num),
        (1, 66, 0, num),
    ]


def test_a_key_that_repeats_is_sent_once_and_the_device_repeats_it(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.send(keyboard("ArrowDown", "down"))
        hmi.send(keyboard("ArrowDown", "down"))
        hmi.send(keyboard("ArrowDown", "down"))
        hmi.send(keyboard("ArrowDown", "up"))
        hmi.send(keyboard("ArrowDown", "up"))  # a late up
        hmi.tap(5, 5)
        wait_for(lambda: len(adb.server.touches()) == 2, what="the tap, which came after")
        assert hmi.poll_errors(0.3) == []
    assert [(a, k) for a, k, _, _ in adb.server.key_messages()] == [(0, 20), (1, 20)]


def test_what_scrcpy_has_no_input_for_is_refused_or_ignored_and_never_reaches_the_server(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.send(keyboard("F13", "down"))  # no Android key
        hmi.send(keyboard("IntlBackslash", "down"))
        hmi.send(keyboard("MediaPlayPause", "down"))
        hmi.send({"pointer": {"type": "wheel", "kind": "touch", "x": 5, "y": 5, "dy": 120}})  # no wheel
        hmi.send({"touch": {"type": "move", "dx": 4, "dy": 4}})  # relative motion
        errors = []
        while len(errors) < 5:
            message = hmi.read_error(5)
            assert message is not None
            errors.append(message)
        assert "F13" in errors[0] and "IntlBackslash" in errors[1] and "MediaPlayPause" in errors[2]
        assert "no wheel" in errors[3] and "relative motion" in errors[4]
        hmi.send({"pointer": {"type": "down", "kind": "mouse", "x": 5, "y": 5, "buttons": 1}})  # not declared: ignored
        hmi.send({"pointer": {"type": "down", "kind": "pen", "x": 5, "y": 5}})
        hmi.touch("down", 7, 7)
        hmi.touch("down", 9, 9, id=1)  # a second finger while one is down: ignored
        hmi.touch("move", 9, 9, id=1)
        hmi.touch("up", 9, 9, id=1)
        hmi.touch("up", 7, 7)
        wait_for(lambda: len(adb.server.touches()) == 2, what="the touch, which came after")
        assert hmi.poll_errors(0.3) == []
    assert adb.server.touches() == [("touch", "down", 7, 7), ("touch", "up", 7, 7)]  # nothing of the rest got there
    assert adb.server.key_messages() == []


def test_the_one_finger_may_have_any_pointer_id(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.touch("down", 3, 4, id=11)  # a browser numbers its contacts as it likes
        hmi.touch("move", 5, 6, id=11)
        hmi.touch("up", 5, 6, id=11)
        wait_for(lambda: len(adb.server.touches()) == 3, what="the touch")
        assert hmi.poll_errors(0.3) == []
    assert adb.server.touches() == [("touch", "down", 3, 4), ("touch", "move", 5, 6), ("touch", "up", 5, 6)]


def test_keys_held_when_the_input_ends_are_released_last_pressed_first(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10):
        with client.input() as hmi:
            hmi.send(keyboard("ControlLeft", "down"))
            hmi.send(keyboard("KeyC", "down"))
            wait_for(lambda: len(adb.server.key_messages()) == 2, what="the keys down")
        wait_for(lambda: len(adb.server.key_messages()) == 4, what="the keys to be released")
    ctrl, num = 0x1000 | 0x2000, 0x200000
    messages = adb.server.key_messages()
    assert messages[:2] == [(0, 113, 0, ctrl | num), (0, 31, 0, ctrl | num)]
    assert sorted((action, keycode) for action, keycode, _, _ in messages[2:]) == [(1, 31), (1, 113)]  # both let go
    assert messages[-1][3] == num  # and when the last one is, nothing is held


def test_a_restart_drops_the_keys_held_the_new_capture_never_saw_them(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.send(keyboard("ShiftLeft", "down"))
        wait_for(lambda: adb.server.key_messages(), what="the shift down")
        client.set_quality("low")  # the capture restarts: a new server, a new control socket
        wait_for(lambda: len(adb.servers) == 2 and client.status()["state"] == "running", what="the restart")
        hmi.send(keyboard("KeyA", "down"))
        hmi.send(keyboard("KeyA", "up"))
        hmi.send(keyboard("ShiftLeft", "up"))  # the person lets go of a shift the new capture never saw: nothing to do
        hmi.tap(5, 5)
        wait_for(lambda: len(adb.servers[1].touches()) == 2, what="the tap, which came after")
        assert hmi.poll_errors(0.3) == []
    num = 0x200000
    assert adb.servers[0].key_messages() == [(0, 59, 0, 0x1 | 0x40 | num)]  # never let go there: the server is gone
    assert adb.servers[1].key_messages() == [(0, 29, 0, num), (1, 29, 0, num)]  # no shift: not carried over


def test_the_input_of_an_open_stream_goes_on_when_the_capture_restarts(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10), client.input() as hmi:
        hmi.tap(5, 5)
        wait_for(lambda: len(adb.server.touches()) == 2, what="the first tap")
        client.set_quality("low")  # the capture restarts: a new server, a new control socket
        wait_for(lambda: len(adb.servers) == 2 and client.status()["state"] == "running", what="the restart")
        hmi.tap(6, 6)
        wait_for(lambda: len(adb.servers[1].touches()) == 2, what="the second tap, at the second server")
        assert hmi.poll_errors(0.3) == []


def test_a_view_only_stream_takes_no_input_and_its_extras_say_so(hmi_for):
    driver, adb = hmi_for(control=False, adb_server=lambda: FakeScrcpyServer(expect_control=False))
    with serve(driver) as client:
        with client.video(timeout=10) as video:
            assert video.header["width"] == 540 and "input" not in video.header
        assert "control=false" in spawns(adb)[0]
        info = client.hmi_info()
        assert info["codecs"] == ["h264", "hevc"]  # the picture is the same, only the input is gone
        assert info["input"]["pointer"]["kinds"] == [] and info["input"]["pointer"]["max_contacts"] == 0
        assert info["input"]["keyboard"] == {"codes": [], "text": False, "layout": None}
        assert info["input"]["keys"] == [] and info["input"]["hard_keys"] == []
        # the stream is refused: by a client that reads hmi_info() when it opens it, or by the exporter, at once
        with contextlib.suppress(HmiStreamClosedError), client.input() as stream:
            assert stream.read_error(10) is None
        for call in (
            lambda: client.press_key("HOME"),
            lambda: client.rotate(),
            lambda: client.type_text("x"),
            lambda: client.panel("settings"),
            lambda: client.get_clipboard(),
            lambda: client.start_app("com.example"),
        ):
            with pytest.raises(Exception, match="view only"):
                call()
        assert adb.keyevents == [] and adb.rotations == []


# -- the Android extras --------------------------------------------------------------------------------------------


def test_press_key_uses_the_control_channel_when_running_and_adb_when_not(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.press_key("BACK")  # nothing running: `input keyevent`, no scrcpy-server started
        client.press_key("POWER", "long")
        assert adb.keyevents == [(4, False), (26, True)] and not adb.servers
        with pytest.raises(Exception, match="press must be"):
            client.press_key("BACK", "down")
        with pytest.raises(Exception, match="press must be"):
            client.press_key("BACK", "tap")
        client.start()
        client.press_key("VOLUME_UP")
        wait_for(lambda: adb.server.key_events(), what="the key over the control channel")
        assert adb.keyevents == [(4, False), (26, True)]
        client.press_key("VOLUME_UP", "down")
        client.press_key("VOLUME_UP", "up")  # all four presses are open while it runs
        wait_for(lambda: len(adb.server.key_events()) == 4, what="the held key")
        with pytest.raises(Exception, match="unknown key"):
            client.press_key("NOPE")


def test_press_key_takes_a_key_code_and_numbers_arrive_as_floats(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.start()
        client.press_key(3.0)  # the key code of HOME, in a generic call
        wait_for(lambda: adb.server.key_events(), what="the key over the control channel")
        assert adb.server.key_events()[0][1] == 3


def test_rotate_toggles_between_portrait_and_landscape_and_can_be_told_where(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:  # no stream needed: it is a setting of the device
        client.rotate()
        client.rotate()
        client.rotate(3)
        client.rotate("auto")
        client.rotate()
        assert adb.rotations == [("lock", 1, 0), ("lock", 0, 0), ("lock", 3, 0), ("free", None, 0), ("lock", 1, 0)]
        for bad in (4, -1, "sideways", True, 1.5):
            with pytest.raises(Exception, match="rotation must be"):
                client.rotate(bad)
        assert not adb.servers  # none of that started the scrcpy server


def test_rotate_toggles_from_the_lock_the_device_already_has(hmi_for):
    driver, adb = hmi_for()
    adb.auto_rotate, adb.locked_rotation = False, 1  # someone left it in landscape
    with serve(driver) as client:
        client.rotate()
    assert adb.rotations == [("lock", 0, 0)]


def test_a_rotation_changes_the_picture_and_the_codec_line(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10) as video:
        media(video, 3)
        client.rotate(1)
        adb.server.change_picture(1200, 540)  # what the device's encoder does when the display turns
        while True:
            frame = video.read_frame(5)
            assert frame is not None
            if isinstance(frame, str):
                break
        assert (video.header["width"], video.header["height"]) == (1200, 540)
        assert client.hmi_info()["width"] == 1200


def test_rotate_and_the_display_are_the_hmis_own(hmi_for):
    driver, adb = hmi_for(display_id=3)
    with serve(driver) as client:
        client.rotate(2)
    assert adb.rotations == [("lock", 2, 3)]


def test_a_new_lease_forgets_the_rotation_and_the_old_error_and_the_pinned_quality(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.rotate(1)
        client.set_quality("minimal")
        client.start()  # nothing was running: the level is what it starts with
        assert "max_size=640" in spawns(adb)[0]
    driver.reset()  # what the exporter does at the start of the next lease
    assert driver._rotation is None
    status = driver.status()
    assert status["state"] == "stopped" and status["error"] is None and status["device"]["encoder"] is None
    assert status["quality"]["mode"] == "auto"
    adb.auto_rotate, adb.locked_rotation = True, 0
    with serve(driver) as client:
        client.rotate()
    assert adb.rotations[-1] == ("lock", 1, 0)  # from the device's own state, not the old lease's


def test_start_app_sends_the_name_over_the_control_channel_and_force_stop_puts_a_plus_in_front(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.start_app("com.example.settings")
        client.start_app("com.example.settings", force_stop=True)
        client.start_app("?Settings", force_stop=True)  # a search already says what it is: no plus
        wait_for(lambda: len(adb.server.messages_of(16)) >= 3, what="the app starts")
        names = [m[2:].decode() for m in adb.server.messages_of(16)]
        with pytest.raises(Exception, match="control character|1 to 255"):
            client.start_app("a\nb")
    assert names == ["com.example.settings", "+com.example.settings", "?Settings"]


def test_the_clipboard_is_set_and_read_back_and_set_waits_for_the_devices_acknowledgement(hmi_for, monkeypatch):
    from .session import ScrcpySession

    monkeypatch.setattr(ScrcpySession, "CLIPBOARD_TIMEOUT_S", 0.5)  # the wait for a clipboard that is empty
    driver, adb = hmi_for()
    with serve(driver) as client:
        assert client.get_clipboard() == ""  # nothing on it: the server says nothing, and the answer is empty
        client.set_clipboard("héllo 世界")
        assert adb.server.clipboard == "héllo 世界"
        assert client.get_clipboard() == "héllo 世界"
        client.set_clipboard("pasted", paste=True)
        assert [struct.unpack_from(">BQ?I", m)[2] for m in adb.server.messages_of(9)] == [False, True]


def test_a_clipboard_the_device_does_not_acknowledge_is_an_error(hmi_for, monkeypatch):
    from .session import ScrcpySession

    monkeypatch.setattr(ScrcpySession, "CLIPBOARD_TIMEOUT_S", 0.3)
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10):
        adb.server.ack_clipboard = False
        with pytest.raises(Exception, match="did not confirm"):
            client.set_clipboard("x")


def test_type_text_and_the_panels_go_over_the_control_channel(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        client.type_text("a" * 650)  # starts the stream
        client.panel("notifications")
        client.panel("settings")
        client.panel("collapse")
        wait_for(lambda: len(adb.server.control_messages) == 3 + 3, what="the messages")
        with pytest.raises(Exception, match="panel must be"):
            client.panel("everything")
    assert [struct.unpack_from(">I", m, 1)[0] for m in adb.server.messages_of(1)] == [300, 300, 50]
    assert [m[0] for m in adb.server.control_messages[3:]] == [5, 6, 7]


def test_the_encoders_are_listed_without_a_stream(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client:
        listed = client.list_encoders()
        assert [e["name"] for e in listed if e["codec"] == "h264"] == [
            "c2.example.avc.encoder",
            "OMX.example.video.encoder.avc",
            "c2.android.avc.encoder",
        ]
        assert next(e for e in listed if e["name"].startswith("OMX"))["alias_of"] == "c2.example.avc.encoder"
        assert not adb.servers


def test_an_operators_encoder_and_the_default_policy(hmi_for):
    named, adb = hmi_for(video_encoder="c2.android.avc.encoder")
    with serve(named) as client:
        assert client.start()["device"]["encoder"]["chosen_by"] == "configured"
    assert "video_encoder=c2.android.avc.encoder" in spawns(adb)[0]
    left, adb2 = hmi_for(encoder_policy="default")
    with serve(left) as client:
        client.start()
    assert "video_encoder" not in spawns(adb2)[0] and "list_encoders" not in adb2.names()


# -- quality -------------------------------------------------------------------------------------------------------


def test_the_stream_starts_at_the_devices_own_size_with_the_bit_rate_that_suits_it(hmi_for):
    driver, adb = hmi_for()  # a 1080x2340 phone (the fake's `wm size`), no ceiling in the config
    with serve(driver) as client:
        quality = client.start()["quality"]
    command = spawns(adb)[0]
    assert (
        "max_size=0" in command and f"video_bit_rate={auto_bit_rate(1080, 2340)}" in command and "max_fps=30" in command
    )
    assert quality["mode"] == "auto" and quality["native"] == {"width": 1080, "height": 2340}
    assert quality["ceiling"] == {"max_size": None, "bit_rate": None, "max_fps": None}
    assert quality["top"] == {"max_size": 0, "bit_rate": auto_bit_rate(1080, 2340), "max_fps": 30}
    assert [c[0] for c in adb.calls].count("wm_size") == 1  # asked once


def test_an_unreadable_display_size_is_taken_to_be_1080p(hmi_for):
    driver, adb = hmi_for()
    adb.wm_size = "no such command\n"
    with serve(driver) as client:
        status = client.start()
    assert "video_bit_rate=8000000" in spawns(adb)[0] and status["quality"]["native"] is None


def test_the_configs_numbers_are_ceilings_and_are_honored(hmi_for):
    driver, adb = hmi_for(max_size=1280, bit_rate=3_000_000, max_fps=24)
    with serve(driver) as client:
        quality = client.start()["quality"]
        suits = auto_bit_rate(*scaled(1080, 2340, 1280), 24)
        command = spawns(adb)[0]
        assert "max_size=1280" in command and f"video_bit_rate={suits}" in command and "max_fps=24" in command
        assert quality["ceiling"] == {"max_size": 1280, "bit_rate": 3_000_000, "max_fps": 24}
        client.set_quality("high")  # a level cannot go above the ceiling: high is the top
        assert f"video_bit_rate={suits}" in spawns(adb)[-1] or len(spawns(adb)) == 1


def test_set_quality_restarts_the_capture_with_the_level_and_the_viewer_stays_attached(hmi_for):
    driver, adb = hmi_for()
    with serve(driver) as client, client.video(timeout=10) as video:
        media(video, 5)
        status = client.set_quality("low")
        assert status["quality"]["mode"] == "manual" and status["quality"]["pinned"] == "low"
        wait_for(lambda: len(adb.servers) == 2, what="the capture to be restarted")
        command = spawns(adb)[1]
        assert "max_size=960" in command and "video_bit_rate=2000000" in command and "max_fps=24" in command
        # the same stream goes on from the new capture: another key picture arrives
        deadline = time.monotonic() + 10
        picture = b""
        while time.monotonic() < deadline and NAL_IDR not in nal_types(picture[8:]):
            frame = video.read_frame(5)
            assert frame is not None
            picture = frame if isinstance(frame, bytes) and len(adb.servers) == 2 else b""
        assert NAL_IDR in nal_types(picture[8:])
        with pytest.raises(Exception, match="quality must be"):
            client.set_quality("ultra")
        auto = client.set_quality("auto")
        assert auto["quality"]["mode"] == "auto" and auto["device"]["settings"]["bit_rate"] == auto_bit_rate(1080, 2340)


def test_a_failed_restart_ends_the_viewers_and_the_next_start_works(hmi_for):
    class Refusing:
        port = 1  # nothing listens: the connect is refused

        def close(self):
            pass

    servers = [FakeScrcpyServer(), Refusing(), FakeScrcpyServer()]
    driver, adb = hmi_for(adb_server=lambda: servers.pop(0), start_timeout_s=1.5, encoder_policy="default")
    with serve(driver) as client, client.video(timeout=10) as video:
        media(video, 3)
        with pytest.raises(Exception, match="did not accept|no picture|connect"):
            client.set_quality("minimal")
        assert ends(video)  # the viewer's stream ends: it does not hang on a stream that is not coming back
        assert client.status()["state"] in ("error", "stopped")
        with client.video(timeout=10) as again:
            assert again.header["width"] == 540
    assert len(adb.servers) == 3


# -- a display that has gone quiet ----------------------------------------------------------------------------------


def test_a_still_hmi_is_not_a_stall_but_a_server_that_is_gone_is(hmi_for):
    driver, adb = hmi_for(stall_s=1.0, adb_server=lambda: FakeScrcpyServer(fps=30, frames=40))  # then a still display
    with serve(driver) as client:
        with client.video(timeout=10) as video:
            media(video, 5)
            wait_for(lambda: "server_alive" in adb.names(), timeout=15, what="the probe of the server")
            assert driver.status()["state"] == "running"  # it is there: the display is only still, nothing is restarted
            assert len(adb.servers) == 1 and adb.server.resets == 0
            adb.server_is_alive = False  # the server process on the device is gone and nothing told the exporter
            assert ends(video, timeout=20)
        assert "stalled" in (driver.status()["error"] or "")
        adb.server_is_alive = True
        with client.video(timeout=10) as again:  # a viewer that comes back starts a new server
            assert again.header["width"] == 540
        assert len(adb.servers) == 2


def test_a_device_that_does_not_answer_is_asked_twice_before_it_is_believed_gone(hmi_for):
    driver, adb = hmi_for(stall_s=1.0, adb_server=lambda: FakeScrcpyServer(fps=30, frames=30))
    driver._the_source()._sleep = lambda s: None  # type: ignore[attr-defined]
    adb.server_is_alive = None  # adb does not answer
    with serve(driver) as client, client.video(timeout=10) as video:
        media(video, 5)
        assert ends(video, timeout=25)
        assert [c[0] for c in adb.calls].count("server_alive") >= 2
        assert "stalled" in (driver.status()["error"] or "")


# -- the recording -------------------------------------------------------------------------------------------------


def test_a_recording_is_muxed_on_the_exporter_from_the_devices_own_pictures(hmi_for):
    driver, _ = hmi_for(adb_server=lambda: FakeScrcpyServer(fps=60))
    with serve(driver) as client:
        assert client.start_recording(True, False)["active"] is True  # what a UI's Record button asks for
        wait_for(lambda: client.recording_status()["frames"] > 10, what="recorded frames")
        result = client.stop_recording()
    assert result["delivered"] is True and result["name"] == "recording.mp4"
    assert result["data"][4:8] == b"ftyp" and b"moov" in result["data"][:2000] and b"moof" in result["data"]
    assert (result["width"], result["height"]) == (540, 1200)


def test_the_status_of_a_running_stream_is_plain_data(hmi_for):
    driver, _ = hmi_for()
    with serve(driver) as client:
        client.start()
        assert json.loads(json.dumps(client.status()))["device"]["name"] == "Fake Phone"
