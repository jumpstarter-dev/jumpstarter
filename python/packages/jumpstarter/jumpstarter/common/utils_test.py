import os
import shutil
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from .utils import (
    ANSI_GRAY,
    ANSI_RESET,
    ANSI_WHITE,
    ANSI_YELLOW,
    PROMPT_CWD,
    _generate_shell_init,
    _validate_j_commands,
    launch_shell,
)


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


def test_generate_shell_init_uses_absolute_paths_for_completion(monkeypatch):
    def fake_which(name):
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", fake_which)

    content = _generate_shell_init("zsh", use_profiles=True, j_commands=None)
    for line in content.splitlines():
        if "completion zsh" in line and "eval" in line:
            dollar_paren = line.split("$(")[1].split(")")[0]
            cmd = dollar_paren.split()[0]
            assert cmd.startswith("/"), f"Expected absolute path for command, got: {cmd}"


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
    assert "jmp completion zsh" in content
    assert "compdef" in content
    assert "1:subcommand:(power qemu)" in content


def test_generate_zsh_init_loads_compinit_before_completions():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power"])
    assert "autoload -Uz compinit && compinit" in content
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    eval_jmp_pos = content.index("completion zsh")
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
    eval_jmp_pos = content.index("completion zsh")
    assert compinit_pos < eval_jmp_pos


def test_generate_bash_init_with_profiles_sources_bashrc():
    content = _generate_shell_init("bash", use_profiles=True, j_commands=None)
    assert ".bashrc" in content
    assert "j completion bash" in content


def test_generate_zsh_init_without_j_commands():
    content = _generate_shell_init("zsh", use_profiles=False, j_commands=None)
    assert "j completion zsh" in content
    assert "compdef" not in content


def test_generate_zsh_init_with_profiles_loads_zshrc_before_compinit():
    content = _generate_shell_init("zsh", use_profiles=True, j_commands=["power"])
    assert ".zshrc" in content
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    zshrc_pos = content.index(".zshrc")
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


def test_launch_shell_zsh_uses_tmpdir_with_zshrc_and_zshenv(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    temp_dirs = []

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            temp_dirs.append(zdotdir)
            entries = sorted(os.listdir(zdotdir))
            assert entries == [".zshenv", ".zshrc"], f"Expected .zshenv and .zshrc in ZDOTDIR, found: {entries}"
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


def test_launch_shell_zsh_sources_original_zshenv(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    home_dir = os.path.expanduser("~")
    original_zshenv = os.path.join(home_dir, ".zshenv")

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            zshenv_path = os.path.join(zdotdir, ".zshenv")
            assert os.path.exists(zshenv_path), ".zshenv must exist in temp ZDOTDIR"
            with open(zshenv_path) as f:
                content = f.read()
            assert original_zshenv in content, (
                f".zshenv must source original {original_zshenv}"
            )
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


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_init_does_not_produce_compdef_errors():
    init_content = _generate_shell_init("zsh", use_profiles=False, j_commands=["power", "serial"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".zsh", delete=False) as f:
        f.write(init_content)
        init_file = f.name
    try:
        result = subprocess.run(
            ["zsh", "-c", f"source {init_file}; exit 0"],
            env={"HOME": "/nonexistent", "PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert "command not found: compdef" not in result.stderr
        assert result.returncode == 0
    finally:
        os.unlink(init_file)


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")
def test_bash_init_produces_no_errors():
    init_content = _generate_shell_init("bash", use_profiles=False, j_commands=["power", "serial"])
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(init_content)
        rcfile = f.name
    try:
        result = subprocess.run(
            ["bash", "-c", f"source {rcfile}; exit 0"],
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


def test_launch_zsh_sets_prompt_after_profile_in_init(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    captured_zshrc = []

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            zshrc = os.path.join(zdotdir, ".zshrc")
            with open(zshrc) as f:
                captured_zshrc.append(f.read())
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="test-device",
            allow=["*"],
            unsafe=False,
            use_profiles=True,
            j_commands=["power"],
        )

    assert len(captured_zshrc) == 1
    content = captured_zshrc[0]
    assert "PROMPT=" in content
    zshrc_pos = content.index(".zshrc")
    prompt_pos = content.index("PROMPT=")
    assert prompt_pos > zshrc_pos


def test_launch_zsh_passes_context_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    captured_env = {}

    def mock_run_process(cmd, env, lease=None):
        captured_env.update(env)
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="test-device",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )

    assert captured_env.get("_JMP_SHELL_CONTEXT") == "test-device"


def test_launch_zsh_prompt_references_env_var_not_literal_context(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    captured_zshrc = []

    def mock_run_process(cmd, env, lease=None):
        zdotdir = env.get("ZDOTDIR")
        if zdotdir:
            zshrc = os.path.join(zdotdir, ".zshrc")
            with open(zshrc) as f:
                captured_zshrc.append(f.read())
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="test-device-name",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            j_commands=["power"],
        )

    content = captured_zshrc[0]
    prompt_line = [line for line in content.split("\n") if "PROMPT=" in line][0]
    assert "${_JMP_SHELL_CONTEXT}" in prompt_line
    assert "test-device-name" not in prompt_line


def test_launch_bash_sets_prompt_after_profile_in_init(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/bash")
    captured_content = []

    def mock_run_process(cmd, env, lease=None):
        if "--rcfile" in cmd:
            rcfile = cmd[cmd.index("--rcfile") + 1]
            with open(rcfile) as f:
                captured_content.append(f.read())
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="test-device",
            allow=["*"],
            unsafe=False,
            use_profiles=True,
            j_commands=["power"],
        )

    assert len(captured_content) == 1
    content = captured_content[0]
    assert "PS1=" in content
    bashrc_pos = content.index(".bashrc")
    ps1_pos = content.index("PS1=")
    assert ps1_pos > bashrc_pos


def test_launch_bash_passes_context_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", "/usr/bin/bash")
    captured_env = {}

    def mock_run_process(cmd, env, lease=None):
        captured_env.update(env)
        return 0

    with patch("jumpstarter.common.utils._run_process", mock_run_process):
        launch_shell(
            host=str(tmp_path / "test.sock"),
            context="test-device",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )

    assert captured_env.get("_JMP_SHELL_CONTEXT") == "test-device"


@pytest.mark.skipif(not shutil.which("zsh"), reason="zsh not installed")
def test_zsh_prompt_survives_user_profile_override():
    home_dir = tempfile.mkdtemp()
    try:
        with open(os.path.join(home_dir, ".zshrc"), "w") as f:
            f.write('PROMPT="user-prompt> "\n')

        init_content = _generate_shell_init("zsh", use_profiles=True, j_commands=["power"])
        init_content += (
            'PROMPT="%F{8}%1~ %F{yellow}⚡%F{white}'
            '${_JMP_SHELL_CONTEXT} %F{yellow}➤%f "\n'
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".zsh", delete=False) as f:
            f.write(init_content)
            init_file = f.name

        try:
            result = subprocess.run(
                ["zsh", "-c", f"source {init_file}; echo \"$PROMPT\""],
                env={
                    "HOME": home_dir,
                    "PATH": os.environ.get("PATH", ""),
                    "_JMP_SHELL_CONTEXT": "test-device",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"zsh failed: {result.stderr}"
            assert "user-prompt" not in result.stdout
            assert "test-device" in result.stdout
        finally:
            os.unlink(init_file)
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not installed")
def test_bash_prompt_survives_user_profile_override():
    home_dir = tempfile.mkdtemp()
    try:
        with open(os.path.join(home_dir, ".bashrc"), "w") as f:
            f.write('PS1="user-prompt> "\n')

        init_content = _generate_shell_init("bash", use_profiles=True, j_commands=["power"])
        init_content += (
            f'PS1="{ANSI_GRAY}{PROMPT_CWD} {ANSI_YELLOW}⚡{ANSI_WHITE}'
            '$_JMP_SHELL_CONTEXT'
            f' {ANSI_YELLOW}➤{ANSI_RESET} "\n'
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
            f.write(init_content)
            rcfile = f.name

        try:
            result = subprocess.run(
                ["bash", "-c", f'source {rcfile}; echo "$PS1"'],
                env={
                    "HOME": home_dir,
                    "PATH": os.environ.get("PATH", ""),
                    "_JMP_SHELL_CONTEXT": "test-device",
                },
                capture_output=True,
                text=True,
                timeout=10,
            )
            assert result.returncode == 0, f"bash failed: {result.stderr}"
            assert "user-prompt" not in result.stdout
            assert "test-device" in result.stdout
        finally:
            os.unlink(rcfile)
    finally:
        shutil.rmtree(home_dir, ignore_errors=True)
