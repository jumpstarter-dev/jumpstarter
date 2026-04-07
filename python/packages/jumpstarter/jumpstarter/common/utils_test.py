import shutil
from types import SimpleNamespace
from unittest.mock import patch

from .utils import _completion_init_lines, launch_shell
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


def test_completion_init_lines_bash():
    lines = _completion_init_lines("bash", [("j", "_J_COMPLETE"), ("jmp", "_JMP_COMPLETE")])
    assert 'eval "$(_J_COMPLETE=bash_source j)"' in lines
    assert 'eval "$(_JMP_COMPLETE=bash_source jmp)"' in lines


def test_completion_init_lines_zsh():
    lines = _completion_init_lines("zsh", [("j", "_J_COMPLETE")])
    assert 'eval "$(_J_COMPLETE=zsh_source j)"' in lines


def test_completion_init_lines_fish():
    lines = _completion_init_lines("fish", [("j", "_J_COMPLETE")])
    assert "_J_COMPLETE=fish_source j | source" in lines


def test_completion_init_lines_empty():
    assert _completion_init_lines("bash", None) == ""
    assert _completion_init_lines("bash", []) == ""


def test_launch_shell_with_completion_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", shutil.which("bash"))
    captured = {}

    def mock_run_process(cmd, env, lease=None):
        captured["cmd"] = cmd
        rcfile_idx = cmd.index("--rcfile")
        with open(cmd[rcfile_idx + 1]) as f:
            captured["rcfile_content"] = f.read()
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            completion_commands=[("j", "_J_COMPLETE")],
        )

    assert "--noprofile" in captured["cmd"]
    assert "--norc" not in captured["cmd"]
    assert '_J_COMPLETE=bash_source j' in captured["rcfile_content"]


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
