"""The client of an Android device's HMI: the generic HMI commands, and the Android ones added to the group."""

from __future__ import annotations

import json

import pytest
from jumpstarter_driver_hmi.client import HmiClient
from jumpstarter_driver_hmi.testing.conformance import wait_for

from .client import AdbDeviceHmiClient
from .fakes import build_hmi, invoke
from jumpstarter.common.utils import serve

GENERIC = {
    "status",
    "info",
    "start",
    "stop",
    "quality",
    "recording",
    "record",
    "key",
    "text",
    "tap",
    "swipe",
    "click",
    "scroll",
    "press",
    "turn",
    "push",
    "view",
}
#: ``rotate`` here is the display's orientation; the generic group's command to turn a rotary knob is ``turn``.
ANDROID = {"rotate", "app", "clipboard", "panel", "encoders"}


@pytest.fixture
def hmi(tmp_path):
    driver, adb = build_hmi(tmp_path)
    with serve(driver) as client:
        assert isinstance(client, AdbDeviceHmiClient)
        yield client, adb
    driver.close()
    adb.close()


def run(client, *args):
    result = invoke(client.cli(), list(args))
    assert result.exit_code == 0, result.output
    return result.output


def test_the_group_is_the_generic_hmis_with_the_android_commands_added(hmi):
    client, _ = hmi
    group = client.cli()
    assert set(group.commands) == GENERIC | ANDROID
    assert set(HmiClient.cli(client).commands) == GENERIC  # the base group is not changed by the extras
    assert set(group.commands["clipboard"].commands) == {"get", "set"}


def test_the_generic_commands_are_the_hmi_clients_own(hmi):
    client, _ = hmi
    assert json.loads(run(client, "status"))["state"] == "stopped"
    info = json.loads(run(client, "info", "--json"))
    assert info["input"]["pointer"]["max_contacts"] == 1 and info["codecs"] == ["h264", "hevc"]
    assert json.loads(run(client, "start"))["state"] == "running"
    assert "state: stopped" in run(client, "stop")
    assert json.loads(run(client, "quality", "low"))["mode"] == "manual"


def test_key_is_a_key_press_that_works_without_the_stream(hmi):
    client, adb = hmi
    run(client, "key", "BACK")
    run(client, "key", "POWER", "--press", "long")
    assert adb.keyevents == [(4, False), (26, True)] and not adb.servers


def test_text_types_into_the_focused_field(hmi):
    client, adb = hmi
    run(client, "text", "hello")
    wait_for(lambda: adb.server.messages_of(1), what="the text")
    assert adb.server.messages_of(1)[0][5:] == b"hello"


def test_rotate_takes_a_quarter_turn_count_or_auto_or_nothing(hmi):
    client, adb = hmi
    run(client, "rotate")
    run(client, "rotate", "3")
    run(client, "rotate", "auto")
    assert adb.rotations == [("lock", 1, 0), ("lock", 3, 0), ("free", None, 0)]


def test_app_starts_an_app_and_force_stop_stops_it_first(hmi):
    client, adb = hmi
    run(client, "app", "com.example.app")
    run(client, "app", "com.example.app", "--force-stop")
    wait_for(lambda: len(adb.server.messages_of(16)) == 2, what="the app starts")
    assert [m[2:] for m in adb.server.messages_of(16)] == [b"com.example.app", b"+com.example.app"]


def test_the_clipboard_is_set_and_printed(hmi):
    client, adb = hmi
    run(client, "clipboard", "set", "copied", "--paste")
    assert adb.server.clipboard == "copied"
    assert run(client, "clipboard", "get") == "copied\n"


def test_panel_pulls_down_a_panel_and_knows_only_three(hmi):
    client, adb = hmi
    run(client, "panel", "settings")
    run(client, "panel", "collapse")
    wait_for(lambda: len(adb.server.control_messages) == 2, what="the panels")
    assert [m[0] for m in adb.server.control_messages] == [6, 7]
    assert invoke(client.cli(), ["panel", "everything"]).exit_code != 0


def test_encoders_lists_what_the_device_has_and_marks_the_hardware_ones(hmi):
    client, _ = hmi
    lines = run(client, "encoders").splitlines()
    assert lines[0].split() == ["h264", "c2.example.avc.encoder", "hw"]
    assert lines[1].split() == [
        "h264",
        "OMX.example.video.encoder.avc",
        "hw",
        "(alias",
        "for",
        "c2.example.avc.encoder)",
    ]
    assert lines[2].split() == ["h264", "c2.android.avc.encoder", "sw"]


def test_the_methods_are_calls_of_the_exporter(hmi):
    client, adb = hmi
    client.press_key("HOME")
    client.type_text("x")
    client.start_app("com.example.app")
    client.set_clipboard("c")
    assert client.get_clipboard() == "c"
    client.panel("notifications")
    client.rotate(2)
    assert [e["codec"] for e in client.list_encoders()][:1] == ["h264"]
    assert adb.rotations == [("lock", 2, 0)]
