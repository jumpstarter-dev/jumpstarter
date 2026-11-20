"""The adb commands of the hmi: what they ask adb for, against which server, and how they read the answers."""

from __future__ import annotations

import subprocess

import pytest

from . import adb as adb_module
from .adb import Adb, AdbError, parse_wm_size
from .fakes import FakeDevice


class Runner:
    """A stand-in for ``subprocess.run``: answers come from a list (the last repeats), and every call is recorded."""

    def __init__(self, *answers):
        self.answers = list(answers) or [("", "", 0)]
        self.calls: list[tuple[list[str], dict]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, BaseException):
            raise answer
        stdout, stderr, code = answer
        return subprocess.CompletedProcess(argv, code, stdout, stderr)

    @property
    def args(self) -> list[list[str]]:
        return [argv[1:] for argv, _ in self.calls]


@pytest.fixture
def run(monkeypatch):
    def install(*answers) -> Runner:
        runner = Runner(*answers)
        monkeypatch.setattr(adb_module.subprocess, "run", runner)
        return runner

    return install


@pytest.fixture
def adb():
    return Adb(adb_path="/usr/bin/adb", server_port=15099)


# -- which server -----------------------------------------------------------------------------------------------------


def test_commands_point_at_the_server_of_the_port():
    assert Adb(adb_path="adb", server_port=15099).env()["ANDROID_ADB_SERVER_PORT"] == "15099"


def test_commands_that_only_clean_up_never_start_a_server():
    quiet = Adb(adb_path="adb", server_port=15099).env(autostart=False)
    assert quiet["ADB_SERVER_SOCKET"] == "tcp:127.0.0.1:15099"  # a "remote" server is one adb does not start


def test_the_parents_environment_is_used_and_holds_a_reference_to_the_shared_server(run):
    device = FakeDevice()
    adb = Adb.of(device)
    assert (adb.adb_path, adb.server_port, adb.timeout) == ("adb", 15037, 30.0)
    runner = run(("ok", "", 0))
    adb.shell("S1", "echo ok")
    assert device.adb_env_calls == 1  # the reference is taken by the command that may need the server
    assert runner.calls[0][1]["env"] == {"ANDROID_ADB_SERVER_PORT": "15037"}


def test_cleanup_commands_do_not_take_a_reference_to_the_shared_server(run):
    device = FakeDevice()
    adb = Adb.of(device)
    runner = run()
    adb.forward_remove("S1", 4000)
    adb.kill_server_process("S1", "scid=00001234")
    adb.remove("S1", "/data/local/tmp/x.jar")
    assert device.adb_env_calls == 0
    assert all(kwargs["env"]["ADB_SERVER_SOCKET"] == "tcp:127.0.0.1:15037" for _, kwargs in runner.calls)


# -- running a command ------------------------------------------------------------------------------------------------


def test_a_failed_command_says_what_adb_said(run, adb):
    run(("", "error: device 'S1' not found", 1))
    with pytest.raises(AdbError, match="device 'S1' not found"):
        adb.shell("S1", "true")


def test_a_command_that_hangs_is_an_error_after_its_timeout(run, adb):
    runner = run(subprocess.TimeoutExpired("adb", 3))
    with pytest.raises(AdbError, match="timed out after 7s"):
        adb.run(["devices"], timeout=7)
    assert runner.calls[0][1]["timeout"] == 7


def test_a_missing_adb_is_an_error(run, adb):
    run(FileNotFoundError("adb"))
    with pytest.raises(AdbError, match="could not run adb"):
        adb.run(["devices"])


def test_the_timeout_of_the_device_bounds_a_command_that_names_none(run):
    runner = run()
    Adb(adb_path="adb", timeout=12.0).run(["devices"])
    assert runner.calls[0][1]["timeout"] == 12.0


def test_push_and_move_address_the_device_by_its_serial(run, adb, tmp_path):
    runner = run()
    adb.push("S1", tmp_path / "server.jar", "/data/local/tmp/a.jar")
    adb.move("S1", "/data/local/tmp/a.jar.x", "/data/local/tmp/a.jar")
    assert runner.args == [
        ["-s", "S1", "push", str(tmp_path / "server.jar"), "/data/local/tmp/a.jar"],
        ["-s", "S1", "shell", "mv -f /data/local/tmp/a.jar.x /data/local/tmp/a.jar"],
    ]


def test_a_path_with_a_space_is_quoted_for_the_devices_shell(run, adb):
    runner = run()
    adb.move("S1", "/a b", "/c d")
    adb.device_sha256("S1", "/a b")
    adb.remove("S1", "/a b")
    assert "mv -f '/a b' '/c d'" in runner.args[0][-1]
    assert "sha256sum '/a b'" in runner.args[1][-1]
    assert runner.args[2][-1] == "'/a b'"


def test_the_hash_of_a_file_on_the_device(run, adb):
    sha = "ab" * 32
    run((f"{sha.upper()}  /data/local/tmp/a.jar\n", "", 0))
    assert adb.device_sha256("S1", "/data/local/tmp/a.jar") == sha
    run(("", "", 1))
    assert adb.device_sha256("S1", "/data/local/tmp/a.jar") is None  # no such file
    run(("sha256sum: not found", "", 127))
    assert adb.device_sha256("S1", "/data/local/tmp/a.jar") is None  # no such command


# -- the server's process ---------------------------------------------------------------------------------------------


def test_the_servers_command_line_is_matched_and_not_the_shell_that_looks_for_it(run, adb):
    runner = run()
    adb.kill_server_process("S1", "scid=00001234")
    command = runner.args[0][-1]
    assert command == "pkill -f 'scrcpy[.]Serve[r] .*scid=00001234'"


@pytest.mark.parametrize("marker", ["scid=1234", "scid=0000123G", "x'; reboot; '", "scid=00001234 "])
def test_only_a_server_marker_is_put_in_a_shell_command(run, adb, marker):
    run()
    with pytest.raises(ValueError, match="not a server marker"):
        adb.kill_server_process("S1", marker)
    with pytest.raises(ValueError, match="not a server marker"):
        adb.server_alive("S1", marker)


@pytest.mark.parametrize(
    ("answer", "alive"),
    [
        (("1234\n", "", 0), True),
        (("", "", 1), False),  # pgrep found none
        (("", "", 0), False),  # nothing printed: nothing found
        (("", "adb: no devices", 255), None),  # adb itself failed: not an answer about the server
        (subprocess.TimeoutExpired("adb", 10), None),
    ],
)
def test_whether_the_server_runs_is_true_false_or_unknown(run, adb, answer, alive):
    runner = run(answer)
    assert adb.server_alive("S1", "scid=00001234") is alive
    assert runner.calls[0][1]["env"]["ADB_SERVER_SOCKET"]  # asking never starts an ADB server


# -- the forward ------------------------------------------------------------------------------------------------------

LISTING = "S1 tcp:4001 localabstract:scrcpy_00001234\nS1 tcp:4002 tcp:5555\nS2 tcp:4003 localabstract:scrcpy_00001234\n"


def test_the_forward_is_the_port_adb_printed(run, adb):
    runner = run((LISTING, "", 0), ("4004\n", "", 0))
    assert adb.forward("S1", "scrcpy_00001234") == 4004
    assert runner.args[1] == ["-s", "S1", "forward", "tcp:0", "localabstract:scrcpy_00001234"]


def test_a_silent_forward_is_found_in_the_list(run, adb):
    after = LISTING + "S1 tcp:4010 localabstract:scrcpy_00005678\n"
    run((LISTING, "", 0), ("", "", 0), (after, "", 0))
    assert adb.forward("S1", "scrcpy_00005678") == 4010


def test_a_forward_that_cannot_be_found_is_an_error(run, adb):
    run((LISTING, "", 0), ("", "", 0), (LISTING, "", 0))
    with pytest.raises(AdbError, match="did not say which port"):
        adb.forward("S1", "scrcpy_00005678")


def test_forwards_are_listed_for_one_device(run, adb):
    run((LISTING, "", 0))
    assert adb.forward_list("S1") == [("tcp:4001", "localabstract:scrcpy_00001234"), ("tcp:4002", "tcp:5555")]


def test_only_stale_forwards_to_the_hmis_own_socket_name_are_dropped(run, adb):
    runner = run((LISTING, "", 0), ("", "", 0))
    assert adb.drop_stale_forwards("S1", "scrcpy_00001234") == 1
    assert runner.args[1:] == [["-s", "S1", "forward", "--remove", "tcp:4001"]]  # not S2's, not the adbd forward


def test_a_forward_is_removed_by_its_port(run, adb):
    runner = run()
    adb.forward_remove("S1", 4001, timeout=3)
    assert runner.args == [["-s", "S1", "forward", "--remove", "tcp:4001"]]
    assert runner.calls[0][1]["timeout"] == 3


# -- keys, rotation, properties ---------------------------------------------------------------------------------------


def test_a_key_without_the_server_is_input_keyevent(run, adb):
    runner = run()
    adb.keyevent("S1", 4)
    adb.keyevent("S1", 26, long=True)
    assert [a[-1] for a in runner.args] == ["input keyevent 4", "input keyevent --longpress 26"]


def test_the_rotation_is_read_from_the_settings(run, adb):
    run(("1\n", "", 0), ("3\n", "", 0))
    assert adb.rotation_state("S1") == (True, 3)
    run(("0\n", "", 0), ("null\n", "", 0))
    assert adb.rotation_state("S1") == (False, 0)
    run(("0\n", "", 0), ("9\n", "", 0))
    assert adb.rotation_state("S1") == (False, 0)


def test_the_rotation_is_locked_with_wm_or_the_two_settings_it_sets(run, adb):
    runner = run(("", "", 0))
    adb.lock_rotation("S1", 1)
    assert [a[-1] for a in runner.args] == ["wm user-rotation lock 1"]
    runner = run(("Error: no such command", "", 0), ("", "", 0))
    adb.lock_rotation("S1", 3, display_id=2)
    assert [a[-1] for a in runner.args] == [
        "wm user-rotation -d 2 lock 3",
        "settings put system accelerometer_rotation 0; settings put system user_rotation 3",
    ]
    with pytest.raises(ValueError, match="0 to 3"):
        adb.lock_rotation("S1", 4)


def test_the_rotation_is_given_back_to_the_sensor(run, adb):
    runner = run(("", "", 0))
    adb.free_rotation("S1")
    assert [a[-1] for a in runner.args] == ["wm user-rotation free"]
    runner = run(("", "wm: not found", 1), ("", "", 0))
    adb.free_rotation("S1")
    assert [a[-1] for a in runner.args] == ["wm user-rotation free", "settings put system accelerometer_rotation 1"]


def test_a_property_name_cannot_carry_a_command(run, adb):
    runner = run(("15\n", "", 0))
    assert adb.prop("S1", "ro.build.version.release") == "15"
    assert runner.args[0][-1] == "getprop ro.build.version.release"
    with pytest.raises(ValueError):
        adb.prop("S1", "x; reboot")


def test_the_server_is_started_under_adb_shell_with_its_output_on_one_pipe(monkeypatch):
    seen = {}

    def popen(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return "process"

    monkeypatch.setattr(adb_module.subprocess, "Popen", popen)
    device = FakeDevice()
    assert Adb.of(device).spawn("S1", "app_process /") == "process"
    assert seen["argv"] == ["adb", "-s", "S1", "shell", "app_process /"]
    assert seen["stdin"] == subprocess.DEVNULL and seen["stderr"] == subprocess.STDOUT
    assert seen["env"] == {"ANDROID_ADB_SERVER_PORT": "15037"} and device.adb_env_calls == 1


# -- the display's size -----------------------------------------------------------------------------------------------


def test_the_display_size_is_the_override_when_there_is_one():
    assert parse_wm_size("Physical size: 1080x2340\n") == (1080, 2340)
    assert parse_wm_size("Physical size: 1080x2340\nOverride size: 720x1560\n") == (720, 1560)
    assert parse_wm_size("Error: no such command") is None
