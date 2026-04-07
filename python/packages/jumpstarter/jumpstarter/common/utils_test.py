import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from .utils import _build_common_env, _lease_env_vars, launch_shell
from jumpstarter.utils.env import ExporterMetadata


def test_launch_shell(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", shutil.which("true"))
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False
    )
    assert exit_code == 0

    monkeypatch.setenv("SHELL", shutil.which("false"))
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote", allow=["*"],
        unsafe=False,
        use_profiles=False
    )
    assert exit_code == 1


def test_launch_shell_sets_lease_env(tmp_path, monkeypatch):
    env_output = tmp_path / "env_output.txt"
    script = tmp_path / "capture_env.sh"
    script.write_text(
        f"#!/bin/sh\n"
        f'echo "JMP_EXPORTER=$JMP_EXPORTER" >> {env_output}\n'
        f'echo "JMP_LEASE=$JMP_LEASE" >> {env_output}\n'
        f'echo "JMP_EXPORTER_LABELS=$JMP_EXPORTER_LABELS" >> {env_output}\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("SHELL", str(script))
    lease = SimpleNamespace(
        exporter_name="my-exporter",
        name="lease-123",
        exporter_labels={"board": "rpi4", "location": "lab-1"},
        lease_ending_callback=None,
    )
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="my-exporter",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        lease=lease,
    )
    assert exit_code == 0
    output = env_output.read_text()
    assert "JMP_EXPORTER=my-exporter" in output
    assert "JMP_LEASE=lease-123" in output
    assert "board=rpi4" in output
    assert "location=lab-1" in output


def test_exporter_metadata_from_env(monkeypatch):
    monkeypatch.setenv("JMP_EXPORTER", "my-board")
    monkeypatch.setenv("JMP_LEASE", "lease-abc")
    monkeypatch.setenv("JMP_EXPORTER_LABELS", "board=rpi4,location=lab-1,team=qa")

    meta = ExporterMetadata.from_env()
    assert meta.name == "my-board"
    assert meta.lease == "lease-abc"
    assert meta.labels == {"board": "rpi4", "location": "lab-1", "team": "qa"}


def test_exporter_metadata_from_env_empty(monkeypatch):
    monkeypatch.delenv("JMP_EXPORTER", raising=False)
    monkeypatch.delenv("JMP_LEASE", raising=False)
    monkeypatch.delenv("JMP_EXPORTER_LABELS", raising=False)

    meta = ExporterMetadata.from_env()
    assert meta.name == ""
    assert meta.lease is None
    assert meta.labels == {}


def test_exporter_metadata_from_env_labels_with_equals_in_value(monkeypatch):
    monkeypatch.setenv("JMP_EXPORTER", "board")
    monkeypatch.setenv("JMP_EXPORTER_LABELS", "key=val=123,other=ok")

    meta = ExporterMetadata.from_env()
    assert meta.labels == {"key": "val=123", "other": "ok"}


def test_exporter_metadata_from_env_ignores_empty_key(monkeypatch):
    monkeypatch.setenv("JMP_EXPORTER", "board")
    monkeypatch.setenv("JMP_EXPORTER_LABELS", "=value,valid=ok")

    meta = ExporterMetadata.from_env()
    assert meta.labels == {"valid": "ok"}


def test_build_common_env_minimal():
    env = _build_common_env("host.sock", ["driver1"], unsafe=False)
    assert env["JUMPSTARTER_HOST"] == "host.sock"
    assert env["JMP_DRIVERS_ALLOW"] == "driver1"
    assert env["_JMP_SUPPRESS_DRIVER_WARNINGS"] == "1"
    assert "JMP_GRPC_INSECURE" not in env
    assert "JMP_GRPC_PASSPHRASE" not in env


def test_build_common_env_unsafe():
    env = _build_common_env("host.sock", ["driver1"], unsafe=True)
    assert env["JMP_DRIVERS_ALLOW"] == "UNSAFE"


def test_build_common_env_insecure():
    env = _build_common_env("host.sock", ["*"], unsafe=False, insecure=True)
    assert env["JMP_GRPC_INSECURE"] == "1"


def test_build_common_env_passphrase():
    env = _build_common_env("host.sock", ["*"], unsafe=False, passphrase="secret")
    assert env["JMP_GRPC_PASSPHRASE"] == "secret"


def test_build_common_env_empty_passphrase():
    env = _build_common_env("host.sock", ["*"], unsafe=False, passphrase="")
    assert "JMP_GRPC_PASSPHRASE" not in env


def test_build_common_env_with_lease():
    lease = SimpleNamespace(
        exporter_name="exp1",
        name="lease-1",
        exporter_labels={"k": "v"},
    )
    env = _build_common_env("host.sock", ["*"], unsafe=False, lease=lease)
    assert env["JMP_EXPORTER"] == "exp1"
    assert env["JMP_LEASE"] == "lease-1"
    assert env["JMP_EXPORTER_LABELS"] == "k=v"


def test_lease_env_vars_basic():
    lease = SimpleNamespace(
        exporter_name="exp",
        name="lease-x",
        exporter_labels={"a": "1", "b": "2"},
    )
    env = _lease_env_vars(lease)
    assert env["JMP_EXPORTER"] == "exp"
    assert env["JMP_LEASE"] == "lease-x"
    assert env["JMP_EXPORTER_LABELS"] == "a=1,b=2"


def test_lease_env_vars_no_name_no_labels():
    lease = SimpleNamespace(
        exporter_name="exp",
        name=None,
        exporter_labels={},
    )
    env = _lease_env_vars(lease)
    assert env["JMP_EXPORTER"] == "exp"
    assert "JMP_LEASE" not in env
    assert "JMP_EXPORTER_LABELS" not in env


@pytest.mark.anyio
async def test_fetch_exporter_labels_success():
    from jumpstarter.client.lease import Lease

    lease = object.__new__(Lease)
    lease.exporter_name = "test-exporter"
    lease.exporter_labels = {}

    mock_exporter = MagicMock()
    mock_exporter.labels = {"board": "rpi4", "env": "test"}
    lease.svc = MagicMock()
    lease.svc.GetExporter = AsyncMock(return_value=mock_exporter)

    await lease._fetch_exporter_labels()

    lease.svc.GetExporter.assert_called_once_with(name="test-exporter")
    assert lease.exporter_labels == {"board": "rpi4", "env": "test"}


@pytest.mark.anyio
async def test_fetch_exporter_labels_failure():
    from jumpstarter.client.lease import Lease

    lease = object.__new__(Lease)
    lease.exporter_name = "test-exporter"
    lease.exporter_labels = {"stale": "data"}

    lease.svc = MagicMock()
    lease.svc.GetExporter = AsyncMock(side_effect=Exception("connection refused"))

    await lease._fetch_exporter_labels()

    assert lease.exporter_labels == {}
