import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from .driver import Cuttlefish, CuttlefishError, CuttlefishTimeout
from jumpstarter.common.utils import serve

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


def _serve_flasher(artifacts_dir, **kwargs):
    """A served Cuttlefish client whose flasher stages into artifacts_dir."""
    return serve(Cuttlefish(group="cvd_1", name="dev1", artifacts_dir=str(artifacts_dir), **kwargs))


def _flash_error(client, *args, **kwargs) -> str:
    """Flash expecting failure; return every exception message, flattened.

    A driver-side error can surface bare or wrapped in an ExceptionGroup,
    depending on whether the resource-forwarding task was still running when
    the call failed — so tests match on the flattened messages, not the type.
    """

    def messages(exc):
        if isinstance(exc, BaseExceptionGroup):
            return "; ".join(messages(e) for e in exc.exceptions)
        return str(exc)

    try:
        with pytest.raises(BaseException) as ei:
            client.storage.flash(*args, **kwargs)
    finally:
        # A flash that dies mid-transfer leaks the client's rich live
        # progress display, and the next flash in the test run then fails
        # with "Only one live display may be requested at a time".
        from rich import get_console

        get_console().clear_live()
    return messages(ei.value)


def _write_image_zip(path, names=("super.img", "boot.img")):
    with zipfile.ZipFile(path, "w") as z:
        for name in names:
            z.writestr(name, f"contents of {name}")


def _write_host_package(path):
    payload = b"#!/bin/sh\n"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("bin/cvd")
        info.size = len(payload)
        info.mode = 0o755
        t.addfile(info, io.BytesIO(payload))


def _image_zip(tmp_path, names=("super.img", "boot.img")):
    path = tmp_path / "aosp_cf_x86_64_auto-img-1234.zip"
    _write_image_zip(path, names)
    return path


def _host_package(tmp_path):
    path = tmp_path / "cvd-host_package.tar.gz"
    _write_host_package(path)
    return path


def test_cvd_flasher_flash_image(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        client.storage.flash(_image_zip(tmp_path))
    assert (artifacts / "super.img").read_text() == "contents of super.img"
    assert (artifacts / "boot.img").exists()
    # nothing staged is left behind
    assert not list(artifacts.glob(".cvd-flash-*"))


def test_cvd_flasher_flash_host_package(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        client.storage.flash(_host_package(tmp_path), target="host_package")
    extracted = artifacts / "bin" / "cvd"
    assert extracted.read_bytes() == b"#!/bin/sh\n"
    assert extracted.stat().st_mode & 0o100  # owner exec survives the data filter


def test_cvd_flasher_reflash_overwrites(tmp_path, drv):
    """A second flash replaces existing files (fresh inodes, not ETXTBSY)."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "super.img").write_text("stale")
    before = (artifacts / "super.img").stat().st_ino
    with _serve_flasher(artifacts) as client:
        client.storage.flash(_image_zip(tmp_path))
    after = artifacts / "super.img"
    assert after.read_text() == "contents of super.img"
    assert after.stat().st_ino != before


def test_cvd_flasher_target_mismatch(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        assert "was detected as 'image'" in _flash_error(client, _image_zip(tmp_path), target="host_package")


def test_cvd_flasher_unknown_target(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        assert "unknown flash target" in _flash_error(client, _image_zip(tmp_path), target="bootloader")


def test_cvd_flasher_unrecognized_archive(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"\x00\x01\x02\x03garbage")
    with _serve_flasher(artifacts) as client:
        assert "unrecognized artifact" in _flash_error(client, junk)
    assert not list(artifacts.iterdir())


def test_cvd_flasher_artifacts_dir_unset(tmp_path, drv):
    with serve(Cuttlefish(group="cvd_1", name="dev1")) as client:
        assert "artifacts_dir is not configured" in _flash_error(client, _image_zip(tmp_path))


def test_cvd_flasher_artifacts_dir_missing(tmp_path, drv):
    with _serve_flasher(tmp_path / "nonexistent") as client:
        assert "does not exist on the exporter" in _flash_error(client, _image_zip(tmp_path))


BUNDLE_REF = "oci://quay.io/org/aaos-cvd:1234"


def _fake_registry(contents):
    """A stand-in oras Registry whose pull() lays `contents` out in outdir.

    `contents` maps a name inside the bundle to a writer taking the
    destination path — the same shape `oras pull -o <dir>` leaves on disk.
    Returns the class to patch in, plus the list it records instances into,
    so a test can assert on how the registry was constructed and authed.
    """
    created = []

    class FakeRegistry:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.auth = MagicMock()
            self.target = None
            created.append(self)

        def pull(self, *, target, outdir):
            self.target = target
            written = []
            for name, write in contents.items():
                path = Path(outdir) / name
                path.parent.mkdir(parents=True, exist_ok=True)
                write(path)
                written.append(str(path))
            return written

    return FakeRegistry, created


def _bundle(client, contents, source=BUNDLE_REF, **kwargs):
    """Flash an oci:// bundle; returns (flash result, registry instances)."""
    fake, created = _fake_registry(contents)
    with patch("jumpstarter_driver_cuttlefish.driver.Registry", fake):
        return client.storage.flash(source, **kwargs), created


def _bundle_error(client, contents, source=BUNDLE_REF, **kwargs) -> str:
    fake, _ = _fake_registry(contents)
    with patch("jumpstarter_driver_cuttlefish.driver.Registry", fake):
        return _flash_error(client, source, **kwargs)


BOTH = {
    "aosp_cf_x86_64_auto-img-1234.zip": _write_image_zip,
    "cvd-host_package.tar.gz": _write_host_package,
}


def test_cvd_flasher_flash_oci_stages_whole_bundle(tmp_path, drv):
    """One bundle reference is one complete flash: both artifacts staged."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        staged, created = _bundle(client, BOTH)
    assert staged == ["image", "host_package"]
    assert (artifacts / "super.img").read_text() == "contents of super.img"
    assert (artifacts / "bin" / "cvd").read_bytes() == b"#!/bin/sh\n"
    # the oci:// prefix is fls/oras scheme sugar, not part of the reference
    assert created[0].target == "quay.io/org/aaos-cvd:1234"
    # the pulled copy does not outlive the flash
    assert not list(artifacts.glob(".cvd-oci-*"))


def test_cvd_flasher_flash_oci_local_build_naming(tmp_path, drv):
    """A local `cvd fetch` image zip has no build number; a bundle may also
    carry otatools.zip. Magic bytes find both, the name tie-break picks right."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    contents = {
        "otatools.zip": lambda p: _write_image_zip(p, names=("otatools/bin/lpmake",)),
        "aosp_cf_x86_64_auto-img.zip": _write_image_zip,
        "cvd-host_package.tar.gz": _write_host_package,
    }
    with _serve_flasher(artifacts) as client:
        staged, _ = _bundle(client, contents)
    assert staged == ["image", "host_package"]
    assert (artifacts / "super.img").exists()
    assert not (artifacts / "otatools").exists()


def test_cvd_flasher_flash_oci_nested_layout(tmp_path, drv):
    """Layers unpacked into subdirectories are still found."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    contents = {
        "images/aosp_cf_x86_64_auto-img-1234.zip": _write_image_zip,
        "host/cvd-host_package.tar.gz": _write_host_package,
    }
    with _serve_flasher(artifacts) as client:
        staged, _ = _bundle(client, contents)
    assert staged == ["image", "host_package"]
    assert (artifacts / "boot.img").exists()


def test_cvd_flasher_flash_oci_single_target(tmp_path, drv):
    """`-t image:oci://…` stages only the image out of the bundle."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    fake, _ = _fake_registry(BOTH)
    with _serve_flasher(artifacts) as client:
        with patch("jumpstarter_driver_cuttlefish.driver.Registry", fake):
            result = client.storage.flash({"image": BUNDLE_REF})
    assert result == {"image": ["image"]}
    assert (artifacts / "super.img").exists()
    assert not (artifacts / "bin").exists()


def test_cvd_flasher_flash_oci_partial_bundle(tmp_path, drv):
    """Half a bundle stages what it has — a re-flash of one artifact is normal."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        staged, _ = _bundle(client, {"cvd-host_package.tar.gz": _write_host_package})
    assert staged == ["host_package"]
    assert (artifacts / "bin" / "cvd").exists()


def test_cvd_flasher_flash_oci_target_absent_from_bundle(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        message = _bundle_error(client, {"cvd-host_package.tar.gz": _write_host_package}, target="image")
    assert "carries no image artifact" in message
    assert "cvd-host_package.tar.gz" in message


def test_cvd_flasher_flash_oci_empty_bundle(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        message = _bundle_error(client, {"README.md": lambda p: p.write_bytes(b"not an archive")})
    assert "nothing recognizable" in message
    assert not list(artifacts.iterdir())


def test_cvd_flasher_flash_oci_ambiguous_artifacts(tmp_path, drv):
    """Two equally image-looking zips are a refusal, never a coin flip."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    contents = {
        "aosp_cf_x86_64_auto-img-1234.zip": _write_image_zip,
        "aosp_cf_x86_64_phone-img-5678.zip": _write_image_zip,
    }
    with _serve_flasher(artifacts) as client:
        message = _bundle_error(client, contents)
    assert "2 candidate image archives" in message
    assert not list(artifacts.iterdir())


def test_cvd_flasher_flash_oci_credentials(tmp_path, drv, monkeypatch):
    """Credentials reach the registry client, never the reference."""
    monkeypatch.setenv("OCI_USERNAME", "bot")
    monkeypatch.setenv("OCI_PASSWORD", "s3cret")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        _, created = _bundle(client, BOTH)
    created[0].auth.set_basic_auth.assert_called_once_with("bot", "s3cret")
    assert created[0].kwargs == {"insecure": False}


def test_cvd_flasher_flash_oci_insecure(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts, oci_insecure=True) as client:
        _, created = _bundle(client, BOTH)
    assert created[0].kwargs == {"insecure": True}


def test_cvd_flasher_flash_oci_rejects_other_schemes(tmp_path, drv):
    """flash_oci is not a general downloader; http(s) goes through flash."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        with pytest.raises(BaseException) as ei:
            client.storage.call("flash_oci", "https://example.com/img.zip", None)
    assert "must start with oci://" in str(ei.value)


def test_cvd_flasher_flash_oci_unknown_target(tmp_path, drv):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    with _serve_flasher(artifacts) as client:
        assert "unknown flash target" in _bundle_error(client, BOTH, target="bootloader")


def test_cvd_flasher_flash_oci_artifacts_dir_unset(tmp_path, drv):
    with serve(Cuttlefish(group="cvd_1", name="dev1")) as client:
        assert "artifacts_dir is not configured" in _bundle_error(client, BOTH)


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
