"""Windows lifecycle hooks: PowerShell by default, with Python, cmd.exe and Git Bash."""

import shutil
import subprocess
import sys
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
from jumpstarter.exporter import hooks
from jumpstarter.exporter.hooks import HookExecutionError, HookExecutor

pytestmark = pytest.mark.anyio

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="native Windows hook execution")


@pytest.fixture
def lease_scope():
    from anyio import Event

    from jumpstarter.exporter.lease_context import LeaseContext

    lease_scope = LeaseContext(lease_name="test-lease-123", before_lease_hook=Event(), client_name="test-client")
    session = MagicMock()
    session.context_log_source.return_value = nullcontext()
    session.motd = None
    lease_scope.session = session
    lease_scope.socket_path = "C:/private/session/socket"
    return lease_scope


@pytest.fixture(params=[None, "powershell", "pwsh"])
def powershell(request):
    """The default interpreter (None) and each installed PowerShell edition."""
    if request.param is not None and shutil.which(request.param) is None:
        pytest.skip(f"{request.param} is not installed")
    return request.param


async def _run(lease_scope, *, after=False, **hook):
    hook.setdefault("timeout", 20)
    instance = HookInstanceConfigV1Alpha1(**hook)
    config = HookConfigV1Alpha1(**{"after_lease" if after else "before_lease": instance})
    executor = HookExecutor(config=config)
    with patch("jumpstarter.exporter.hooks.logger") as logger:
        if after:
            result = await executor.execute_after_lease_hook(lease_scope)
        else:
            result = await executor.execute_before_lease_hook(lease_scope)
    return result, [call.args[1] for call in logger.info.call_args_list if call.args[:1] == ("%s",)]


def _running(pid: int) -> bool:
    listing = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True,
    )
    return f'"{pid}"' in listing.stdout


@windows_only
async def test_powershell_streams_output_lines_in_order(lease_scope, powershell):
    result, lines = await _run(
        lease_scope, exec_=powershell,
        script="Write-Output 'Line 1'\n'Line 2'\necho 'Line 3 café ☃'",
    )
    assert result is None
    assert lines == ["Line 1", "Line 2", "Line 3 café ☃"]


@windows_only
async def test_powershell_exit_code_fails_hook(lease_scope, powershell):
    with pytest.raises(HookExecutionError, match="exit code 3") as caught:
        await _run(lease_scope, exec_=powershell, script="Write-Output 'before'\nexit 3", on_failure="endLease")
    assert caught.value.hook_type == "before_lease"


@windows_only
async def test_powershell_receives_hook_environment(lease_scope, powershell):
    result, lines = await _run(
        lease_scope, exec_=powershell,
        script='"LEASE=$env:LEASE_NAME"; "CLIENT=$env:CLIENT_NAME"; "HOST=$env:JUMPSTARTER_HOST"',
    )
    assert result is None
    assert lines == ["LEASE=test-lease-123", "CLIENT=test-client", "HOST=C:/private/session/socket"]


@windows_only
async def test_powershell_motd_file_is_appended(lease_scope, powershell):
    # Windows PowerShell's > redirection and Set-Content use different encodings.
    lease_scope.session.motd = "Welcome"
    result, _ = await _run(
        lease_scope, exec_=powershell,
        script='"flashed image: test-v1 ☃" > $env:JMP_MOTD_FILE',
    )
    assert result is None
    assert lease_scope.session.motd == "Welcome\nflashed image: test-v1 ☃"


@windows_only
async def test_after_lease_hook_has_no_motd_file(lease_scope, powershell):
    result, _ = await _run(
        lease_scope, after=True, exec_=powershell,
        script="if ($env:JMP_MOTD_FILE) { exit 1 }", on_failure="endLease",
    )
    assert result is None


@windows_only
async def test_timeout_stops_the_hook_process_tree(lease_scope, tmp_path):
    pid_file = tmp_path / "child.pid"
    child = tmp_path / "child.py"
    child.write_text(f"import os, time\nopen({str(pid_file)!r}, 'w').write(str(os.getpid()))\ntime.sleep(60)\n")
    with pytest.raises(HookExecutionError, match="timed out"):
        await _run(
            lease_scope, timeout=3, on_failure="exit",
            script=f"& '{sys.executable}' '{child}'",
        )
    assert not _running(int(pid_file.read_text()))


@windows_only
async def test_timeout_with_warn_returns_warning(lease_scope):
    result, _ = await _run(lease_scope, timeout=1, on_failure="warn", script="Start-Sleep 30")
    assert "timed out" in result


@windows_only
async def test_ps1_file(lease_scope, tmp_path):
    script = tmp_path / "hook script.ps1"
    script.write_text("Write-Output \"PS1_OK $env:LEASE_NAME\"\nexit 0\n")
    result, lines = await _run(lease_scope, script=str(script))
    assert result is None
    assert lines == ["PS1_OK test-lease-123"]


@windows_only
async def test_cmd_file(lease_scope, tmp_path):
    script = tmp_path / "hook.cmd"
    script.write_text("@echo CMD_OK %LEASE_NAME%\r\n@exit /b 5\r\n")
    with pytest.raises(HookExecutionError, match="exit code 5"):
        await _run(lease_scope, script=str(script), on_failure="endLease")


requires_bash = pytest.mark.skipif(
    sys.platform != "win32" or hooks._windows_bash() is None, reason="native Windows Bash such as Git Bash"
)


@requires_bash
@pytest.mark.parametrize("interpreter", ["bash", "/bin/sh", "/bin/bash"])
async def test_posix_shell_hooks_use_native_bash(lease_scope, interpreter):
    result, lines = await _run(
        lease_scope, exec_=interpreter, script='V="hello_world"; echo "BASH_OK: ${V:6:5} $LEASE_NAME"',
    )
    assert result is None
    assert lines == ["BASH_OK: world test-lease-123"]


@requires_bash
async def test_sh_file_uses_native_bash(lease_scope, tmp_path):
    script = tmp_path / "hook.sh"
    script.write_text('echo "SHFILE_OK" > "$JMP_MOTD_FILE"\necho done\n', newline="\n")
    result, lines = await _run(lease_scope, script=str(script))
    assert result is None
    assert lines == ["done"]
    assert lease_scope.session.motd == "SHFILE_OK"


def test_default_interpreter_prefers_powershell_7(monkeypatch):
    monkeypatch.setattr(hooks.shutil, "which", {"pwsh": "C:/pwsh.exe", "powershell": "C:/powershell.exe"}.get)
    command = hooks._windows_hook_command(HookInstanceConfigV1Alpha1(script="j power on"))
    assert command[:4] == ["C:/pwsh.exe", "-NoLogo", "-NoProfile", "-NonInteractive"]
    assert command[-2] == "-EncodedCommand"


@pytest.mark.parametrize("name", ["python", "python3"])
def test_bare_python_names_use_the_exporter_interpreter(name):
    command = hooks._windows_hook_command(HookInstanceConfigV1Alpha1(exec_=name, script="print(1)"))
    assert command == [sys.executable, "-c", "print(1)"]


def test_missing_bash_is_a_hook_error(monkeypatch):
    monkeypatch.setattr(hooks, "_windows_bash", lambda: None)
    with pytest.raises(RuntimeError, match="no native Bash"):
        hooks._windows_hook_command(HookInstanceConfigV1Alpha1(exec_="bash", script="true"))


def test_bash_lookup_skips_the_wsl_launcher(monkeypatch, tmp_path):
    system32 = tmp_path / "Windows" / "System32"
    system32.mkdir(parents=True)
    (system32 / "bash.exe").touch()
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    monkeypatch.setattr(hooks.shutil, "which", {"bash": str(system32 / "bash.exe")}.get)
    assert hooks._windows_bash() is None


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"plain \xe2\x98\x83\n", "plain ☃\n"),
        (b"\xef\xbb\xbfutf-8 bom", "utf-8 bom"),
        ("utf-16 ☃".encode("utf-16"), "utf-16 ☃"),
    ],
)
def test_hook_text_decoding(data, expected):
    assert hooks._decode_hook_text(data) == expected
