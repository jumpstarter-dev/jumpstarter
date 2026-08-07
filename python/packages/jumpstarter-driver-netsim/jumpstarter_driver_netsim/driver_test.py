import json
from unittest.mock import patch

import pytest
import requests

from .driver import Netsim, NetsimError, _parse_bool

BASE = "http://localhost:7681"

DEVICES_RESPONSE = {
    "devices": [
        {"id": 1, "name": "cvd-1", "chips": [{"kind": "BLUETOOTH"}]},
        {"id": 2, "name": "cvd-2", "chips": []},
    ]
}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def drv():
    return Netsim()


@pytest.fixture
def drv_cli():
    return Netsim(netsim_cli="/usr/lib/cuttlefish-common/bin/netsim")


def test_parse_bool_on():
    for v in ("true", "1", "on", "True", "ON"):
        assert _parse_bool(v, "x") is True


def test_parse_bool_off():
    for v in ("false", "0", "off", "False", "OFF"):
        assert _parse_bool(v, "x") is False


def test_parse_bool_invalid():
    with pytest.raises(NetsimError, match="invalid enabled.*yes"):
        _parse_bool("yes", "enabled")


def test_resolve_device_id_numeric(requests_mock, drv):
    result = drv._resolve_device_id("42")
    assert result == 42
    assert not requests_mock.called


def test_resolve_device_id_exact_name(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    assert drv._resolve_device_id("cvd-1") == 1


def test_resolve_device_id_substring(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    assert drv._resolve_device_id("vd-2") == 2


def test_resolve_device_id_ambiguous(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with pytest.raises(NetsimError, match="ambiguous.*cvd"):
        drv._resolve_device_id("cvd")


def test_resolve_device_id_not_found(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    with pytest.raises(NetsimError, match="device not found.*nope"):
        drv._resolve_device_id("nope")


def test_status_returns_device_count(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    assert drv.status() == "OK (2 devices)"


def test_status_empty(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json={"devices": []})
    assert drv.status() == "OK (0 devices)"


def test_status_connection_error(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", exc=requests.ConnectionError)
    with pytest.raises(NetsimError, match="not connected"):
        drv.status()


def test_status_timeout(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", exc=requests.Timeout)
    with pytest.raises(NetsimError, match="timed out"):
        drv.status()


def test_list_devices(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    result = json.loads(drv.list_devices())
    assert result["devices"][0]["name"] == "cvd-1"


def test_get_device_by_name(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    result = json.loads(drv.get_device("cvd-2"))
    assert result["name"] == "cvd-2"
    assert result["id"] == 2


def test_get_device_by_numeric_id(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    result = json.loads(drv.get_device("1"))
    assert result["name"] == "cvd-1"


def test_get_device_not_found(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json={"devices": []})
    with pytest.raises(NetsimError, match="device not found.*cvd-99"):
        drv.get_device("cvd-99")


def test_patch_device_by_name(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    drv.patch_device("cvd-1", '{"visible": false}')
    req = requests_mock.request_history[-1]
    assert req.json() == {"device": {"visible": False}}


def test_patch_device_by_numeric_id(requests_mock, drv):
    requests_mock.patch(f"{BASE}/v1/devices/5", json={})
    drv.patch_device("5", '{"visible": false}')
    assert requests_mock.request_history[-1].path == "/v1/devices/5"


def test_patch_device_invalid_json(requests_mock, drv):
    with pytest.raises(NetsimError, match="invalid JSON"):
        drv.patch_device("cvd-1", "not json")
    assert not requests_mock.called


def test_set_radio_bt_classic(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    drv.set_radio("cvd-1", "bt_classic", "on")
    req = requests_mock.request_history[-1]
    chips = req.json()["device"]["chips"]
    assert chips[0]["kind"] == "BLUETOOTH"
    assert chips[0]["bt"]["classic"]["state"] is True


def test_set_radio_ble_off(requests_mock, drv):
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    drv.set_radio("1", "ble", "off")
    req = requests_mock.request_history[-1]
    chips = req.json()["device"]["chips"]
    assert chips[0]["bt"]["low_energy"]["state"] is False


def test_set_radio_wifi(requests_mock, drv):
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    drv.set_radio("1", "wifi", "true")
    req = requests_mock.request_history[-1]
    chips = req.json()["device"]["chips"]
    assert chips[0]["kind"] == "WIFI"
    assert chips[0]["wifi"]["state"] is True


def test_set_radio_uwb(requests_mock, drv):
    requests_mock.patch(f"{BASE}/v1/devices/1", json={})
    drv.set_radio("1", "uwb", "1")
    req = requests_mock.request_history[-1]
    chips = req.json()["device"]["chips"]
    assert chips[0]["kind"] == "UWB"
    assert chips[0]["uwb"]["state"] is True


def test_set_radio_invalid_type(requests_mock, drv):
    with pytest.raises(NetsimError, match="unknown radio"):
        drv.set_radio("cvd-1", "zigbee", "on")
    assert not requests_mock.called


def test_set_radio_invalid_state(requests_mock, drv):
    with pytest.raises(NetsimError, match="invalid enabled.*yes"):
        drv.set_radio("cvd-1", "ble", "yes")
    assert not requests_mock.called


def test_reset_devices(requests_mock, drv):
    requests_mock.post(f"{BASE}/v1/devices/reset", text="")
    assert drv.reset_devices() == "OK"


def test_list_captures(requests_mock, drv):
    body = {"captures": [{"id": 1, "state": "ON"}]}
    requests_mock.get(f"{BASE}/v1/captures", json=body)
    result = json.loads(drv.list_captures())
    assert result["captures"][0]["id"] == 1


@pytest.mark.anyio
async def test_get_capture(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/captures/5", content=b"\x00pcap-data")
    import base64

    chunks = []
    async for chunk in drv.get_capture("5"):
        chunks.append(base64.b64decode(chunk))
    assert b"".join(chunks) == b"\x00pcap-data"


@pytest.mark.anyio
async def test_get_capture_large(requests_mock, drv):
    """Captures larger than chunk size are split into multiple chunks."""
    import base64

    large_data = b"\xab" * (3 * 1024 * 1024)  # 3 MiB
    requests_mock.get(f"{BASE}/v1/captures/7", content=large_data)
    chunks = []
    async for chunk in drv.get_capture("7"):
        chunks.append(base64.b64decode(chunk))
    assert b"".join(chunks) == large_data
    assert len(chunks) == 2  # 3 MiB / 2 MiB per chunk = 2 chunks


@pytest.mark.anyio
async def test_get_capture_invalid_id(drv):
    with pytest.raises(NetsimError, match="capture_id must be numeric"):
        async for _ in drv.get_capture("foo/bar"):
            pass


def test_set_capture_no_cli(drv):
    with pytest.raises(NetsimError, match="capture toggle requires netsim_cli"):
        drv.set_capture("1", "on")


def test_set_capture_invalid_state(drv_cli):
    with pytest.raises(NetsimError, match="invalid enabled"):
        drv_cli.set_capture("1", "yes")


@patch("subprocess.run")
def test_set_capture_on(mock_run, drv_cli):
    mock_run.return_value.returncode = 0
    assert drv_cli.set_capture("1", "on") == "OK"
    mock_run.assert_called_once_with(
        ["/usr/lib/cuttlefish-common/bin/netsim", "capture", "patch", "on", "1"],
        capture_output=True,
        text=True,
        timeout=10,
    )


@patch("subprocess.run")
def test_set_capture_off(mock_run, drv_cli):
    mock_run.return_value.returncode = 0
    assert drv_cli.set_capture("1", "off") == "OK"
    mock_run.assert_called_once_with(
        ["/usr/lib/cuttlefish-common/bin/netsim", "capture", "patch", "off", "1"],
        capture_output=True,
        text=True,
        timeout=10,
    )


@patch("subprocess.run")
def test_start_capture(mock_run, requests_mock, drv_cli):
    mock_run.return_value.returncode = 0
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    captures = {
        "captures": [
            {"id": 10, "deviceName": "cvd-1", "chipKind": "BLUETOOTH", "state": "OFF"},
            {"id": 11, "deviceName": "cvd-2", "chipKind": "BLUETOOTH", "state": "OFF"},
        ]
    }
    requests_mock.get(f"{BASE}/v1/captures", json=captures)
    cap_id = drv_cli.start_capture("cvd-1")
    assert cap_id == "10"
    mock_run.assert_called_once_with(
        ["/usr/lib/cuttlefish-common/bin/netsim", "capture", "patch", "on", "10"],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_start_capture_no_cli(requests_mock, drv):
    with pytest.raises(NetsimError, match="capture toggle requires netsim_cli"):
        drv.start_capture("cvd-1")
    assert not requests_mock.called


def test_start_capture_not_found(requests_mock, drv_cli):
    requests_mock.get(f"{BASE}/v1/devices", json=DEVICES_RESPONSE)
    requests_mock.get(f"{BASE}/v1/captures", json={"captures": []})
    with pytest.raises(NetsimError, match="no BLUETOOTH capture found"):
        drv_cli.start_capture("cvd-1")


@patch("subprocess.run")
def test_stop_capture(mock_run, drv_cli):
    mock_run.return_value.returncode = 0
    assert drv_cli.stop_capture("10") == "OK"
    mock_run.assert_called_once_with(
        ["/usr/lib/cuttlefish-common/bin/netsim", "capture", "patch", "off", "10"],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_stop_capture_no_cli(drv):
    with pytest.raises(NetsimError, match="capture toggle requires netsim_cli"):
        drv.stop_capture("10")


@patch("subprocess.run")
def test_capture_toggle_cli_failure(mock_run, drv_cli):
    mock_run.return_value.returncode = 1
    mock_run.return_value.stderr = "netsim: error"
    with pytest.raises(NetsimError, match="netsim capture patch failed"):
        drv_cli.set_capture("1", "on")


@patch("subprocess.run")
def test_capture_toggle_timeout(mock_run, drv_cli):
    import subprocess

    mock_run.side_effect = subprocess.TimeoutExpired(cmd="netsim", timeout=10)
    with pytest.raises(NetsimError, match="timed out"):
        drv_cli.set_capture("1", "on")


@patch("subprocess.run")
def test_capture_toggle_bad_cli(mock_run, drv_cli):
    mock_run.side_effect = FileNotFoundError("No such file")
    with pytest.raises(NetsimError, match="failed to run netsim CLI"):
        drv_cli.set_capture("1", "on")


def test_request_http_error(requests_mock, drv):
    requests_mock.get(f"{BASE}/v1/devices", status_code=500, text="error")
    with pytest.raises(NetsimError, match="failed"):
        drv.list_devices()


def test_custom_host_port():
    drv = Netsim(host="10.0.0.1", port=9090)
    assert drv._base_url == "http://10.0.0.1:9090"
