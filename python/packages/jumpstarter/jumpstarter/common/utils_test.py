import os
import pty
import select
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .utils import _build_common_env, _lease_env_vars, _zsh_isolated_config, launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1Drivers
from jumpstarter.utils.env import ExporterMetadata, _resolve_drivers_config


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


def test_launch_shell_prints_motd(tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("SHELL", shutil.which("true"))
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        motd="Welcome to my-exporter!",
    )
    assert exit_code == 0
    assert "Welcome to my-exporter!" in capfd.readouterr().out


def test_launch_shell_no_motd_for_command(tmp_path, monkeypatch, capfd):
    true_cmd = shutil.which("true")
    assert true_cmd is not None
    monkeypatch.setenv("SHELL", true_cmd)
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        command=(true_cmd,),
        motd="Welcome to my-exporter!",
    )
    assert exit_code == 0
    assert "Welcome to my-exporter!" not in capfd.readouterr().out


def test_launch_shell_command_not_found(tmp_path, capfd):
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        command=("nonexistent_binary_xyz",),
    )
    assert exit_code == 127
    assert "command not found: nonexistent_binary_xyz" in capfd.readouterr().err


def test_launch_shell_permission_denied(tmp_path, capfd):
    script = tmp_path / "not_executable.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        command=(str(script),),
    )
    assert exit_code == 126
    assert "permission denied" in capfd.readouterr().err


def test_launch_shell_oserror(tmp_path, capfd):
    from unittest.mock import patch
    with patch("jumpstarter.common.utils.Popen", side_effect=OSError(22, "Invalid argument")):
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            command=("some_cmd",),
        )
    assert exit_code == 126
    assert "cannot execute" in capfd.readouterr().err


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


def _launch_shell_capturing_popen(tmp_path, monkeypatch, shell_name):
    """Run launch_shell with a mocked Popen; return (exit_code, call_args)."""
    monkeypatch.setenv("SHELL", str(tmp_path / shell_name))
    with patch("jumpstarter.common.utils.Popen") as mock_popen:
        mock_popen.return_value.wait.return_value = 0
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )
    return exit_code, mock_popen.call_args


def test_launch_shell_bash_prompt_uses_emoji_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fake.bash")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "⚡" in ps1
    assert "➤" in ps1


def test_launch_shell_bash_prompt_uses_ascii_when_no_icons(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_ICONS", "1")
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fake.bash")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "^" in ps1
    assert "⚡" not in ps1
    assert "➤" not in ps1


def test_launch_shell_bash_prompt_uses_ascii_when_no_icons_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_ICONS", "")
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fake.bash")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "^" in ps1
    assert "⚡" not in ps1


def test_launch_shell_fish_prompt_uses_emoji_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fish")
    assert exit_code == 0
    cmd = call[0][0]
    assert "--init-command" in cmd
    init_cmd = cmd[cmd.index("--init-command") + 1]
    assert 'printf "⚡"' in init_cmd
    assert 'printf "➤ "' in init_cmd


def test_launch_shell_fish_prompt_uses_ascii_when_no_icons(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_ICONS", "1")
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fish")
    assert exit_code == 0
    cmd = call[0][0]
    assert "--init-command" in cmd
    init_cmd = cmd[cmd.index("--init-command") + 1]
    assert 'printf "^"' in init_cmd
    assert 'printf "> "' in init_cmd
    assert "⚡" not in init_cmd
    assert "➤" not in init_cmd


def test_launch_shell_zsh_prompt_uses_emoji_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "zsh")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "⚡" in ps1
    assert "➤" in ps1


def test_launch_shell_zsh_prompt_uses_ascii_when_no_icons(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_ICONS", "1")
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "zsh")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "^" in ps1
    assert "⚡" not in ps1
    assert "➤" not in ps1


def test_launch_shell_bash_prompt_is_plain_when_no_color(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fake.bash")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "\\e[" not in ps1  # No ANSI escape sequences
    assert "\\W" in ps1
    assert "⚡" in ps1
    assert "➤" in ps1


@pytest.mark.parametrize("bashopts", ["", "checkwinsize", "histappend:checkwinsize"])
def test_launch_shell_bash_isolated_history_env(tmp_path, monkeypatch, bashopts):
    monkeypatch.setenv("BASHOPTS", bashopts)
    for var in ("HISTSIZE", "HISTFILESIZE"):
        monkeypatch.delenv(var, raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fake.bash")
    assert exit_code == 0
    env = call[1].get("env", {})
    assert env["HISTSIZE"] == "50000"
    assert env["HISTFILESIZE"] == "-1"
    assert "histappend" in env["BASHOPTS"].split(":")
    assert set(bashopts.split(":")) - {""} <= set(env["BASHOPTS"].split(":"))


def test_launch_shell_bash_with_profiles_keeps_user_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", str(tmp_path / "fake.bash"))
    monkeypatch.setenv("BASHOPTS", "checkwinsize")
    for var in ("HISTSIZE", "HISTFILESIZE"):
        monkeypatch.delenv(var, raising=False)
    with patch("jumpstarter.common.utils.Popen") as mock_popen:
        mock_popen.return_value.wait.return_value = 0
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=True,
        )
    assert exit_code == 0
    env = mock_popen.call_args[1].get("env", {})
    assert "HISTSIZE" not in env
    assert "HISTFILESIZE" not in env
    assert env["BASHOPTS"] == "checkwinsize"


def test_launch_shell_fish_prompt_is_plain_when_no_color(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "fish")
    assert exit_code == 0
    cmd = call[0][0]
    assert "--init-command" in cmd
    init_cmd = cmd[cmd.index("--init-command") + 1]
    assert "set_color" not in init_cmd
    assert 'printf "⚡"' in init_cmd
    assert 'printf "➤ "' in init_cmd


def test_launch_shell_zsh_prompt_is_plain_when_no_color(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("NO_ICONS", raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "zsh")
    assert exit_code == 0
    ps1 = call[1].get("env", {}).get("PS1", "")
    assert "%F{" not in ps1  # No zsh color sequences
    assert "⚡" in ps1
    assert "➤" in ps1


def test_launch_shell_zsh_isolated_config(tmp_path, monkeypatch):
    created = []

    def fake_temporary_directory(**kwargs):
        @contextmanager
        def _dir():
            path = tempfile.mkdtemp(dir=str(tmp_path))
            created.append(path)
            yield path

        return _dir()

    monkeypatch.setattr("jumpstarter.common.utils.TemporaryDirectory", fake_temporary_directory)
    for var in ("ZDOTDIR", "HISTSIZE", "SAVEHIST"):
        monkeypatch.delenv(var, raising=False)
    exit_code, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, "zsh")
    assert exit_code == 0
    assert "--no-rcs" not in call[0][0]
    env = call[1].get("env", {})
    assert env["ZDOTDIR"] == created[0]
    assert env["HISTSIZE"] == "50000"
    assert env["SAVEHIST"] == "2147483647"
    assert "history-incremental-search-backward" in (Path(created[0]) / ".zshrc").read_text()
    assert "unsetopt GLOBALRCS" in (Path(created[0]) / ".zshenv").read_text()


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not available")
@pytest.mark.parametrize("original", [None, "", "/config with spaces/it's $(touch UNEXPECTED)"])
def test_zsh_isolated_config_restores_zdotdir(tmp_path, original):
    zsh = shutil.which("zsh")
    assert zsh is not None
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "HISTFILE": ""}
    if original is not None:
        env["ZDOTDIR"] = original
    with _zsh_isolated_config(env):
        result = subprocess.run(
            [
                zsh,
                "-fc",
                'source "$1"; printenv ZDOTDIR',
                "zsh",
                str(Path(env["ZDOTDIR"]) / ".zshrc"),
            ],
            env=env,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
    assert result.returncode == (1 if original is None else 0)
    assert result.stdout == ("" if original is None else original + "\n")
    assert not (tmp_path / "UNEXPECTED").exists()


@pytest.mark.parametrize(
    "shell,values",
    [
        ("fake.bash", {"HISTSIZE": "42", "HISTFILESIZE": "0"}),
        ("zsh", {"HISTSIZE": "42", "SAVEHIST": "0"}),
    ],
)
def test_launch_shell_preserves_exported_history_settings(tmp_path, monkeypatch, shell, values):
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    _, call = _launch_shell_capturing_popen(tmp_path, monkeypatch, shell)
    for key, value in values.items():
        assert call.kwargs["env"][key] == value


def test_launch_shell_zsh_with_profiles_keeps_user_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELL", str(tmp_path / "zsh"))
    monkeypatch.delenv("ZDOTDIR", raising=False)
    with patch("jumpstarter.common.utils.Popen") as mock_popen:
        mock_popen.return_value.wait.return_value = 0
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=True,
        )
    assert exit_code == 0
    assert "--no-rcs" not in mock_popen.call_args[0][0]
    assert "ZDOTDIR" not in mock_popen.call_args[1].get("env", {})


def _pty_read(fd, timeout=3.0):
    out = b""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
            deadline = max(deadline, time.time() + 0.5)
    return out


def _pty_send(fd, data, timeout=1.5):
    os.write(fd, data)
    return _pty_read(fd, timeout)


@pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh not available")
@pytest.mark.parametrize("run_command", [False, True])
def test_launch_shell_zsh_history_and_reverse_search(tmp_path, monkeypatch, run_command):
    """Isolated zsh recalls history at the first prompt and binds ^R."""
    import jumpstarter.common.utils as utils_module

    home = tmp_path / "home"
    home.mkdir()
    (home / ".zsh_history").write_text(
        "".join(f": 1700000000:0;echo line{i:05d}\n" for i in range(1, 20000))
        + ": 1700000000:0;echo PREVIOUS_SESSION_COMMAND\n"
    )
    (home / ".zshrc").write_text("touch $HOME/RC_WAS_SOURCED\n")

    monkeypatch.setenv("SHELL", shutil.which("zsh"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    for var in ("HISTFILE", "HISTSIZE", "SAVEHIST", "ZDOTDIR"):
        monkeypatch.delenv(var, raising=False)

    master_fd, slave_fd = pty.openpty()
    slave = os.fdopen(slave_fd, "w+b", buffering=0)
    monkeypatch.setattr(utils_module.sys, "stdin", slave)
    monkeypatch.setattr(utils_module.sys, "stdout", slave)
    monkeypatch.setattr(utils_module.sys, "stderr", slave)

    real_popen = utils_module.Popen
    spawned = {}

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned["proc"] = proc
        return proc

    monkeypatch.setattr(utils_module, "Popen", recording_popen)

    result = {}

    def run_launch_shell():
        result["rc"] = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="pytest",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )

    thread = threading.Thread(target=run_launch_shell, daemon=True)

    try:
        thread.start()
        prompt = _pty_read(master_fd)
        assert b"pytest" in prompt, f"jmp prompt not shown: {prompt!r}"

        out = _pty_send(master_fd, b"\x1b[A")
        assert b"PREVIOUS_SESSION_COMMAND" in out, f"Up-arrow did not recall history: {out!r}"

        _pty_send(master_fd, b"\x15")
        out = _pty_send(master_fd, b"\x12")
        assert b"i-search:" in out, f"Ctrl-R did not start a search: {out!r}"

        _pty_send(master_fd, b"\x15")
        if run_command:
            _pty_send(master_fd, b"echo SESSION_WRITTEN\n")
        _pty_send(master_fd, b"exit\n")
        thread.join(timeout=10)
        assert not thread.is_alive(), "shell did not exit"
        assert result["rc"] == 0

        content = (home / ".zsh_history").read_text()
        assert ("SESSION_WRITTEN" in content) == run_command
        assert "echo line00001" in content
        assert content.count("\n") >= 20000
        assert all(line.startswith(": ") for line in content.splitlines()), "shared history lost its timestamps"
        assert not (home / "RC_WAS_SOURCED").exists()
    finally:
        proc = spawned.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if thread.is_alive():
            thread.join(timeout=5)
        slave.close()
        os.close(master_fd)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
@pytest.mark.parametrize("small_history", [False, True])
def test_launch_shell_bash_does_not_truncate_history(tmp_path, monkeypatch, small_history):
    """Isolated bash keeps long history files intact on exit."""
    import jumpstarter.common.utils as utils_module

    home = tmp_path / "home"
    home.mkdir()
    histfile = home / ".bash_history"
    histfile.write_text("".join(f"echo line{i:05d}\n" for i in range(1, 60001)))

    monkeypatch.setenv("SHELL", shutil.which("bash"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setenv("NO_COLOR", "1")
    for var in ("HISTFILE", "HISTSIZE", "HISTFILESIZE", "BASHOPTS", "HISTCONTROL", "HISTIGNORE"):
        monkeypatch.delenv(var, raising=False)
    if small_history:
        monkeypatch.setenv("HISTSIZE", "3")

    master_fd, slave_fd = pty.openpty()
    slave = os.fdopen(slave_fd, "w+b", buffering=0)
    monkeypatch.setattr(utils_module.sys, "stdin", slave)
    monkeypatch.setattr(utils_module.sys, "stdout", slave)
    monkeypatch.setattr(utils_module.sys, "stderr", slave)

    real_popen = utils_module.Popen
    spawned = {}

    def recording_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        spawned["proc"] = proc
        return proc

    monkeypatch.setattr(utils_module, "Popen", recording_popen)

    result = {}

    def run_launch_shell():
        result["rc"] = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="pytest",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
        )

    thread = threading.Thread(target=run_launch_shell, daemon=True)

    try:
        thread.start()
        prompt = _pty_read(master_fd)
        assert b"pytest" in prompt, f"jmp prompt not shown: {prompt!r}"

        with histfile.open("a") as file:
            file.write("echo CONCURRENT_ENTRY\n")
        _pty_send(master_fd, b"echo SESSION_WRITTEN\n" * 5)
        _pty_send(master_fd, b"exit\n")
        thread.join(timeout=10)
        assert not thread.is_alive(), "shell did not exit"
        assert result["rc"] == 0

        content = histfile.read_text()
        assert "SESSION_WRITTEN" in content
        assert "CONCURRENT_ENTRY" in content
        assert "echo line00001" in content, "history file was truncated"
        assert content.count("\n") >= 60000, "history file was truncated"
    finally:
        proc = spawned.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if thread.is_alive():
            thread.join(timeout=5)
        slave.close()
        os.close(master_fd)


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


def test_resolve_drivers_config_env_allow_takes_precedence(monkeypatch):
    monkeypatch.setenv("JMP_DRIVERS_ALLOW", "UNSAFE")
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)
    drivers = _resolve_drivers_config()
    assert drivers.unsafe is True
    assert "UNSAFE" in drivers.allow


def test_resolve_drivers_config_env_unsafe_takes_precedence(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.setenv("JMP_DRIVERS_UNSAFE", "true")
    drivers = _resolve_drivers_config()
    assert drivers.unsafe is True


def test_resolve_drivers_config_falls_back_to_client_config(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)

    mock_drivers = ClientConfigV1Alpha1Drivers(allow=["my_driver.*"], unsafe=False)
    mock_client = MagicMock()
    mock_client.drivers = mock_drivers
    mock_user_config = MagicMock()
    mock_user_config.config.current_client = mock_client

    with patch("jumpstarter.config.user.UserConfigV1Alpha1.load", return_value=mock_user_config):
        drivers = _resolve_drivers_config()

    assert drivers.allow == ["my_driver.*"]
    assert drivers.unsafe is False


def test_resolve_drivers_config_falls_back_to_client_config_unsafe(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)

    mock_drivers = ClientConfigV1Alpha1Drivers(allow=["UNSAFE"], unsafe=True)
    mock_client = MagicMock()
    mock_client.drivers = mock_drivers
    mock_user_config = MagicMock()
    mock_user_config.config.current_client = mock_client

    with patch("jumpstarter.config.user.UserConfigV1Alpha1.load", return_value=mock_user_config):
        drivers = _resolve_drivers_config()

    assert drivers.unsafe is True


def test_resolve_drivers_config_defaults_when_no_config(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)

    with patch("jumpstarter.config.user.UserConfigV1Alpha1.load", side_effect=FileNotFoundError("no config")):
        drivers = _resolve_drivers_config()

    assert drivers.allow == []
    assert drivers.unsafe is False


def test_resolve_drivers_config_defaults_when_no_current_client(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)

    mock_user_config = MagicMock()
    mock_user_config.config.current_client = None

    with patch("jumpstarter.config.user.UserConfigV1Alpha1.load", return_value=mock_user_config):
        drivers = _resolve_drivers_config()

    assert drivers.allow == []
    assert drivers.unsafe is False


def test_resolve_drivers_config_propagates_unexpected_errors(monkeypatch):
    monkeypatch.delenv("JMP_DRIVERS_ALLOW", raising=False)
    monkeypatch.delenv("JMP_DRIVERS_UNSAFE", raising=False)

    with (
        patch("jumpstarter.config.user.UserConfigV1Alpha1.load", side_effect=RuntimeError("unexpected")),
        pytest.raises(RuntimeError, match="unexpected"),
    ):
        _resolve_drivers_config()


def test_launch_shell_logs_exit_status(tmp_path, caplog):
    with caplog.at_level("DEBUG", logger="jumpstarter.common.utils"):
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            command=("sh", "-c", "exit 42"),
        )
    assert exit_code == 42
    assert "exited with 42" in caplog.text


def test_launch_shell_ends_a_shell_started_after_access_was_lost(tmp_path):
    """A shell that starts after the session lost access to its lease is ended at once.

    The shared-access monitor signals a running shell; one that starts after
    the monitor acted would otherwise run with nothing left to end it.
    """
    lease = SimpleNamespace(
        exporter_name="my-exporter",
        name="lease-123",
        exporter_labels={},
        lease_ending_callback=None,
        lease_revoked=True,
    )
    exit_code = launch_shell(
        host=str(tmp_path / "test.sock"),
        context="remote",
        allow=["*"],
        unsafe=False,
        use_profiles=False,
        command=("sleep", "10"),
        lease=lease,
    )
    assert exit_code == 128 + signal.SIGHUP


@pytest.mark.parametrize(("signal_name", "signum", "expected"), [("KILL", 9, 137), ("TERM", 15, 143)])
def test_launch_shell_signal_death(tmp_path, caplog, signal_name, signum, expected):
    with caplog.at_level("DEBUG", logger="jumpstarter.common.utils"):
        exit_code = launch_shell(
            host=str(tmp_path / "test.sock"),
            context="remote",
            allow=["*"],
            unsafe=False,
            use_profiles=False,
            command=("sh", "-c", f"kill -{signal_name} $$"),
        )
    assert exit_code == expected
    assert f"killed by signal {signum}" in caplog.text
