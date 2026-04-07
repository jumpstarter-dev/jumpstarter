import os
import shutil
from unittest.mock import patch

from .utils import _generate_shell_init, _validate_j_commands, launch_shell


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


def test_generate_bash_init_with_j_commands():
    content = _generate_shell_init("bash", use_profiles=False, j_commands=["power", "serial", "ssh"])
    assert "_j_completion" in content
    assert "power serial ssh" in content
    assert "jmp completion bash" in content
    assert "jmp-admin completion bash" in content
    assert ".bashrc" not in content


def test_generate_bash_init_with_profiles():
    content = _generate_shell_init("bash", use_profiles=True, j_commands=["power"])
    assert ".bashrc" in content
    assert "_j_completion" in content


def test_generate_bash_init_without_j_commands():
    content = _generate_shell_init("bash", use_profiles=False, j_commands=None)
    assert "j completion bash" in content
    assert "_j_completion" not in content


def test_generate_zsh_init_with_j_commands():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power", "qemu"])
    assert "power qemu" in content
    assert "jmp completion zsh" in content
    assert "compdef" in content


def test_generate_bash_init_with_profiles_sources_bashrc():
    content = _generate_shell_init("bash", use_profiles=True, j_commands=None)
    assert ".bashrc" in content
    assert "j completion bash" in content


def test_generate_zsh_init_without_j_commands():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=None)
    assert "j completion zsh" in content
    assert "compdef" not in content


def test_generate_zsh_init_with_profiles_sources_zshrc():
    content = _generate_shell_init("zsh", use_profiles=True, j_commands=["power"])
    assert ".zshrc" in content


def test_generate_fish_init_with_j_commands():
    content = _generate_shell_init("fish", use_profiles=False, j_commands=["power", "qemu"])
    assert "power" in content
    assert "qemu" in content
    assert "jmp completion fish" in content


def test_generate_fish_init_without_j_commands():
    content = _generate_shell_init("fish", use_profiles=False, j_commands=None)
    assert "j completion fish" in content


def test_generate_shell_init_unknown_shell():
    content = _generate_shell_init("csh", use_profiles=False, j_commands=["power"])
    assert content == ""


def test_launch_shell_with_j_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", shutil.which("true"))
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        j_commands=["power", "serial"],
    )
    assert exit_code == 0


def test_validate_j_commands_filters_unsafe_names():
    assert _validate_j_commands(None) is None
    assert _validate_j_commands(["power", "serial"]) == ["power", "serial"]
    assert _validate_j_commands(["good-cmd", "good_cmd"]) == ["good-cmd", "good_cmd"]
    assert _validate_j_commands(["$(evil)", "power"]) == ["power"]
    assert _validate_j_commands(["bad;cmd", "ok"]) == ["ok"]
    assert _validate_j_commands(["bad cmd", "ok"]) == ["ok"]
    assert _validate_j_commands(['"injection', "ok"]) == ["ok"]


def test_generate_shell_init_excludes_unsafe_j_commands():
    content = _generate_shell_init("bash", use_profiles=False, j_commands=["power", "$(evil)", "serial"])
    assert "power" in content
    assert "serial" in content
    assert "$(evil)" not in content


def test_launch_shell_zsh_cleans_up_all_temp_files(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    zshrc_paths = []

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            zshrc = os.path.join(zdotdir, ".zshrc")
            zshrc_paths.append(zshrc)
            assert os.path.exists(zshrc)
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            j_commands=["power", "serial"],
        )
        assert exit_code == 0

    assert len(zshrc_paths) == 1
    assert not os.path.exists(zshrc_paths[0])
