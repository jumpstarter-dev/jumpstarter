import ctypes
import json
import subprocess
import sys
from datetime import timedelta
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock

import anyio
import pytest

from . import utils


def _launch(**kwargs):
    return utils.launch_shell(
        host=kwargs.pop("host", "C:/private/session/socket"),
        context="test-exporter",
        allow=["test.Client"],
        unsafe=False,
        use_profiles=kwargs.pop("use_profiles", False),
        **kwargs,
    )


@pytest.fixture
def windows_platform(monkeypatch):
    monkeypatch.setattr(utils, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.delenv("SHELL", raising=False)


@pytest.mark.parametrize(
    ("available", "comspec", "expected"),
    [
        ({"pwsh": "pwsh.exe", "powershell": "powershell.exe"}, "cmd.exe", ["pwsh.exe", "-NoLogo", "-NoProfile"]),
        ({"powershell": "powershell.exe"}, "cmd.exe", ["powershell.exe", "-NoLogo", "-NoProfile"]),
        ({}, "C:/Windows/System32/cmd.exe", ["C:/Windows/System32/cmd.exe", "/D"]),
        ({}, None, ["cmd.exe", "/D"]),
    ],
)
def test_windows_default_shell(monkeypatch, windows_platform, available, comspec, expected):
    monkeypatch.setattr(utils.shutil, "which", available.get)
    if comspec is None:
        monkeypatch.delenv("COMSPEC", raising=False)
    else:
        monkeypatch.setenv("COMSPEC", comspec)
    run = Mock(return_value=0)
    monkeypatch.setattr(utils, "_run_process", run)

    assert _launch() == 0
    assert run.call_args.args[0][:len(expected)] == expected
    assert run.call_args.args[1]["JUMPSTARTER_HOST"] == "C:/private/session/socket"


@pytest.mark.parametrize(
    ("shell", "use_profiles", "options"),
    [
        (r"C:\Program Files\PowerShell\7\pwsh.EXE", True, ["-NoLogo"]),
        (r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", False, ["-NoLogo", "-NoProfile"]),
        (r"C:\Windows\System32\cmd.exe", True, []),
        (r"C:\Windows\System32\cmd.exe", False, ["/D"]),
        (r"C:\Program Files\Git\bin\bash.exe", False, ["--norc", "--noprofile"]),
        (r"C:\custom\shell.exe", False, []),
    ],
)
def test_windows_shell_override_and_profiles(monkeypatch, windows_platform, shell, use_profiles, options):
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setattr(utils.shutil, "which", Mock(side_effect=AssertionError("SHELL must take precedence")))
    run = Mock(return_value=0)
    monkeypatch.setattr(utils, "_run_process", run)

    assert _launch(use_profiles=use_profiles) == 0
    assert run.call_args.args[0][:len(options) + 1] == [shell, *options]
    assert ("-NoProfile" in run.call_args.args[0]) == ("-NoProfile" in options)


@pytest.fixture
def owned_processes(monkeypatch):
    processes = []

    def start(cmd, **kwargs):
        kwargs.update(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        process = subprocess.Popen(cmd, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(utils, "Popen", start)
    yield processes
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_command_preserves_arguments_environment_and_exit_status(tmp_path, monkeypatch, owned_processes):
    output = tmp_path / "child output.json"
    host = tmp_path / "private socket"
    script = (
        "import json, os, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps([sys.argv[2:], os.environ['JUMPSTARTER_HOST']])); "
        "sys.exit(42)"
    )
    arguments = ["spaces and Unicode \u96ea", "& $ semicolon;", 'embedded "quote"']
    monkeypatch.setattr(utils, "_default_shell", Mock(side_effect=AssertionError("commands do not need a shell")))

    assert _launch(host=host, command=(sys.executable, "-c", script, str(output), *arguments)) == 42
    assert json.loads(output.read_text()) == [arguments, str(host)]
    assert owned_processes[0].poll() == 42


def _lease():
    return SimpleNamespace(
        exporter_name="test-exporter",
        name="test-lease",
        exporter_labels={},
        lease_ending_callback=Mock(),
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process termination on lease expiration")
@pytest.mark.anyio
async def test_windows_lease_expiration_stops_owned_command(owned_processes):
    lease = _lease()
    previous_callback = lease.lease_ending_callback
    with anyio.fail_after(8):
        async with anyio.create_task_group() as group:
            group.start_soon(
                anyio.to_thread.run_sync,
                partial(_launch, command=(sys.executable, "-c", "import time; time.sleep(60)"), lease=lease),
            )
            while not owned_processes or lease.lease_ending_callback is previous_callback:
                await anyio.sleep(0.01)
            lease.lease_ending_callback(lease, timedelta(seconds=1))
            assert owned_processes[0].poll() is None
            lease.lease_ending_callback(lease, timedelta(0))

    assert owned_processes[0].poll() is not None


# The command starts a descendant, records its PID, then runs for the given seconds.
_SPAWN_DESCENDANT = """
import pathlib, subprocess, sys, time
descendant = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path(sys.argv[1]).write_text(str(descendant.pid))
time.sleep(float(sys.argv[2]))
"""


def _running(pid):
    listing = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, check=True,
    )
    return f'"{pid}"' in listing.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object containment")
@pytest.mark.anyio
async def test_windows_lease_expiration_stops_the_process_tree(owned_processes, tmp_path):
    lease = _lease()
    previous_callback = lease.lease_ending_callback
    pid_file = tmp_path / "descendant.pid"
    with anyio.fail_after(20):
        async with anyio.create_task_group() as group:
            group.start_soon(
                anyio.to_thread.run_sync,
                partial(_launch, command=(sys.executable, "-c", _SPAWN_DESCENDANT, str(pid_file), "60"), lease=lease),
            )
            while not pid_file.exists() or lease.lease_ending_callback is previous_callback:
                await anyio.sleep(0.05)
            descendant = int(pid_file.read_text())
            assert _running(descendant)
            lease.lease_ending_callback(lease, timedelta(0))

    assert owned_processes[0].poll() is not None
    assert not _running(descendant)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object containment")
def test_windows_normal_exit_leaves_background_processes_running(owned_processes, tmp_path):
    pid_file = tmp_path / "descendant.pid"
    assert _launch(command=(sys.executable, "-c", _SPAWN_DESCENDANT, str(pid_file), "0")) == 0
    descendant = int(pid_file.read_text())
    try:
        assert _running(descendant)
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(descendant)], capture_output=True, check=False)


def test_posix_lease_expiration_preserves_sighup(monkeypatch):
    monkeypatch.setattr(utils, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(utils, "signal", SimpleNamespace(SIGHUP=1))
    process = Mock()
    utils.lease_ending_handler(process, None, timedelta(0))
    process.send_signal.assert_called_once_with(1)
    process.terminate.assert_not_called()


@pytest.mark.parametrize(("changed", "expected_restore"), [(1, [("ctrl", None, False)]), (0, [])])
def test_windows_foreground_child_owns_console_ctrl_c(monkeypatch, changed, expected_restore):
    # Console Ctrl+C reaches every attached process; only the child should act on it.
    calls = []
    kernel32 = SimpleNamespace(SetConsoleCtrlHandler=lambda handler, ignore: calls.append(("ctrl", handler, ignore))
                               or (changed if ignore else 1))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)
    monkeypatch.setattr(utils, "sys", SimpleNamespace(
        platform="win32", stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
    ))
    process = Mock()
    process.wait.side_effect = lambda: calls.append(("wait",)) or 7
    monkeypatch.setattr(utils, "Popen", Mock(return_value=process))
    monkeypatch.setattr(utils, "_contain_process_tree", lambda _process: None)

    assert utils._run_process(["child"], {}) == 7
    assert calls == [("ctrl", None, True), ("wait",), *expected_restore]


def test_posix_wait_leaves_console_signals_unchanged(monkeypatch):
    monkeypatch.setattr(ctypes, "WinDLL", Mock(side_effect=AssertionError("POSIX must not call Win32")), raising=False)
    monkeypatch.setattr(utils, "sys", SimpleNamespace(
        platform="linux", stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
    ))
    process = Mock()
    process.wait.return_value = 0
    monkeypatch.setattr(utils, "Popen", Mock(return_value=process))

    assert utils._run_process(["child"], {}) == 0
