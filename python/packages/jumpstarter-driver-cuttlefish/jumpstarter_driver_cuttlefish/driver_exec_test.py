"""Exec backend: cvd over jumpstarter-exec instead of Host Orchestrator HTTP."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .cvdcli import cvd_argv
from .driver import Cuttlefish, CuttlefishError, CuttlefishTimeout, CvdCliBackend
from .driver_test import _ADB_PATCHES

GROUP = {
    "group_name": "cvd_1",
    "instances": [{"instance_name": "1", "status": "Running", "adb_port": 6520, "displays": []}],
}
BANNER = "cvd(400)  I 09-09 09:58:35   400   400 main.cc:137] version: 1.57.0 | VCS: abc\n"


class FakeCvd:
    """Records cvd argv and answers fleet/load like the real CLI."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.groups: list[dict] = []
        self.failures: dict[str, str] = {}

    def __call__(self, argv, **kwargs):
        if "cvd" not in argv:  # adb start-server and friends
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        cvd_args = argv[argv.index("cvd") + 1:]
        self.calls.append(cvd_args)
        subcommand = next(arg for arg in cvd_args if not arg.startswith("--"))
        if subcommand in self.failures:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr=BANNER + self.failures[subcommand])
        if subcommand == "fleet":
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"groups": self.groups}), stderr=BANNER)
        if subcommand == "load":
            self.groups.append(GROUP)
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(GROUP), stderr=BANNER)
        if subcommand == "remove":
            self.groups.clear()
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr=BANNER)


@pytest.fixture
def cvd(drv):
    # Applied after ``drv`` so it wins over the global subprocess.run patch in _ADB_PATCHES.
    fake = FakeCvd()
    with patch("jumpstarter_driver_cuttlefish.driver.subprocess.run", side_effect=fake):
        yield fake


@pytest.fixture
def drv(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    for p in _ADB_PATCHES:
        p.start()
    try:
        yield Cuttlefish(launcher_socket=str(shared / "launcher.sock"), cvd_user="httpcvd", group="cvd_1", name="1")
    finally:
        for p in _ADB_PATCHES:
            p.stop()


def test_selects_exec_backend(drv):
    assert isinstance(drv._backend, CvdCliBackend)
    assert drv._backend.work_dir == Path(drv.launcher_socket).parent


def test_status_and_list_use_fleet(cvd, drv):
    assert drv.status() == "OK"
    cvd.groups.append(GROUP)
    result = json.loads(drv.list_cvds())
    assert result["cvds"] == [{
        "group": "cvd_1", "name": "1", "status": "Running", "displays": [],
        "webrtc_device_id": None, "adb_serial": None, "adb_port": 6520,
    }]
    assert cvd.calls == [["fleet"], ["fleet"]]


def test_runs_cvd_as_user_through_launcher(cvd, drv):
    with patch("jumpstarter_driver_cuttlefish.driver.subprocess.run", side_effect=cvd) as run:
        drv.status()
    assert run.call_args.args[0] == cvd_argv(drv.launcher_socket, "httpcvd", ["fleet"])


def test_get_cvd_and_adb_port_filter_fleet(cvd, drv):
    cvd.groups.append(GROUP)
    cvd.groups.append({"group_name": "other", "instances": [{"instance_name": "1", "adb_port": 6521}]})
    assert json.loads(drv.get_cvd())["cvds"] == [json.loads(drv.list_cvds())["cvds"][0]]
    assert drv.get_adb_port() == "6520"


def test_create_writes_env_config_and_loads_it(cvd, drv):
    env_config = {"instances": [{"vm": {"cpus": 2}}]}
    result = json.loads(drv.create_cvd(json.dumps({"env_config": env_config})))
    config_path = Path(drv.launcher_socket).parent / "env_config.json"
    assert json.loads(config_path.read_text()) == env_config
    assert config_path.stat().st_mode & 0o777 == 0o644
    assert cvd.calls == [["load", str(config_path)]]
    assert result["done"] is True
    assert result["cvds"][0] == {
        "group": "cvd_1", "name": "1", "status": "Running", "displays": [],
        "webrtc_device_id": None, "adb_serial": None, "adb_port": 6520,
    }


def test_create_requires_env_config(cvd, drv):
    with pytest.raises(CuttlefishError, match="env_config"):
        drv.create_cvd(json.dumps({"cvd": {}}))
    assert cvd.calls == []


@pytest.mark.parametrize("operation,expected", [
    ("start_cvd", ["--group_name=cvd_1", "--instance_name=1", "start", "--report_anonymous_usage_stats=n"]),
    ("stop_cvd", ["--group_name=cvd_1", "--instance_name=1", "stop"]),
    ("restart_cvd", ["--group_name=cvd_1", "--instance_name=1", "restart"]),
    ("powerwash_cvd", ["--group_name=cvd_1", "--instance_name=1", "powerwash"]),
    ("powerbtn_cvd", ["--group_name=cvd_1", "--instance_name=1", "powerbtn"]),
    ("delete_cvd", ["--group_name=cvd_1", "remove"]),
    ("reset_host", ["reset", "-y"]),
])
def test_lifecycle_maps_to_cvd_subcommands(cvd, drv, operation, expected):
    assert json.loads(getattr(drv, operation)()) == {"done": True}
    assert cvd.calls[-1] == expected


def test_failure_reports_exit_code_and_stderr_without_banner(cvd, drv):
    cvd.failures["stop"] = "No such instance\n"
    with pytest.raises(CuttlefishError, match=r"exit code 2: No such instance$"):
        drv.stop_cvd()


def test_timeout_and_missing_launcher(drv):
    with patch("jumpstarter_driver_cuttlefish.driver.subprocess.run",
               side_effect=subprocess.TimeoutExpired("cvd", 5)):
        with pytest.raises(CuttlefishTimeout, match="timed out"):
            drv.start_cvd()
    with patch("jumpstarter_driver_cuttlefish.driver.subprocess.run", side_effect=FileNotFoundError("missing")):
        with pytest.raises(CuttlefishError, match="cannot run jumpstarter-exec"):
            drv.status()


def test_invalid_fleet_output(drv):
    with patch("jumpstarter_driver_cuttlefish.driver.subprocess.run",
               return_value=subprocess.CompletedProcess([], 0, stdout="garbage", stderr="")):
        with pytest.raises(CuttlefishError, match="invalid JSON"):
            drv.list_cvds()


def test_list_operations_unsupported(drv):
    with pytest.raises(CuttlefishError, match="synchronously"):
        drv.list_operations()


def test_power_on_creates_then_tracks_group(cvd, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    drv.children["power"].on()
    assert [call[-1] if call[0] != "load" else "load" for call in cvd.calls] == ["fleet", "load"]
    assert (drv._cvd_group, drv._cvd_name) == ("cvd_1", "1")
    drv.children["power"].off(destroy=True)
    assert cvd.calls[-1] == ["--group_name=cvd_1", "remove"]
    assert cvd.groups == []


def test_power_on_starts_stopped_instance(cvd, drv):
    drv.children["adb"] = MagicMock()
    drv.boot_timeout = 0
    cvd.groups.append({"group_name": "cvd_1", "instances": [{"instance_name": "1", "status": "Stopped"}]})
    drv.children["power"].on()
    assert cvd.calls[-1][2] == "start"


def test_managed_exec_records_backend_in_health(cvd, drv, tmp_path):
    from .health import initialize

    runtime_id = tmp_path / "runtime-id"
    runtime_id.write_text("runtime-1")
    state_path = tmp_path / "health.json"
    endpoint = f"exec://httpcvd@{drv.launcher_socket}"
    initialize(str(state_path), str(runtime_id), endpoint)
    managed = Cuttlefish(
        launcher_socket=drv.launcher_socket, cvd_user="httpcvd", managed=True,
        runtime_id_path=str(runtime_id), health_state_path=str(state_path),
        env_config={"instances": [{}]}, health_ports=[7681],
    )
    state = json.loads(state_path.read_text())
    assert state["backend"] == "exec"
    assert state["socket"] == drv.launcher_socket
    assert state["cvd_user"] == "httpcvd"
    assert "url" not in state
    managed.create_cvd(json.dumps({"env_config": managed.env_config}))
    state = json.loads(state_path.read_text())
    assert (state["state"], state["group"], state["name"]) == ("running", "cvd_1", "1")
    with pytest.raises(CuttlefishError, match="already has a CVD"):
        managed.create_cvd(json.dumps({"env_config": managed.env_config}))
    managed.close()
