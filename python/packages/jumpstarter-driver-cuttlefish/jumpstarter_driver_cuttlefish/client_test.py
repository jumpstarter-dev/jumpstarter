import subprocess

import pytest
from click.testing import CliRunner

from .client import _echo, _parse
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
