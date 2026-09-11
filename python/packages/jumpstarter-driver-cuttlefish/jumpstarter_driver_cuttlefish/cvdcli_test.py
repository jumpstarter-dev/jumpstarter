import json

import pytest

from .cvdcli import cvd_argv, exec_binary, fleet_to_cvds, group_to_cvds, parse_endpoint, stderr_tail

FLEET = {
    "groups": [
        {
            "group_name": "cvd_1",
            "instances": [
                {
                    "instance_name": "1",
                    "status": "Running",
                    "displays": ["720x1280"],
                    "instance_dir": "/var/tmp/cvd/101/1/cuttlefish/instances/cvd-1",
                    "webrtc_device_id": "cvd-1",
                    "adb_serial": "0.0.0.0:6520",
                    "adb_port": 6520,
                }
            ],
        }
    ]
}


def test_cvd_argv_runs_as_user_over_launcher_socket():
    argv = cvd_argv("/shared/launcher.sock", "httpcvd", ["fleet"])
    assert argv == [
        "/shared/jumpstarter-exec", "exec", "--socket", "/shared/launcher.sock", "--",
        "runuser", "-u", "httpcvd", "--", "cvd", "fleet",
    ]
    assert exec_binary("/shared/launcher.sock").as_posix() == "/shared/jumpstarter-exec"


def test_cvd_argv_without_user():
    assert cvd_argv("/shared/launcher.sock", "", ["fleet"])[5:] == ["cvd", "fleet"]


def test_parse_endpoint():
    assert parse_endpoint("http://127.0.0.1:2081") == {"backend": "http", "url": "http://127.0.0.1:2081"}
    assert parse_endpoint("exec://httpcvd@/shared/launcher.sock") == {
        "backend": "exec", "socket": "/shared/launcher.sock", "cvd_user": "httpcvd",
    }
    assert parse_endpoint("exec:///shared/launcher.sock")["cvd_user"] == ""
    for endpoint in ("exec://httpcvd@", "grpc://x", "launcher.sock"):
        with pytest.raises(ValueError):
            parse_endpoint(endpoint)


def test_fleet_to_cvds_matches_host_orchestrator_shape():
    cvds = fleet_to_cvds(json.dumps(FLEET))
    assert cvds == [{
        "group": "cvd_1", "name": "1", "status": "Running", "displays": ["720x1280"],
        "webrtc_device_id": "cvd-1", "adb_serial": "0.0.0.0:6520", "adb_port": 6520,
    }]
    assert fleet_to_cvds(json.dumps({"groups": []})) == []
    assert group_to_cvds(FLEET["groups"][0])[0]["adb_port"] == 6520


@pytest.mark.parametrize("output", ["not json", "[]", json.dumps({"groups": "x"}), json.dumps({"groups": [{}]})])
def test_fleet_to_cvds_rejects_unexpected_documents(output):
    with pytest.raises(ValueError):
        fleet_to_cvds(output)


def test_stderr_tail_drops_version_banner():
    text = "cvd(400)  I 09-09 09:58:35   400   400 main.cc:137] version: 1.57.0 | VCS: abc\nboom\n"
    assert stderr_tail(text) == "boom"
