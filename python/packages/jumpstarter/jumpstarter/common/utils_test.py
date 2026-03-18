import shutil
from types import SimpleNamespace

from .utils import launch_shell
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
    monkeypatch.setenv("SHELL", shutil.which("env"))
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
