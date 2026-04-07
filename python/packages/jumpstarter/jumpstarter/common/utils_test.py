import shutil

from .utils import _generate_shell_init, launch_shell


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
