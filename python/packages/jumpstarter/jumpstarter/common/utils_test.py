import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .utils import (
    ANSI_GRAY,
    ANSI_RESET,
    ANSI_WHITE,
    ANSI_YELLOW,
    PROMPT_CWD,
    _build_common_env,
    _generate_shell_init,
    _lease_env_vars,
    _validate_j_commands,
    launch_shell,
)
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


def test_generate_shell_init_uses_absolute_paths_for_completion():
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


def test_generate_zsh_init_with_profiles_loads_compinit_before_zshrc():
    content = _generate_shell_init("zsh", use_profiles=True, j_commands=["power"])
    assert ".zshrc" in content
    compinit_pos = content.index("autoload -Uz compinit && compinit")
    zshrc_pos = content.index(".zshrc")
    assert compinit_pos < zshrc_pos


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
