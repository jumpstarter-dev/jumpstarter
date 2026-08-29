import inspect
import re
import socket
import subprocess
import threading
from contextlib import contextmanager

import pytest
from click.testing import CliRunner

from .client import CuttlefishClient, _echo, _parse
from .driver import Cuttlefish
from jumpstarter.common.utils import serve

BASE = "http://localhost:2080"


@pytest.fixture(autouse=True)
def _mock_adb(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_adb.driver.shutil.which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(
        "jumpstarter_driver_adb.driver.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 0, stdout="", stderr=""),
    )


def _op(requests_mock, method, path, op_name="op-1"):
    getattr(requests_mock, method)(f"{BASE}{path}", json={"name": op_name, "done": False})
    requests_mock.post(f"{BASE}/operations/{op_name}/:wait", json={"name": op_name, "done": True})


# --- _parse ---


def test_parse_dict():
    assert _parse('{"a": 1}') == {"a": 1}


def test_parse_list():
    assert _parse("[1, 2]") == [1, 2]


def test_parse_plain():
    assert _parse("text") == "text"


# --- _echo ---


def test_echo_dict(capsys):
    _echo({"k": "v"})
    assert '"k"' in capsys.readouterr().out


def test_echo_list(capsys):
    _echo([1])
    assert "1" in capsys.readouterr().out


def test_echo_str(capsys):
    _echo("hi")
    assert "hi" in capsys.readouterr().out


# --- client methods via serve() ---


def test_list_cvds(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "d1"}]})
    with serve(Cuttlefish()) as client:
        assert client.list_cvds()["cvds"][0]["name"] == "d1"


def test_get_cvd(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"adb_port": 6520})
    with serve(Cuttlefish()) as client:
        assert client.get_cvd()["adb_port"] == 6520


def test_restart_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:restart")
    with serve(Cuttlefish()) as client:
        assert client.restart_cvd()["done"] is True


def test_powerwash_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerwash")
    with serve(Cuttlefish()) as client:
        assert client.powerwash_cvd()["done"] is True


def test_powerbtn_cvd(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerbtn")
    with serve(Cuttlefish()) as client:
        assert client.powerbtn_cvd()["done"] is True


def test_status(requests_mock):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok")
    with serve(Cuttlefish()) as client:
        assert client.status() == "OK"


def test_list_operations(requests_mock):
    requests_mock.get(f"{BASE}/operations", json={"operations": []})
    with serve(Cuttlefish()) as client:
        assert client.list_operations()["operations"] == []


def test_non_operation_response(requests_mock):
    """Covers _do_operation returning a non-operation result (no 'done' key)."""
    requests_mock.post(f"{BASE}/cvds/cvd/1/:restart", json={"status": "ready"})
    with serve(Cuttlefish()) as client:
        result = client.restart_cvd()
        assert result["status"] == "ready"


# --- CLI ---


def test_cli_list(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "d1"}]})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["list"])
        assert r.exit_code == 0
        assert "d1" in r.output


def test_cli_get(requests_mock):
    requests_mock.get(f"{BASE}/cvds/cvd/1", json={"name": "n"})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["get"])
        assert r.exit_code == 0


def test_cli_restart(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:restart")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["restart"])
        assert r.exit_code == 0


def test_cli_powerwash(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerwash")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["powerwash"])
        assert r.exit_code == 0


def test_cli_powerbtn(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:powerbtn")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["powerbtn"])
        assert r.exit_code == 0


def test_cli_status(requests_mock):
    requests_mock.get(f"{BASE}/_debug/statusz", text="ok")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["status"])
        assert r.exit_code == 0
        assert "OK" in r.output


def test_cli_ops(requests_mock):
    requests_mock.get(f"{BASE}/operations", json={"operations": []})
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["ops"])
        assert r.exit_code == 0


def test_get_host(requests_mock):
    with serve(Cuttlefish()) as client:
        assert client.get_host() == "localhost"


def test_wait_boot(requests_mock):
    with serve(Cuttlefish(boot_timeout=0)) as client:
        assert client.wait_boot(0) == "OK"


def test_cli_wait_boot(requests_mock):
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["wait-boot", "--timeout", "0"])
        assert r.exit_code == 0


def test_cli_power_on(requests_mock):
    requests_mock.get(f"{BASE}/cvds", json={"cvds": [{"name": "1", "group": "cvd", "status": "Running"}]})
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["power", "on"])
        assert r.exit_code == 0


def test_cli_power_off(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:stop")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["power", "off"])
        assert r.exit_code == 0


def test_cli_power_off_destroy(requests_mock):
    _op(requests_mock, "delete", "/cvds/cvd/1")
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["power", "off", "--destroy"])
        assert r.exit_code == 0


def test_cli_power_cycle(requests_mock):
    _op(requests_mock, "post", "/cvds/cvd/1/:stop")
    requests_mock.get(
        f"{BASE}/cvds",
        json={"cvds": [{"name": "1", "group": "cvd", "status": "Stopped"}]},
    )
    _op(requests_mock, "post", "/cvds/cvd/1/:start", op_name="op-2")
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["power", "cycle", "--wait", "0"])
        assert r.exit_code == 0


def test_run_with_progress_error():
    from .client import _run_with_progress

    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        _run_with_progress("Testing", boom)


# --- serve ---


@contextmanager
def _echo_server():
    """Echo server on 127.0.0.1:<ephemeral>, yields the port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen()

    def worker():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                while True:
                    try:
                        data = conn.recv(1024)
                    except OSError:
                        break
                    if not data:
                        break
                    conn.sendall(data)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


def _free_port() -> int:
    """Pick a free local port by binding and releasing an ephemeral socket."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _url_port(url: str) -> int:
    m = re.search(r":(\d+)$", url)
    assert m, f"no port in url: {url}"
    return int(m.group(1))


def test_serve_forwards_ui_child():
    with _echo_server() as p:
        with serve(Cuttlefish(operator_port=p, boot_timeout=0)) as client:
            with client.serve(port=0) as url:
                assert re.fullmatch(r"http://localhost:\d+", url)
                with socket.create_connection(("127.0.0.1", _url_port(url))) as s:
                    s.sendall(b"ping")
                    assert s.recv(4) == b"ping"


def test_serve_default_port_is_6080():
    # JEP-0016 names http://localhost:6080 as the default; pin the method
    # signature without binding the port in CI.
    assert inspect.signature(CuttlefishClient.serve).parameters["port"].default == 6080


def test_serve_explicit_port():
    port = _free_port()
    with serve(Cuttlefish(boot_timeout=0)) as client:
        with client.serve(port=port) as url:
            assert url == f"http://localhost:{port}"


def test_serve_tls_uses_ui_tls_child():
    with _echo_server() as p:
        with serve(Cuttlefish(operator_tls_port=p, boot_timeout=0)) as client:
            with client.serve(port=0, tls=True) as url:
                assert url.startswith("https://")
                with socket.create_connection(("127.0.0.1", _url_port(url))) as s:
                    s.sendall(b"ping")
                    assert s.recv(4) == b"ping"


def test_serve_teardown():
    with serve(Cuttlefish(boot_timeout=0)) as client:
        with client.serve(port=0) as url:
            port = _url_port(url)
        with pytest.raises(ConnectionRefusedError):
            socket.create_connection(("127.0.0.1", port))


def test_serve_missing_ui_child_raises():
    with serve(Cuttlefish(boot_timeout=0)) as client:
        client.children.pop("ui")
        with pytest.raises(RuntimeError, match="does not expose"):
            with client.serve(port=0):
                pass


def test_serve_cli_prints_url(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_cuttlefish.client._wait_forever", lambda: None)
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["serve", "--port", "0"])
        assert r.exit_code == 0
        assert "Serving Cuttlefish UI at http://localhost:" in r.output
        assert "(Ctrl+C to stop)" in r.output


def test_serve_cli_tls_flag(monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_cuttlefish.client._wait_forever", lambda: None)
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["serve", "--port", "0", "--tls"])
        assert r.exit_code == 0
        assert "https://" in r.output


def test_serve_cli_help():
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["serve", "--help"])
        assert r.exit_code == 0
        assert "--port" in r.output
        assert "--tls" in r.output
        assert "6080" in r.output


def test_ui_child_cli_mounted():
    with serve(Cuttlefish()) as client:
        r = CliRunner().invoke(client.cli(), ["ui", "address"])
        assert r.exit_code == 0
        assert ":1080" in r.output


# --- CvdFlasherClient (storage child) ---

ZIP_BYTES = b"PK\x03\x04" + b"fake zip payload"
TGZ_BYTES = b"\x1f\x8b\x08\x00" + b"fake tarball payload"


def _mock_flash_backend(requests_mock, checksum, *, dir_ids=("dir-1",)):
    """Register every HO endpoint the flash flow touches for one checksum."""
    requests_mock.get(f"{BASE}/v1/userartifacts/{checksum}", status_code=404, json={"error": "user artifact not found"})
    requests_mock.put(f"{BASE}/v1/userartifacts/{checksum}", text="")
    requests_mock.post(f"{BASE}/v1/userartifacts/{checksum}/:extract", json={"name": "op-x", "done": False})
    requests_mock.post(f"{BASE}/operations/op-x/:wait", json={})
    requests_mock.post(f"{BASE}/cvd_imgs_dirs", json={"name": "op-d", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d/:wait", [{"json": {"id": d}} for d in dir_ids])
    for d in dir_ids:
        requests_mock.put(f"{BASE}/cvd_imgs_dirs/{d}", json={"name": "op-u", "done": False})
    requests_mock.post(f"{BASE}/operations/op-u/:wait", json={})
    requests_mock.get(f"{BASE}/cvds", json={"cvds": []})
    requests_mock.post(f"{BASE}/cvds", json={"name": "op-c", "done": False})
    requests_mock.post(
        f"{BASE}/operations/op-c/:wait",
        json={"cvds": [{"group": "cvd", "name": "1", "adb_port": 6520}]},
    )


def _checksum(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


def test_flash_local_file_streams_and_recreates(requests_mock, tmp_path):
    """flash() streams the local file to the exporter, uploads it, recreates the CVD."""
    path = tmp_path / "images.zip"
    path.write_bytes(ZIP_BYTES)
    checksum = _checksum(ZIP_BYTES)
    _mock_flash_backend(requests_mock, checksum)
    with serve(Cuttlefish(boot_timeout=0)) as client:
        client.storage.flash(str(path))
    uploads = [
        r for r in requests_mock.request_history if r.method == "PUT" and r.path == f"/v1/userartifacts/{checksum}"
    ]
    assert len(uploads) == 1
    creates = [r for r in requests_mock.request_history if r.method == "POST" and r.path == "/cvds"]
    assert len(creates) == 1
    cfg = creates[0].json()["env_config"]
    assert cfg["instances"][0]["disk"]["default_build"] == "@image_dirs/dir-1"


def test_flash_dict_recreates_once(requests_mock, tmp_path):
    """Flashing multiple artifacts recreates the CVD once, after the last upload."""
    pkg = tmp_path / "cvd-host_package.tar.gz"
    pkg.write_bytes(TGZ_BYTES)
    img = tmp_path / "images.zip"
    img.write_bytes(ZIP_BYTES)
    _mock_flash_backend(requests_mock, _checksum(TGZ_BYTES), dir_ids=("dir-a", "dir-b"))
    _mock_flash_backend(requests_mock, _checksum(ZIP_BYTES), dir_ids=("dir-a", "dir-b"))
    # re-register shared endpoints clobbered by the second call
    requests_mock.post(f"{BASE}/cvd_imgs_dirs", json={"name": "op-d", "done": False})
    requests_mock.post(f"{BASE}/operations/op-d/:wait", [{"json": {"id": "dir-a"}}, {"json": {"id": "dir-b"}}])
    with serve(Cuttlefish(boot_timeout=0)) as client:
        client.storage.flash({"host_package": str(pkg), "default_build": str(img)})
    creates = [r for r in requests_mock.request_history if r.method == "POST" and r.path == "/cvds"]
    assert len(creates) == 1
    cfg = creates[0].json()["env_config"]
    assert cfg["common"]["host_package"] == "@image_dirs/dir-a"
    assert cfg["instances"][0]["disk"]["default_build"] == "@image_dirs/dir-b"


def test_cli_storage_flash(requests_mock, tmp_path):
    path = tmp_path / "images.zip"
    path.write_bytes(ZIP_BYTES)
    _mock_flash_backend(requests_mock, _checksum(ZIP_BYTES))
    with serve(Cuttlefish(boot_timeout=0)) as client:
        r = CliRunner().invoke(client.cli(), ["storage", "flash", str(path)])
        assert r.exit_code == 0, r.output


def test_flash_error_surfaces_to_client(requests_mock, tmp_path):
    path = tmp_path / "images.zip"
    path.write_bytes(ZIP_BYTES)
    checksum = _checksum(ZIP_BYTES)
    _mock_flash_backend(requests_mock, checksum)
    requests_mock.post(
        f"{BASE}/operations/op-c/:wait",
        status_code=500,
        json={"error": "failed to launch cvd", "details": "boom"},
    )

    def _leaf_messages(exc):
        if isinstance(exc, BaseExceptionGroup):
            for sub in exc.exceptions:
                yield from _leaf_messages(sub)
        else:
            yield str(exc)

    with serve(Cuttlefish(boot_timeout=0)) as client:
        with pytest.raises(Exception) as excinfo:
            client.storage.flash(str(path))
        assert any("failed to launch cvd" in m for m in _leaf_messages(excinfo.value)), excinfo.value
