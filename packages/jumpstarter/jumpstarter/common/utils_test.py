import shutil

from .utils import launch_shell


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
