import os
import tempfile
from unittest.mock import patch

from click.testing import CliRunner

from .client import _echo, _parse
from .driver import Netsim
from jumpstarter.common.utils import serve

CLI_PATH = "/usr/lib/cuttlefish-common/bin/netsim"

BASE = "http://localhost:7681"

DEVICES_RESPONSE = {
    "devices": [
        {"id": 1, "name": "cvd-1", "chips": [{"kind": "BLUETOOTH"}]},
        {"id": 2, "name": "cvd-2", "chips": []},
    ]
}


def test_parse_dict():
    assert _parse('{"a": 1}') == {"a": 1}


def test_parse_list():
    assert _parse("[1]") == [1]


def test_parse_plain():
    assert _parse("text") == "text"


def test_echo_dict(capsys):
    _echo({"k": "v"})
    assert '"k"' in capsys.readouterr().out


def test_echo_list(capsys):
    _echo([1])
    assert "1" in capsys.readouterr().out


def test_echo_str(capsys):
    _echo("hi")
    assert "hi" in capsys.readouterr().out


def test_list_devices(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        assert client.list_devices()["devices"][0]["name"] == "cvd-1"


def test_get_device(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        assert client.get_device("cvd-1")["name"] == "cvd-1"


def test_patch_device(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    with serve(Netsim()) as client:
        client.patch_device("cvd-1", '{"visible": false}')


def test_set_radio(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    with serve(Netsim()) as client:
        client.set_radio("cvd-1", "bt_classic", "on")


def test_reset_devices(requests_mock):
    requests_mock.post(f"{BASE}/v1/devices/reset", text="")
    with serve(Netsim()) as client:
        assert client.reset_devices() == "OK"


def test_list_captures(requests_mock):
    requests_mock.get(f"{BASE}/v1/captures", json={"captures": []})
    with serve(Netsim()) as client:
        assert client.list_captures()["captures"] == []


def test_get_capture(requests_mock):
    requests_mock.get(f"{BASE}/v1/captures/5", content=b"\x00pcap")
    with serve(Netsim()) as client:
        assert client.get_capture("5") == b"\x00pcap"


@patch("subprocess.run")
def test_set_capture(mock_run, requests_mock):
    mock_run.return_value.returncode = 0
    with serve(Netsim(netsim_cli=CLI_PATH)) as client:
        client.set_capture("1", "on")


@patch("subprocess.run")
def test_start_capture(mock_run, requests_mock):
    mock_run.return_value.returncode = 0
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    captures = {
        "captures": [
            {"id": 10, "deviceName": "cvd-1", "chipKind": "BLUETOOTH", "state": "OFF"},
        ]
    }
    requests_mock.get(f"{BASE}/v1/captures", json=captures)
    with serve(Netsim(netsim_cli=CLI_PATH)) as client:
        cap_id = client.start_capture("cvd-1")
        assert cap_id == "10"


@patch("subprocess.run")
def test_stop_capture(mock_run, requests_mock):
    mock_run.return_value.returncode = 0
    with serve(Netsim(netsim_cli=CLI_PATH)) as client:
        assert client.stop_capture("10") == "OK"


def test_status(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        assert client.status() == "OK (2 devices)"


def test_cli_devices(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["devices"])
        assert r.exit_code == 0
        assert "cvd-1" in r.output


def test_cli_device(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["device", "cvd-1"])
        assert r.exit_code == 0
        assert "cvd-1" in r.output


def test_cli_radio(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["radio", "cvd-1", "bt_classic", "on"])
        assert r.exit_code == 0


def test_cli_patch(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["patch", "cvd-1", '{"visible": false}'])
        assert r.exit_code == 0


def test_cli_reset(requests_mock):
    requests_mock.post(f"{BASE}/v1/devices/reset", text="")
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["reset"])
        assert r.exit_code == 0


def test_cli_capture_list(requests_mock):
    requests_mock.get(f"{BASE}/v1/captures", json={"captures": []})
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["capture", "list"])
        assert r.exit_code == 0


@patch("subprocess.run")
def test_cli_capture_start(mock_run, requests_mock):
    mock_run.return_value.returncode = 0
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    captures = {
        "captures": [
            {"id": 10, "deviceName": "cvd-1", "chipKind": "BLUETOOTH", "state": "OFF"},
        ]
    }
    requests_mock.get(f"{BASE}/v1/captures", json=captures)
    with serve(Netsim(netsim_cli=CLI_PATH)) as client:
        r = CliRunner().invoke(client.cli(), ["capture", "start", "cvd-1"])
        assert r.exit_code == 0
        assert "id=10" in r.output


@patch("subprocess.run")
def test_cli_capture_stop(mock_run, requests_mock):
    mock_run.return_value.returncode = 0
    with serve(Netsim(netsim_cli=CLI_PATH)) as client:
        r = CliRunner().invoke(client.cli(), ["capture", "stop", "10"])
        assert r.exit_code == 0


def test_cli_capture_get(requests_mock):
    requests_mock.get(f"{BASE}/v1/captures/5", content=b"\x00pcap-data")
    with tempfile.NamedTemporaryFile(suffix=".pcap", delete=False) as f:
        path = f.name
    try:
        with serve(Netsim()) as client:
            r = CliRunner().invoke(client.cli(), ["capture", "get", "5", "-o", path])
            assert r.exit_code == 0
            assert "10 bytes" in r.output
            with open(path, "rb") as f:
                assert f.read() == b"\x00pcap-data"
    finally:
        os.unlink(path)


def test_cli_status(requests_mock):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with serve(Netsim()) as client:
        r = CliRunner().invoke(client.cli(), ["status"])
        assert r.exit_code == 0
        assert "OK (2 devices)" in r.output
