import os
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

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


def test_generate_zsh_init_loads_compinit_before_completions():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power"])
    assert "autoload -Uz compinit && compinit" in content
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    eval_jmp_pos = content.index('eval "$(jmp completion zsh')
    assert compinit_pos < eval_jmp_pos


def test_generate_zsh_init_loads_compinit_before_compdef():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power", "qemu"])
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    compdef_pos = content.index("compdef")
    assert compinit_pos < compdef_pos


def test_generate_zsh_init_without_j_commands_loads_compinit():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=None)
    assert "autoload -Uz compinit && compinit" in content
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    eval_jmp_pos = content.index('eval "$(jmp completion zsh')
    assert compinit_pos < eval_jmp_pos


def test_generate_bash_init_with_profiles_sources_bashrc():
    content = _generate_shell_init("bash", use_profiles=True, j_commands=None)
    assert ".bashrc" in content
    assert "j completion bash" in content


def test_generate_zsh_init_without_j_commands():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=None)
    assert "j completion zsh" in content
    assert "compdef" not in content


def test_generate_zsh_init_with_profiles_loads_compinit_after_zshrc():
    content = _generate_shell_init("zsh", use_profiles=True, j_commands=["power"])
    assert ".zshrc" in content
    zshrc_pos = content.index(".zshrc")
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    assert zshrc_pos < compinit_pos


def test_generate_fish_init_with_j_commands():
    content = _generate_shell_init("fish", use_profiles=False, j_commands=["power", "qemu"])
    assert "'power'" in content
    assert "'qemu'" in content
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


def test_launch_fish_passes_context_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    captured_env = {}
    captured_cmd = []

    def mock_run_process(cmd, env, lease=None):
        captured_env.update(env)
        captured_cmd.extend(cmd)
        return 0

    context = "test-context"
    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context=context,
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )

    assert captured_env.get("_JMP_SHELL_CONTEXT") == context
    init_cmd_arg = captured_cmd[captured_cmd.index("--init-command") + 1]
    assert context not in init_cmd_arg


def test_launch_fish_passes_init_file_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    captured_env = {}
    captured_cmd = []

    def mock_run_process(cmd, env, lease=None):
        captured_env.update(env)
        captured_cmd.extend(cmd)
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            j_commands=["power"],
        )

    assert "_JMP_SHELL_INIT" in captured_env
    init_cmd_arg = captured_cmd[captured_cmd.index("--init-command") + 1]
    assert captured_env["_JMP_SHELL_INIT"] not in init_cmd_arg


def test_generate_bash_init_limits_completion_to_first_arg():
    content = _generate_shell_init("bash", use_profiles=False, j_commands=["power", "serial"])
    assert "COMP_CWORD" in content
    assert "-eq 1" in content


def test_launch_shell_zsh_restores_zdotdir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    home_dir = os.path.expanduser("~")

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            zshrc = os.path.join(zdotdir, ".zshrc")
            with open(zshrc) as f:
                first_line = f.readline().strip()
            assert "ZDOTDIR=" in first_line
            assert home_dir in first_line
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            j_commands=["power"],
        )


def test_launch_shell_zsh_uses_tmpdir_without_intermediate_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    temp_dirs = []

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            temp_dirs.append(zdotdir)
            entries = os.listdir(zdotdir)
            assert entries == [".zshrc"], f"Expected only .zshrc in ZDOTDIR, found: {entries}"
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            j_commands=["power"],
        )

    assert len(temp_dirs) == 1
    assert not os.path.exists(temp_dirs[0])


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_init_does_not_produce_compdef_errors():
    init_content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power", "serial"])
    with tempfile.TemporaryDirectory() as tmpdir:
        zshrc_path = os.path.join(tmpdir, ".zshrc")
        with open(zshrc_path, "w") as f:
            f.write(init_content)
        result = subprocess.run(
            ["zsh", "--rcs", "-c", "exit 0"],
            env={"ZDOTDIR": tmpdir, "HOME": tmpdir, "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "command not found: compdef" not in result.stderr
        assert result.returncode == 0


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")
def test_bash_init_produces_no_errors():
    init_content = _generate_shell_init("bash", use_profiles=False, j_commands=["power", "serial"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(init_content)
        rcfile = f.name
    try:
        result = subprocess.run(
            ["bash", "--rcfile", rcfile, "-c", "exit 0"],
            env={"HOME": "/nonexistent", "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "command not found" not in result.stderr
        assert result.returncode == 0
    finally:
        os.unlink(rcfile)


@pytest.mark.skipif(not shutil.which("fish"), reason="fish not installed")
def test_fish_init_produces_no_errors():
    init_content = _generate_shell_init("fish", use_profiles=False, j_commands=["power", "serial"])
    result = subprocess.run(
        ["fish", "--init-command", init_content, "-c", "exit 0"],
        env={"HOME": "/nonexistent", "PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert "command not found" not in result.stderr
    assert result.returncode == 0
