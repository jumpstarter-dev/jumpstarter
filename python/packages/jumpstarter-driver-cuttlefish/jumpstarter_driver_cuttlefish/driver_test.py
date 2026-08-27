import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import requests

from .driver import Cuttlefish, CuttlefishError, CuttlefishTimeout

BASE = "http://localhost:2080"

_ADB_PATCHES = [
    patch("jumpstarter_driver_adb.driver.shutil.which", return_value="/usr/bin/adb"),
    patch("jumpstarter_driver_adb.driver.subprocess.run"),
]


@pytest.fixture
def drv():
    for p in _ADB_PATCHES:
        p.start()
    try:
        yield Cuttlefish(group="cvd_1", name="dev1")
    finally:
        for p in _ADB_PATCHES:
            p.stop()


def test_status_ok(requests_mock, drv):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok")
    assert drv.status() == "OK"


def test_status_connection_error(requests_mock, drv):
    requests_mock.get(f"{BASE}/_debug/statusz", exc=requests.ConnectionError)
    with pytest.raises(CuttlefishError, match="not connected"):
        drv.status()


def test_list_cvds(requests_mock, drv):
    body = {"cvds": [{"name": "dev1", "group": "cvd_1", "status": "Running"}]}
    requests_mock.get(f"{BASE}/cvds", json=body)
    result = json.loads(drv.list_cvds())
    assert result["cvds"][0]["name"] == "dev1"


def test_get_cvd(requests_mock, drv):
    body = {"cvds": [{"name": "dev1", "group": "cvd_1", "adb_port": 6520}]}
    requests_mock.get(f"{BASE}/cvds/cvd_1/dev1", json=body)
    result = json.loads(drv.get_cvd())
    assert result["cvds"][0]["adb_port"] == 6520


def test_get_cvd_http_error(requests_mock, drv):
    requests_mock.get(f"{BASE}/cvds/cvd_1/dev1", status_code=404, json={"error": "not found"})
    with pytest.raises(CuttlefishError, match="failed"):
        drv.get_cvd()


def test_create_cvd_ok(requests_mock, drv):
    """Operation returns done=false, then completes on poll."""
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    requests_mock.post(f"{BASE}/operations/op-1/:wait", json={"name": "op-1", "done": True})
    config = {"env_config": {}}
    result = json.loads(drv.create_cvd(json.dumps(config)))
    assert result["done"] is True
    history = [r for r in requests_mock.request_history if r.path == "/cvds"]
    assert history[0].json() == config


def test_create_cvd_invalid_json(drv):
    with pytest.raises(CuttlefishError, match="invalid JSON"):
        drv.create_cvd("not json {{{")


def test_wait_503_retry(requests_mock, drv):
    """503 should retry, then succeed."""
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    responses = [
        {"status_code": 503, "text": "unavailable"},
        {"status_code": 200, "json": {"name": "op-1", "done": True}},
    ]
    requests_mock.post(f"{BASE}/operations/op-1/:wait", responses)
    result = json.loads(drv.create_cvd("{}"))
    assert result["done"] is True


def test_wait_504_retry(requests_mock, drv):
    """504 should retry, then succeed."""
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    responses = [
        {"status_code": 504, "text": "timeout"},
        {"status_code": 200, "json": {"name": "op-1", "done": True}},
    ]
    requests_mock.post(f"{BASE}/operations/op-1/:wait", responses)
    result = json.loads(drv.create_cvd("{}"))
    assert result["done"] is True


def test_wait_500_error_with_body(requests_mock, drv):
    """500 with JSON error body should raise with message."""
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    requests_mock.post(
        f"{BASE}/operations/op-1/:wait",
        status_code=500,
        json={"error": "disk full", "details": "no space left"},
    )
    with pytest.raises(CuttlefishError, match="disk full"):
        drv.create_cvd("{}")


def test_wait_500_error_plain_text(requests_mock, drv):
    """500 with non-JSON body should still raise."""
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    requests_mock.post(f"{BASE}/operations/op-1/:wait", status_code=500, text="internal error")
    with pytest.raises(CuttlefishError, match="500"):
        drv.create_cvd("{}")


@patch("jumpstarter_driver_cuttlefish.driver.time.sleep")
def test_wait_timeout(mock_sleep, requests_mock, drv):
    """Operation that never completes should raise CuttlefishTimeout."""
    requests_mock.post(f"{BASE}/operations/op-1/:wait", exc=requests.Timeout)
    with pytest.raises(CuttlefishTimeout, match="timed out"):
        drv._wait_for_operation("op-1", timeout=0.1)


def test_wait_connection_lost(requests_mock, drv):
    """Connection drop during polling should raise."""
    requests_mock.post(f"{BASE}/operations/op-1/:wait", exc=requests.ConnectionError)
    with pytest.raises(CuttlefishError, match="lost connection"):
        drv._wait_for_operation("op-1")


def test_wait_unexpected_http_error(requests_mock, drv):
    """Non-500/503/504 error should raise."""
    requests_mock.post(f"{BASE}/operations/op-1/:wait", status_code=403, text="forbidden")
    with pytest.raises(CuttlefishError, match="failed"):
        drv._wait_for_operation("op-1")


def _mock_op(requests_mock, method, path, op_name="op-1"):
    """Register mocks for an operation endpoint and its wait endpoint."""
    getattr(requests_mock, method)(f"{BASE}{path}", json={"name": op_name, "done": False})
    requests_mock.post(f"{BASE}/operations/{op_name}/:wait", json={"name": op_name, "done": True})


def test_start_cvd(requests_mock, drv):
    _mock_op(requests_mock, "post", "/cvds/cvd_1/dev1/:start")
    result = json.loads(drv.start_cvd())
    assert result["done"] is True


def test_stop_cvd(requests_mock, drv):
    _mock_op(requests_mock, "post", "/cvds/cvd_1/dev1/:stop")
    result = json.loads(drv.stop_cvd())
    assert result["done"] is True


def test_restart_cvd(requests_mock, drv):
    _mock_op(requests_mock, "post", "/cvds/cvd_1/dev1/:restart")
    result = json.loads(drv.restart_cvd())
    assert result["done"] is True


def test_delete_cvd(requests_mock, drv):
    _mock_op(requests_mock, "delete", "/cvds/cvd_1/dev1")
    result = json.loads(drv.delete_cvd())
    assert result["done"] is True


def test_powerwash_cvd(requests_mock, drv):
    _mock_op(requests_mock, "post", "/cvds/cvd_1/dev1/:powerwash")
    result = json.loads(drv.powerwash_cvd())
    assert result["done"] is True


def test_powerbtn_cvd(requests_mock, drv):
    _mock_op(requests_mock, "post", "/cvds/cvd_1/dev1/:powerbtn")
    result = json.loads(drv.powerbtn_cvd())
    assert result["done"] is True


def test_list_operations(requests_mock, drv):
    body = {"operations": [{"name": "op-1", "done": False}]}
    requests_mock.get(f"{BASE}/operations", json=body)
    result = json.loads(drv.list_operations())
    assert len(result["operations"]) == 1


def test_get_adb_port_ok(requests_mock, drv):
    body = {"cvds": [{"name": "dev1", "group": "cvd_1", "adb_port": 6520}]}
    requests_mock.get(f"{BASE}/cvds/cvd_1/dev1", json=body)
    assert drv.get_adb_port() == "6520"


def test_get_adb_port_no_cvds(requests_mock, drv):
    requests_mock.get(f"{BASE}/cvds/cvd_1/dev1", json={"cvds": []})
    with pytest.raises(CuttlefishError, match="no ADB port"):
        drv.get_adb_port()


def test_get_adb_port_missing_field(requests_mock, drv):
    requests_mock.get(f"{BASE}/cvds/cvd_1/dev1", json={"cvds": [{"name": "dev1"}]})
    with pytest.raises(CuttlefishError, match="no ADB port"):
        drv.get_adb_port()


def test_request_timeout(requests_mock, drv):
    requests_mock.get(f"{BASE}/_debug/statusz", exc=requests.Timeout)
    with pytest.raises(CuttlefishError, match="timed out"):
        drv.status()


def test_request_non_json_response(requests_mock, drv):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok", headers={"Content-Type": "text/plain"})
    assert drv.status() == "OK"


def test_request_custom_port():
    for p in _ADB_PATCHES:
        p.start()
    try:
        drv = Cuttlefish(host="10.0.0.1", port=9090)
        assert drv._base_url == "http://10.0.0.1:9090"
    finally:
        for p in _ADB_PATCHES:
            p.stop()


def test_scheme_https():
    for p in _ADB_PATCHES:
        p.start()
    try:
        drv = Cuttlefish(scheme="https", host="10.0.0.1", port=443)
        assert drv._base_url == "https://10.0.0.1:443"
    finally:
        for p in _ADB_PATCHES:
            p.stop()


def test_expected_adb_port(drv):
    assert drv._expected_adb_port == 6520


def test_expected_adb_port_instance_2():
    for p in _ADB_PATCHES:
        p.start()
    try:
        drv = Cuttlefish(instance_num=3)
        assert drv._expected_adb_port == 6522
    finally:
        for p in _ADB_PATCHES:
            p.stop()


def test_cvd_device(drv):
    assert drv._cvd_device == "localhost:6520"


def test_get_host(drv):
    assert drv.get_host() == "localhost"


def test_get_existing_cvds_ok(requests_mock, drv):
    body = {"cvds": [{"name": "dev1", "group": "cvd_1"}]}
    requests_mock.get(f"{BASE}/cvds", json=body)
    assert len(drv._get_existing_cvds()) == 1


def test_get_existing_cvds_unreachable(requests_mock, drv):
    requests_mock.get(f"{BASE}/cvds", exc=requests.ConnectionError)
    with pytest.raises(CuttlefishError, match="not connected"):
        drv._get_existing_cvds()


def test_get_existing_cvds_non_dict(requests_mock, drv):
    requests_mock.get(f"{BASE}/cvds", text="not json")
    with pytest.raises(CuttlefishError, match="unexpected response"):
        drv._get_existing_cvds()


def test_auto_connect_adb(drv):
    drv.children["adb"] = MagicMock()
    result = drv._auto_connect_adb()
    assert result == "localhost:6520"
    drv.children["adb"].connect_device.assert_called_once_with("localhost:6520")


def test_auto_connect_adb_failure(drv):
    mock_adb = MagicMock()
    mock_adb.connect_device.side_effect = RuntimeError("fail")
    drv.children["adb"] = mock_adb
    result = drv._auto_connect_adb()
    assert result == "localhost:6520"


def test_auto_connect_adb_no_child(drv):
    drv.children.pop("adb", None)
    result = drv._auto_connect_adb()
    assert result == "localhost:6520"


def test_auto_disconnect_adb(drv):
    drv.children["adb"] = MagicMock()
    drv._auto_disconnect_adb()
    drv.children["adb"].disconnect_device.assert_called_once_with("localhost:6520")


def test_auto_disconnect_adb_failure(drv):
    mock_adb = MagicMock()
    mock_adb.disconnect_device.side_effect = RuntimeError("fail")
    drv.children["adb"] = mock_adb
    drv._auto_disconnect_adb()


def test_auto_disconnect_adb_no_child(drv):
    drv.children.pop("adb", None)
    drv._auto_disconnect_adb()


def test_wait_boot_no_adb_child(drv):
    drv.children.pop("adb", None)
    drv._wait_boot(timeout=1)


def test_wait_boot_wrapper_zero_timeout(drv):
    drv.boot_timeout = 0
    assert drv.wait_boot(timeout=0) == "OK"


@patch("jumpstarter_driver_cuttlefish.driver.subprocess.run")
def test_wait_boot_success(mock_run, drv):
    mock_adb = MagicMock()
    mock_adb.adb_path = "/usr/bin/adb"
    mock_adb.adb_env.return_value = {}
    drv.children["adb"] = mock_adb

    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if "devices" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="localhost:6520\tdevice\n")
        if "getprop" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="1\n")
        return subprocess.CompletedProcess(cmd, 0, stdout="connected\n")

    mock_run.side_effect = fake_run
    drv._wait_boot(timeout=30)


@patch("jumpstarter_driver_cuttlefish.driver.time.sleep")
@patch("jumpstarter_driver_cuttlefish.driver.subprocess.run")
def test_wait_boot_timeout(mock_run, mock_sleep, drv):
    mock_adb = MagicMock()
    mock_adb.adb_path = "/usr/bin/adb"
    mock_adb.adb_env.return_value = {}
    drv.children["adb"] = mock_adb

    mock_run.return_value = subprocess.CompletedProcess([], 0, stdout="")
    with pytest.raises(CuttlefishTimeout, match="did not come online"):
        drv._wait_boot(timeout=0.1)


def test_cvd_power_off_stop(requests_mock, drv):
    power = drv.children["power"]
    requests_mock.post(f"{BASE}/cvds/cvd_1/dev1/:stop", json={"name": "op-1", "done": False})
    requests_mock.post(f"{BASE}/operations/op-1/:wait", json={"name": "op-1", "done": True})
    power.off()


def test_cvd_power_off_destroy(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    power = drv.children["power"]
    requests_mock.delete(f"{BASE}/cvds/cvd_1/dev1", json={"name": "op-1", "done": False})
    requests_mock.post(f"{BASE}/operations/op-1/:wait", json={"name": "op-1", "done": True})
    power.off(destroy=True)
    assert drv._cvd_group is None
    assert drv._cvd_name is None


def test_cvd_power_on_existing_running(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={"cvds": [{"name": "dev1", "group": "cvd_1", "status": "Running"}]},
    )
    power.on()
    assert drv._cvd_group == "cvd_1"
    assert drv._cvd_name == "dev1"


def test_cvd_power_on_existing_stopped(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={"cvds": [{"name": "dev1", "group": "cvd_1", "status": "Stopped"}]},
    )
    requests_mock.post(f"{BASE}/cvds/cvd_1/dev1/:start", json={"name": "op-1", "done": False})
    requests_mock.post(f"{BASE}/operations/op-1/:wait", json={"name": "op-1", "done": True})
    power.on()


def test_cvd_power_on_create_new(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    requests_mock.post(
        f"{BASE}/operations/op-1/:wait",
        json={"name": "op-1", "done": True, "cvds": [{"group": "cvd_1", "name": "dev1", "adb_port": 6520}]},
    )
    power.on()
    assert drv._cvd_group == "cvd_1"
    assert drv._cvd_name == "dev1"


def test_cvd_power_on_stale_cleanup(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={
            "cvds": [
                {"name": "d1", "group": "cvd_1"},
                {"name": "d2", "group": "cvd_1"},
            ]
        },
    )
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d1", json={"name": "op-d1", "done": False})
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d2", json={"name": "op-d2", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d1/:wait", json={"name": "op-d1", "done": True})
    requests_mock.post(f"{BASE}/operations/op-d2/:wait", json={"name": "op-d2", "done": True})
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-c", "done": False})
    requests_mock.post(f"{BASE}/operations/op-c/:wait", json={"name": "op-c", "done": True})
    power.on()


def test_cvd_power_on_stale_cleanup_failure(requests_mock, drv):
    """Failed stale CVD deletion aborts instead of creating duplicate."""
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={
            "cvds": [
                {"name": "d1", "group": "cvd_1"},
                {"name": "d2", "group": "cvd_1"},
            ]
        },
    )
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d1", status_code=500, json={"error": "busy"})
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d2", json={"name": "op-d2", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d2/:wait", json={"name": "op-d2", "done": True})
    with pytest.raises(CuttlefishError, match="failed to delete stale CVDs"):
        power.on()


def test_cvd_power_on_auto_reset_retries_once(requests_mock, drv):
    """auto_reset calls reset_host() and retries after a stale-state failure, then succeeds."""
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    drv.auto_reset = True
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        [
            {"json": {"cvds": [{"name": "d1", "group": "cvd_1"}, {"name": "d2", "group": "cvd_1"}]}},
            {"json": {"cvds": []}},
        ],
    )
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d1", status_code=500, json={"error": "busy"})
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d2", json={"name": "op-d2", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d2/:wait", json={"name": "op-d2", "done": True})
    requests_mock.post(f"{BASE}/reset", json={"name": "op-reset", "done": False})
    requests_mock.post(f"{BASE}/operations/op-reset/:wait", json={"name": "op-reset", "done": True})
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-c", "done": False})
    requests_mock.post(
        f"{BASE}/operations/op-c/:wait",
        json={"name": "op-c", "done": True, "cvds": [{"group": "cvd_1", "name": "dev1", "adb_port": 6520}]},
    )
    power.on()
    assert any(r.method == "POST" and r.path == "/reset" for r in requests_mock.request_history)
    assert drv._cvd_group == "cvd_1"
    assert drv._cvd_name == "dev1"


def test_cvd_power_on_auto_reset_disabled_raises_immediately(requests_mock, drv):
    """Without auto_reset, a stale-state failure raises without touching /reset."""
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={"cvds": [{"name": "d1", "group": "cvd_1"}, {"name": "d2", "group": "cvd_1"}]},
    )
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d1", status_code=500, json={"error": "busy"})
    requests_mock.delete(f"{BASE}/cvds/cvd_1/d2", json={"name": "op-d2", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d2/:wait", json={"name": "op-d2", "done": True})
    with pytest.raises(CuttlefishError, match="failed to delete stale CVDs"):
        power.on()
    assert not any(r.method == "POST" and r.path == "/reset" for r in requests_mock.request_history)


def test_cvd_power_on_port_mismatch(requests_mock, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-1", "done": False})
    requests_mock.post(
        f"{BASE}/operations/op-1/:wait",
        json={"name": "op-1", "done": True, "cvds": [{"group": "cvd_1", "name": "dev1", "adb_port": 9999}]},
    )
    requests_mock.delete(f"{BASE}/cvds/cvd_1/dev1", json={"name": "op-del", "done": False})
    requests_mock.post(f"{BASE}/operations/op-del/:wait", json={"name": "op-del", "done": True})
    with pytest.raises(CuttlefishError, match="adb_port 9999"):
        power.on()
    assert any(r.method == "DELETE" for r in requests_mock.request_history)


def test_cvd_power_read_not_implemented(drv):
    power = drv.children["power"]
    with pytest.raises(NotImplementedError):
        list(power.read())


def test_cvd_flasher_flash_not_implemented(drv):
    flasher = drv.children["storage"]
    with pytest.raises(NotImplementedError):
        flasher.flash("source")


def test_cvd_flasher_dump_not_implemented(drv):
    flasher = drv.children["storage"]
    with pytest.raises(NotImplementedError):
        flasher.dump("target")


def test_cvd_power_on_ignores_other_groups(requests_mock, drv):
    """CVDs from other groups are not touched."""
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    power = drv.children["power"]
    requests_mock.get(
        f"{BASE}/cvds",
        json={
            "cvds": [
                {"name": "d1", "group": "other_group", "status": "Running"},
                {"name": "dev1", "group": "cvd_1", "status": "Running"},
            ]
        },
    )
    power.on()
    assert drv._cvd_group == "cvd_1"
    assert drv._cvd_name == "dev1"
    assert not any(r.method == "DELETE" for r in requests_mock.request_history)
