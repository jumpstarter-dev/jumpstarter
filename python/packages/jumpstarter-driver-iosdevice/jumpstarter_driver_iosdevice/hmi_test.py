"""The scrios HMI: its configuration, what it declares, the command line it builds, the ``hmi:`` option of the device
driver and the helpers that look at the binary. Nothing here needs a device; what runs a process is in
``hmi_process_test``.
"""

import os
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import anyio
import pytest
from anyio import to_thread
from jumpstarter_driver_hmi.client import HmiClient
from jumpstarter_driver_hmi.common import KEY_CODE, canonical_key

from . import hmi as hmi_module
from .client import IosDeviceClient
from .driver import IosDevice
from .fake_scrios_testing import write_fake_scrios
from .hid_usages import CODES, MAX_USAGE, USAGES, usage_argument
from .hmi import (
    BUTTONS,
    DEFAULT_HARD_KEYS,
    HMI_OPTIONS,
    KEYS,
    RELEASES_URL,
    VIDEO_CODECS,
    IosDeviceHmi,
    IosDeviceHmiSource,
    find_scrios,
    scrios_version,
)
from .scrios_framed import CODEC_FAMILIES
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

UDID = "00008110-001234567890001E"
SOCKET = "/tmp/jumpstarter-test-usbmuxd.sock"


@pytest.fixture(autouse=True)
def fresh_versions():
    hmi_module._version.cache_clear()


def make(**config) -> IosDeviceHmi:
    return IosDeviceHmi(device=IosDevice(udid=UDID, usbmux_socket=SOCKET), **config)


def command(driver: IosDeviceHmi, program: str = "/opt/scrios", udid: str = UDID, **options) -> list[str]:
    source = driver._the_source()
    assert isinstance(source, IosDeviceHmiSource)
    return source.command(program, udid, **options)


# -- configuration -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {"executable": ""},
        {"executable": 5},
        {"usbmuxd": ""},
        {"udid": "../phone"},
        {"udid": ""},
        {"display_id": -1},
        {"display_id": True},
        {"display_id": "1"},
        {"visible_size": "1206"},
        {"visible_size": "0x100"},
        {"visible_size": 1206},
        {"hid_space": "hmi"},
        {"hid_space": 1},
        {"hid_swap_xy": "yes"},
        {"hid_flip_x": 1},
        {"hid_flip_y": None},
        {"control": False, "hid_flip_x": True},
        {"control": False, "hid_space": "ui"},
        {"extra_args": "--log-answer"},
        {"extra_args": [5]},
        {"extra_args": [""]},
        {"extra_args": ["--format", "raw"]},
        {"extra_args": ["--format=raw"]},
        {"extra_args": ["--output", "x"]},
        {"extra_args": ["-o", "-"]},
        {"extra_args": ["-o-"]},
        {"extra_args": ["--udid", "x"]},
        {"extra_args": ["-sABC"]},
        {"extra_args": ["--usbmuxd=x"]},
        {"extra_args": ["--no-control"]},
        {"extra_args": ["-n"]},
        {"extra_args": ["--video-codec", "h265"]},
        {"extra_args": ["--no-stdin"]},
        {"extra_args": ["--probe-secs", "5"]},
        {"extra_args": ["--list-devices"]},
        {"stop_grace_s": 0},
        {"stop_grace_s": float("nan")},
        {"restart_backoff_s": -1},
        {"restart_backoff_s": 5, "restart_backoff_max_s": 1},
        {"idle_stop_s": -1},
        {"start_timeout_s": 0},
        {"hard_keys": "HOME"},
        {"codec": "mjpeg"},
        {"codec": "h265"},
        {"codec": "vp9"},
        {"codec": 264},
    ],
)
def test_a_wrong_option_is_a_configuration_error(config):
    with pytest.raises(ConfigurationError):
        make(**config)


def test_a_hmi_without_a_device_needs_a_udid():
    with pytest.raises(ConfigurationError, match="udid"):
        IosDeviceHmi()
    assert IosDeviceHmi(udid=UDID).device is None


def test_the_defaults_are_the_documented_ones():
    hmi = make()
    assert hmi.executable == "scrios" and hmi.extra_args == [] and hmi.codec is None
    assert hmi.usbmuxd is None and hmi.udid is None and hmi.display_id is None and hmi.visible_size is None
    assert hmi.control is True and hmi.hid_space is None
    assert (hmi.stop_grace_s, hmi.restart_backoff_s, hmi.restart_backoff_max_s) == (2.0, 1.0, 30.0)
    assert (hmi.start_timeout_s, hmi.stall_s, hmi.idle_stop_s, hmi.stop_deadline_s) == (
        30.0,
        8.0,
        60.0,
        6.0,
    )
    assert hmi.driver_type == "hmi"
    assert IosDeviceHmi.client() == "jumpstarter_driver_hmi.client.HmiClient"


def test_building_the_driver_runs_and_looks_up_nothing(monkeypatch):
    monkeypatch.setenv("PATH", "")
    hmi = make(executable="a-scrios-that-is-not-installed")
    assert hmi._source is None
    assert hmi.status()["state"] == "stopped"


@pytest.mark.parametrize("option", [{"max_size": 1000}, {"bit_rate": 4_000_000}, {"max_fps": 30}])
def test_the_ceilings_that_scrios_cannot_honor_are_refused_not_silently_taken(option):
    with pytest.raises(ConfigurationError, match="not options here"):
        make(**option)


def test_what_the_hmi_says_about_itself():
    info = make().hmi_info()
    assert info["interface"] == "hmi"
    assert info["codecs"] == ["h264", "hevc"] and info["codec"] == "h264"
    assert info["recording"] is True
    assert info["input"]["pointer"] == {
        "kinds": ["touch"],
        "absolute": True,
        "relative": False,
        "buttons": False,
        "wheel": False,
        "max_contacts": 1,
    }
    assert info["input"]["keyboard"] == {"codes": list(CODES), "text": True, "layout": "us"}
    assert info["input"]["clipboard"] is False
    assert info["input"]["keys"] == list(KEYS)
    assert [k["key"] for k in info["input"]["hard_keys"]] == [
        "HOME",
        "APP_SWITCH",
        "VOLUME_UP",
        "VOLUME_DOWN",
        "MUTE",
        "ASSIST",
    ]
    assert info["quality"] == {"adjustable": False, "auto": False, "levels": []}
    assert make()._the_source().capabilities().keyframe_cost == "cheap"
    with pytest.raises(ValueError, match="cannot be adjusted"):
        make().set_quality("low")


def test_the_hmi_declares_the_subset_of_the_interface_that_scrios_supports_and_nothing_more():
    caps = make()._the_source().capabilities()
    assert caps.codecs == ("h264", "hevc")  # H.264 is the default; MJPEG is not offered
    assert caps.pointer.kinds == ("touch",) and caps.pointer.max_contacts == 1
    assert not (caps.pointer.relative or caps.pointer.buttons or caps.pointer.wheel)
    assert caps.keyboard.codes == CODES and caps.keyboard.text is True and caps.keyboard.layout == "us"
    assert caps.clipboard is False and caps.quality is None and caps.rotation is True


def test_the_codecs_are_the_ones_scrios_streams_under_the_names_of_its_option():
    assert tuple(VIDEO_CODECS) == CODEC_FAMILIES == ("h264", "hevc")
    assert VIDEO_CODECS == {"h264": "h264", "hevc": "h265"}  # scrios's --video-codec says h265, not hevc


def test_a_codec_the_hmi_does_not_make_is_refused_when_it_is_asked_for_too():
    hmi = make()
    source = hmi._the_source()
    assert isinstance(source, IosDeviceHmiSource)
    with pytest.raises(RuntimeError, match="scrios streams h264 or hevc, not mjpeg"):
        source.command("/opt/scrios", UDID, "mjpeg")
    with pytest.raises(RuntimeError, match="could not start: scrios streams h264 or hevc, not mjpeg"):
        source.start(MagicMock(), None, 1.0, codec="mjpeg")
    with pytest.raises(ValueError, match="produces h264, hevc, not 'mjpeg'"):
        hmi.start(codec="mjpeg")


def test_the_codec_option_chooses_the_stream_and_hmi_info_says_so():
    assert make(codec="hevc").hmi_info()["codec"] == "hevc"
    assert make(codec="h264").hmi_info()["codec"] == "h264"


def test_every_key_it_accepts_is_a_canonical_name_and_every_hard_key_one_of_them():
    assert all(canonical_key(k) == k for k in KEYS)
    assert {h.key for h in DEFAULT_HARD_KEYS} <= set(KEYS)
    assert set(BUTTONS.values()) == {
        "home",
        "lock",
        "power",
        "volume-up",
        "volume-down",
        "mute",
        "siri",
        "app-switcher",
    }
    for alias, canonical in (("SIRI", "ASSIST"), ("APP_SWITCHER", "APP_SWITCH"), ("recents", "APP_SWITCH")):
        assert canonical_key(alias) == canonical


def test_the_exporter_can_replace_the_hard_keys():
    info = make(hard_keys=["home", {"key": "volume-up", "label": "Louder", "presses": ["short"]}]).hmi_info()
    assert info["input"]["hard_keys"] == [
        {"key": "HOME", "label": "Home", "presses": ["short", "long"]},
        {"key": "VOLUME_UP", "label": "Louder", "presses": ["short"]},
    ]


def test_lock_and_power_are_not_offered_by_default():
    """Pressing them can lock the user out of a device whose only way in is its hmi: they are opt-in."""
    hmi = make()
    keys = hmi.hmi_info()["input"]["keys"]
    assert "LOCK" not in keys and "POWER" not in keys
    assert not {"LOCK", "POWER"} & {k["key"] for k in hmi.hmi_info()["input"]["hard_keys"]}
    assert not {"LOCK", "POWER"} & {k.key for k in DEFAULT_HARD_KEYS}
    assert set(hmi._the_source().capabilities().optional_keys) == {"LOCK", "POWER"}


def test_an_exporter_can_add_lock_or_power_by_listing_it():
    info = make(hard_keys=["HOME", {"key": "lock", "label": "Sleep", "presses": ["short"]}]).hmi_info()
    assert [k["key"] for k in info["input"]["hard_keys"]] == ["HOME", "LOCK"]
    assert "LOCK" in info["input"]["keys"] and "POWER" not in info["input"]["keys"]  # only what was listed


def test_a_view_only_hmi_takes_no_input():
    hmi = make(control=False)
    info = hmi.hmi_info()
    assert info["codecs"] == ["h264", "hevc"]  # the picture is the same
    assert info["input"] == {
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
    assert hmi._the_source().input_sink() is None
    assert hmi._the_source().capabilities().has_input is False


# -- the keyboard keys -------------------------------------------------------------------------------------------------


def test_the_declared_keyboard_codes_are_exactly_the_keys_of_the_usage_table():
    assert make().hmi_info()["input"]["keyboard"]["codes"] == list(USAGES) == list(CODES)
    # modifiers, letters, digits, the rest of the main block, F1 to F12, editing and arrows, the numeric keypad
    assert len(CODES) == len(set(CODES)) == 8 + 26 + 10 + 17 + 12 + 10 + 17


def test_every_code_is_a_keyboard_event_code_and_every_usage_one_scrios_takes_and_none_twice():
    assert all(KEY_CODE.fullmatch(code) for code in USAGES)
    usages = list(USAGES.values())
    assert len(set(usages)) == len(usages)  # one key, one usage
    assert usages == sorted(usages)  # in the order of the usage table
    assert all(0x04 <= u <= MAX_USAGE for u in usages)  # 0x01 to 0x03 are errors, not keys


@pytest.mark.parametrize(
    ("code", "usage"),
    [
        ("KeyA", 0x04),
        ("KeyM", 0x10),
        ("KeyZ", 0x1D),
        ("Digit1", 0x1E),
        ("Digit9", 0x26),
        ("Digit0", 0x27),
        ("Enter", 0x28),
        ("Escape", 0x29),
        ("Backspace", 0x2A),
        ("Tab", 0x2B),
        ("Space", 0x2C),
        ("Minus", 0x2D),
        ("Equal", 0x2E),
        ("BracketLeft", 0x2F),
        ("BracketRight", 0x30),
        ("Backslash", 0x31),
        ("Semicolon", 0x33),
        ("Quote", 0x34),
        ("Backquote", 0x35),
        ("Comma", 0x36),
        ("Period", 0x37),
        ("Slash", 0x38),
        ("CapsLock", 0x39),
        ("F1", 0x3A),
        ("F12", 0x45),
        ("Insert", 0x49),
        ("Home", 0x4A),
        ("PageUp", 0x4B),
        ("Delete", 0x4C),
        ("End", 0x4D),
        ("PageDown", 0x4E),
        ("ArrowRight", 0x4F),
        ("ArrowLeft", 0x50),
        ("ArrowDown", 0x51),
        ("ArrowUp", 0x52),
        ("NumLock", 0x53),
        ("NumpadDivide", 0x54),
        ("NumpadMultiply", 0x55),
        ("NumpadSubtract", 0x56),
        ("NumpadAdd", 0x57),
        ("NumpadEnter", 0x58),
        ("Numpad1", 0x59),
        ("Numpad9", 0x61),
        ("Numpad0", 0x62),
        ("NumpadDecimal", 0x63),
        ("ControlLeft", 0xE0),
        ("ShiftLeft", 0xE1),
        ("AltLeft", 0xE2),
        ("MetaLeft", 0xE3),
        ("ControlRight", 0xE4),
        ("ShiftRight", 0xE5),
        ("AltRight", 0xE6),
        ("MetaRight", 0xE7),
    ],
)
def test_a_key_has_the_usage_the_ui_events_specification_gives_it(code, usage):
    assert USAGES[code] == usage


def test_the_whole_table_is_the_ranges_it_is_made_of():
    assert [USAGES[f"Key{c}"] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"] == list(range(0x04, 0x1E))
    assert [USAGES[f"Digit{n}"] for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)] == list(range(0x1E, 0x28))
    assert [USAGES[f"F{n}"] for n in range(1, 13)] == list(range(0x3A, 0x46))
    assert [USAGES[f"Numpad{n}"] for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 0)] == list(range(0x59, 0x63))
    assert sorted(set(USAGES.values()) & set(range(0x53, 0x64))) == list(range(0x53, 0x64))  # the keypad, whole


@pytest.mark.parametrize("code", ["PrintScreen", "ScrollLock", "Pause", "ContextMenu", "IntlBackslash", "F13", "Lang1"])
def test_keys_that_only_some_keyboards_have_are_not_declared(code):
    assert code not in USAGES


def test_a_usage_is_written_as_scrios_writes_it_two_lower_case_hex_digits():
    assert [usage_argument(c) for c in ("KeyA", "Enter", "Backspace", "ShiftLeft", "MetaRight")] == [
        "04",
        "28",
        "2a",
        "e1",
        "e7",
    ]
    with pytest.raises(KeyError):
        usage_argument("PrintScreen")


# -- the command line --------------------------------------------------------------------------------------------------


def test_the_command_line_is_the_documented_one_and_nothing_else():
    assert command(make()) == [
        "/opt/scrios",
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


def test_hevc_is_asked_for_with_the_value_scrios_calls_it_h265():
    assert command(make(), codec="hevc")[5:8] == [UDID, "--video-codec", "h265"]
    assert "hevc" not in command(make(), codec="hevc")  # scrios's parser has no such value
    assert command(make(), codec="h264")[6:8] == ["--video-codec", "h264"]


def test_without_control_scrios_is_run_with_no_control():
    argv = command(make(control=False))
    assert argv[:8] == ["/opt/scrios", "stream", "--format", "framed", "--udid", UDID, "--video-codec", "h264"]
    assert argv[8] == "--no-control"
    assert "--hid-space" not in argv


def test_the_parents_usbmux_address_is_used_whatever_its_kind():
    network = IosDevice(transport="network", udid=UDID, usbmux_host="usbmux.example", usbmux_port=27016)
    assert command(IosDeviceHmi(device=network))[-2:] == ["--usbmuxd", "usbmux.example:27016"]
    assert command(make())[-2:] == ["--usbmuxd", f"UNIX:{SOCKET}"]


def test_the_hmis_own_udid_and_usbmux_address_win():
    argv = command(make(udid="OTHER-1", usbmuxd="127.0.0.1:27015"), udid="OTHER-1")
    assert argv[argv.index("--udid") + 1] == "OTHER-1"
    assert argv[argv.index("--usbmuxd") + 1] == "127.0.0.1:27015"


def test_a_hmi_without_a_device_driver_passes_no_usbmux_address():
    assert "--usbmuxd" not in command(IosDeviceHmi(udid=UDID))


def test_the_display_the_size_the_touch_mapping_and_the_extra_arguments_follow_in_that_order():
    argv = command(
        make(
            display_id=2,
            visible_size="1206x2622",
            hid_space="panel",
            hid_swap_xy=True,
            hid_flip_x=True,
            hid_flip_y=True,
            extra_args=["--stats-interval-ms", "1000", "--rtp-stall-keyframe-ms=3000"],
        )
    )
    assert argv[8:] == [
        "--usbmuxd",
        f"UNIX:{SOCKET}",
        "--display-id",
        "2",
        "--visible-size",
        "1206x2622",
        "--hid-space",
        "panel",
        "--hid-swap-xy",
        "--hid-flip-x",
        "--hid-flip-y",
        "--stats-interval-ms",
        "1000",
        "--rtp-stall-keyframe-ms=3000",
    ]


def test_the_touch_mapping_is_left_to_scrios_unless_asked_for():
    argv = command(make())
    assert not any(a.startswith("--hid") or a in ("--display-id", "--visible-size") for a in argv)


@pytest.mark.parametrize("display_id", [0, 1, 7])
def test_any_display_number_is_passed(display_id):
    assert command(make(display_id=display_id)).count("--display-id") == 1


# -- the ``hmi:`` option of the device driver ----------------------------------------------------------------------


def test_the_hmi_child_is_built_from_the_option_only():
    assert "hmi" not in IosDevice(udid=UDID).children
    device = IosDevice(udid=UDID, forward_ports=[9100], hmi={})
    assert set(device.children) == {"port_9100", "hmi"}
    child = device.children["hmi"]
    assert isinstance(child, IosDeviceHmi) and child.device is device
    configured = IosDevice(udid=UDID, hmi={"executable": "/opt/scrios", "display_id": 1, "control": False})
    child = configured.children["hmi"]
    assert isinstance(child, IosDeviceHmi)
    assert (child.executable, child.display_id, child.control) == ("/opt/scrios", 1, False)


def test_it_sits_beside_the_appium_child():
    device = IosDevice(udid=UDID, appium={}, hmi={})
    assert set(device.children) == {"appium", "hmi"}


def test_a_hmi_child_that_was_written_by_hand_conflicts_with_the_option():
    other = make()
    with pytest.raises(ConfigurationError, match="conflicts"):
        IosDevice(udid=UDID, hmi={}, children={"hmi": other})
    assert IosDevice(udid=UDID, children={"hmi": other}).children["hmi"] is other  # but without the option, fine


@pytest.mark.parametrize(
    "hmi", [True, "yes", ["executable"], {"unknown": 1}, {"device": None}, {"children": {}}, {"executable": 5}]
)
def test_the_option_must_be_a_mapping_of_known_options_with_good_values(hmi):
    with pytest.raises(ConfigurationError):
        IosDevice(udid=UDID, hmi=hmi)


def test_an_unknown_option_is_named_with_the_known_ones():
    with pytest.raises(ConfigurationError, match=r"unknown hmi options: not_an_option \(known: .*executable"):
        IosDevice(udid=UDID, hmi={"not_an_option": "x"})


def test_the_options_are_the_fields_of_the_hmi_without_the_ones_the_driver_owns():
    assert {"executable", "extra_args", "usbmuxd", "udid", "display_id", "visible_size", "control", "hard_keys"} <= (
        HMI_OPTIONS
    )
    assert {"stop_grace_s", "restart_backoff_s", "restart_backoff_max_s", "idle_stop_s", "stop_deadline_s"} <= (
        HMI_OPTIONS
    )
    assert not HMI_OPTIONS & {"device", "uuid", "labels", "children"}
    assert not HMI_OPTIONS & {"max_bitrate", "queue_depth"}  # scrios sets its own bit rate; the hub owns the queues
    IosDevice(udid=UDID, hmi=dict.fromkeys(("executable",), "scrios"))


def test_the_hmi_is_closed_before_the_usbmux_connection_goes_away(monkeypatch):
    device = IosDevice(udid=UDID, hmi={})
    order = []
    hmi = device.children["hmi"]
    monkeypatch.setattr(hmi, "close", lambda: order.append("hmi"))
    monkeypatch.setattr(device, "_invalidate_trust", lambda: order.append("usbmux"))
    device.close()
    assert order[:2] == ["hmi", "usbmux"]


def test_a_device_without_the_option_closes_as_before(monkeypatch):
    device = IosDevice(udid=UDID)
    order = []
    monkeypatch.setattr(device, "_invalidate_trust", lambda: order.append("usbmux"))
    device.close()
    assert order == ["usbmux"]


def test_without_the_hmi_package_the_option_says_what_to_install(monkeypatch):
    for name in [m for m in sys.modules if m == "jumpstarter_driver_hmi" or m.startswith("jumpstarter_driver_hmi.")]:
        monkeypatch.setitem(sys.modules, name, None)  # as if the package were not installed
    monkeypatch.delitem(sys.modules, "jumpstarter_driver_iosdevice.hmi")
    with pytest.raises(ConfigurationError, match=r"pip install jumpstarter-driver-iosdevice\[hmi\]"):
        IosDevice(udid=UDID, hmi={})
    assert "hmi" not in IosDevice(udid=UDID).children  # no option, no import, no complaint


def test_an_import_error_that_is_not_the_hmi_package_is_not_hidden_behind_the_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "jumpstarter_driver_iosdevice.scrios_framed", None)  # one of our own is broken
    monkeypatch.delitem(sys.modules, "jumpstarter_driver_iosdevice.hmi")
    with pytest.raises(ImportError):
        IosDevice(udid=UDID, hmi={})


def test_a_plain_device_never_imports_the_hmi_package():
    code = (
        "import sys\n"
        "from jumpstarter_driver_iosdevice.driver import IosDevice\n"
        "from jumpstarter_driver_iosdevice.client import IosDeviceClient\n"
        f"IosDevice(udid={UDID!r}, appium={{}})\n"
        "assert not [m for m in sys.modules if m.startswith('jumpstarter_driver_hmi')], 'imported'\n"
        "assert 'jumpstarter_driver_iosdevice.hmi' not in sys.modules\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr


# -- the client --------------------------------------------------------------------------------------------------------


def test_the_client_finds_the_hmi_through_the_driver_tree():
    with serve(IosDevice(udid=UDID, hmi={})) as client:
        assert isinstance(client, IosDeviceClient)
        assert isinstance(client.hmi, HmiClient) and client.children["hmi"] is client.hmi
        assert client.hmi.status()["state"] == "stopped"
        assert "hmi" in client.cli().commands
    with serve(IosDevice(udid=UDID)) as plain:
        assert "hmi" not in plain.cli().commands
        with pytest.raises(AttributeError, match="not configured"):
            _ = plain.hmi


# -- the binary --------------------------------------------------------------------------------------------------------


def test_a_missing_binary_says_what_to_do(monkeypatch):
    monkeypatch.setenv("PATH", "")
    with pytest.raises(RuntimeError, match="scrios was not found") as raised:
        find_scrios("scrios")
    text = str(raised.value)
    assert "executable:" in text and "PATH" in text and RELEASES_URL in text and "'scrios'" in text
    with pytest.raises(RuntimeError, match="scrios was not found"):
        find_scrios("/no/such/dir/scrios")


def test_the_binary_is_a_path_or_a_name_on_path(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    assert find_scrios(str(path)) == str(path)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin")
    assert find_scrios("scrios") == str(path)
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="was not found"):
        find_scrios(str(path))  # not executable


def test_the_version_is_what_scrios_says_and_is_asked_once_per_file(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    monkeypatch.setenv("FAKE_SCRIOS_VERSION", "scrios 1.2.3")
    asked = []
    real = subprocess.run
    monkeypatch.setattr(hmi_module.subprocess, "run", lambda *a, **k: asked.append(a) or real(*a, **k))
    assert scrios_version(str(path)) == "1.2.3"
    assert scrios_version(str(path)) == "1.2.3"
    assert len(asked) == 1
    path.write_text(path.read_text() + "# changed\n")
    scrios_version(str(path))
    assert len(asked) == 2


@pytest.mark.parametrize("said", ["not scrios at all", "", "ffmpeg version 7"])
def test_a_program_that_does_not_say_it_is_scrios_is_refused(tmp_path, monkeypatch, said):
    path = write_fake_scrios(tmp_path)
    monkeypatch.setenv("FAKE_SCRIOS_VERSION", said)
    with pytest.raises(RuntimeError, match="does not look like scrios"):
        scrios_version(str(path))


def test_a_scrios_whose_version_fails_is_refused_and_asked_again_next_time(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    monkeypatch.setenv("FAKE_SCRIOS_VERSION_EXIT", "3")
    with pytest.raises(RuntimeError, match="does not look like scrios"):
        scrios_version(str(path))
    monkeypatch.delenv("FAKE_SCRIOS_VERSION_EXIT")
    assert scrios_version(str(path)) == "0.0.0-fake"  # a failure is not remembered


def test_a_version_probe_that_was_killed_by_a_signal_is_asked_again(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    real_run = hmi_module.subprocess.run
    outcomes = []

    def run(*args, **kwargs):
        done = real_run(*args, **kwargs)
        if not outcomes:  # the first child dies of a stray SIGPIPE before it can answer
            done = hmi_module.subprocess.CompletedProcess(done.args, -13, "", "stray output")
        outcomes.append(done.returncode)
        return done

    monkeypatch.setattr(hmi_module.subprocess, "run", run)
    assert scrios_version(str(path)) == "0.0.0-fake"
    assert outcomes == [-13, 0]


def test_a_version_probe_that_is_always_killed_is_refused_after_a_few_tries(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    tries = []

    def run(command, **kwargs):
        tries.append(command)
        return hmi_module.subprocess.CompletedProcess(command, -9, "", "")

    monkeypatch.setattr(hmi_module.subprocess, "run", run)
    with pytest.raises(RuntimeError, match=r"does not look like scrios.*exit status -9"):
        scrios_version(str(path))
    assert len(tries) == hmi_module.VERSION_ATTEMPTS


def test_a_scrios_that_does_not_answer_in_time_is_refused(tmp_path, monkeypatch):
    path = write_fake_scrios(tmp_path)
    monkeypatch.setenv("FAKE_SCRIOS_VERSION_DELAY", "5")
    monkeypatch.setattr(hmi_module, "VERSION_TIMEOUT_S", 0.5)
    with pytest.raises(RuntimeError, match="did not answer within 0.5 s"):
        scrios_version(str(path))


def test_a_file_that_cannot_be_run_is_refused(tmp_path):
    path = tmp_path / "scrios"
    path.write_text("not a program")
    path.chmod(0o755)
    with pytest.raises(RuntimeError, match="could not be run"):
        scrios_version(str(path))
    with pytest.raises(RuntimeError, match="cannot be read"):
        scrios_version(str(tmp_path / "gone"))


# -- which device ------------------------------------------------------------------------------------------------------


def test_the_udid_is_the_hmis_then_the_devices_then_what_its_usb_port_holds_now(monkeypatch):
    assert make(udid="MINE-1")._resolve_udid() == "MINE-1"
    assert make()._resolve_udid() == UDID

    device = IosDevice(usb_port="1-4.2")
    hmi = IosDeviceHmi(device=device)
    answers = [{"present": True, "udid": "FROM-PORT-1"}]

    async def info():
        return answers[0]

    monkeypatch.setattr(device, "info", info)
    assert hmi._resolve_udid() == "FROM-PORT-1"  # from a thread that is not a worker of the exporter
    answers[0] = {"present": False, "reason": "no iOS device at USB port 1-4.2"}
    with pytest.raises(RuntimeError, match="no iOS device at USB port 1-4.2"):
        hmi._resolve_udid()


def test_the_udid_is_found_from_a_worker_thread_of_the_exporter_too(monkeypatch):
    device = IosDevice(usb_port="1-4.2")
    hmi = IosDeviceHmi(device=device)

    async def info():
        return {"present": True, "udid": "FROM-WORKER-1"}

    monkeypatch.setattr(device, "info", info)
    found: list[str] = []
    errors: list[BaseException] = []

    async def from_worker():
        try:
            found.append(await to_thread.run_sync(hmi._resolve_udid))
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    thread = threading.Thread(target=anyio.run, args=(from_worker,))
    thread.start()
    thread.join(10)
    assert found == ["FROM-WORKER-1"], errors


def test_an_error_of_the_device_lookup_itself_is_not_taken_for_a_missing_event_loop(monkeypatch):
    device = IosDevice(usb_port="1-4.2")

    async def info():
        raise RuntimeError("usbmuxd is down")

    monkeypatch.setattr(device, "info", info)
    with pytest.raises(RuntimeError, match="usbmuxd is down"):
        IosDeviceHmi(device=device)._resolve_udid()
