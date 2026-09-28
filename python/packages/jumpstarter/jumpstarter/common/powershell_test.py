"""Real PowerShell hosts exercise startup, prompt data, and command quoting."""

import json
import os
import shutil
import subprocess
import sys
from functools import partial
from types import SimpleNamespace

import anyio
import pytest

from . import utils

pytestmark = [pytest.mark.anyio, pytest.mark.skipif(sys.platform != "win32", reason="native Windows PowerShell")]


@pytest.fixture(params=["pwsh", "powershell"])
def powershell(request):
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} is not installed")
    return executable


@pytest.fixture
def run_shell(monkeypatch, tmp_path):
    processes = []

    async def run(input_text="", **kwargs):
        input_path = tmp_path / "stdin.txt"
        input_path.write_text(input_text, encoding="ascii")
        with input_path.open("rb") as stdin, (tmp_path / "stdout.txt").open("w+b") as stdout, (
            tmp_path / "stderr.txt"
        ).open("w+b") as stderr:
            def start(cmd, **options):
                options.update(stdin=stdin, stdout=stdout, stderr=stderr)
                process = subprocess.Popen(cmd, **options)
                processes.append(process)
                return process

            monkeypatch.setattr(utils, "Popen", start)
            with anyio.fail_after(20):
                result = await anyio.to_thread.run_sync(partial(
                    utils.launch_shell,
                    host=tmp_path / "private socket",
                    context=kwargs.pop("context", "test-exporter"),
                    allow=["test.Client"],
                    unsafe=False,
                    use_profiles=False,
                    **kwargs,
                ))
            stdout.seek(0)
            stderr.seek(0)
            return result, stdout.read(), stderr.read()

    yield run
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _session_input(powershell, commands, monkeypatch):
    if os.path.basename(powershell).lower() == "powershell.exe":
        # Windows PowerShell 5 consumes redirected stdin as pipeline input to
        # EncodedCommand. Exercise the real host's startup and prompt in that
        # initial command; PowerShell 7 additionally exercises its stdin REPL.
        monkeypatch.setattr(utils, "_POWERSHELL_PROMPT", utils._POWERSHELL_PROMPT + commands)
        return ""
    return commands


@pytest.mark.parametrize("no_icons", [True, False])
async def test_startup_prompt_and_session_environment(powershell, run_shell, monkeypatch, tmp_path, no_icons):
    output = tmp_path / "session.json"
    monkeypatch.setenv("SHELL", powershell)
    monkeypatch.setenv("NO_COLOR", "1")
    if no_icons:
        monkeypatch.setenv("NO_ICONS", "1")
    else:
        monkeypatch.delenv("NO_ICONS", raising=False)
    monkeypatch.setenv("_JMP_TEST_RESULT", str(output))
    lease = SimpleNamespace(
        exporter_name="test-exporter", name="test-lease", exporter_labels={"board": "test"}, lease_ending_callback=None,
    )
    commands = (
        "$record = @{Prompt=(prompt); HostPath=$env:JUMPSTARTER_HOST; "
        "Exporter=$env:JMP_EXPORTER; Lease=$env:JMP_LEASE; Labels=$env:JMP_EXPORTER_LABELS; "
        "Allow=$env:JMP_DRIVERS_ALLOW; Insecure=$env:JMP_GRPC_INSECURE; Passphrase=$env:JMP_GRPC_PASSPHRASE}\n"
        "[IO.File]::WriteAllText($env:_JMP_TEST_RESULT, ($record | ConvertTo-Json -Compress), [Text.Encoding]::UTF8)\n"
        "exit 23\n"
    )

    commands = _session_input(powershell, commands, monkeypatch)
    result, stdout, stderr = await run_shell(commands, lease=lease, insecure=True, passphrase="test passphrase")

    assert result == 23, (stdout, stderr)
    record = json.loads(output.read_text(encoding="utf-8-sig"))
    bolt, arrow = ("^", ">") if no_icons else ("⚡", "➤")
    assert record == {
        "Prompt": f"{os.path.basename(os.getcwd())} {bolt} test-exporter {arrow} ",
        "HostPath": str(tmp_path / "private socket"),
        "Exporter": "test-exporter", "Lease": "test-lease", "Labels": "board=test",
        "Allow": "test.Client", "Insecure": "1", "Passphrase": "test passphrase",
    }
    assert b"\x1b[" not in record["Prompt"].encode()


@pytest.mark.parametrize("no_color", [False, True])
async def test_prompt_context_is_literal_data(powershell, run_shell, monkeypatch, tmp_path, no_color):
    output = tmp_path / "literal context.txt"
    monkeypatch.setenv("SHELL", powershell)
    monkeypatch.setenv("NO_ICONS", "1")
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("_JMP_TEST_RESULT", str(output))
    context = "test-exporter $(throw 'context is data'); \u00fc"
    commands = (
        "[IO.File]::WriteAllText($env:_JMP_TEST_RESULT, (prompt), [Text.Encoding]::UTF8)\n"
        "exit 17\n"
    )
    result, stdout, stderr = await run_shell(_session_input(powershell, commands, monkeypatch), context=context)

    assert result == 17, (stdout, stderr)
    value = output.read_text(encoding="utf-8-sig")
    if no_color:
        assert context in value
    else:
        # Colored prompts write their decoration to the host, returning only
        # the final space to PowerShell's prompt machinery.
        assert value == " "
        assert b"test-exporter" in stdout + stderr


async def test_explicit_command_preserves_powershell_quotes_and_exit_code(powershell, run_shell, monkeypatch, tmp_path):
    output = tmp_path / "quoted command.txt"
    monkeypatch.setenv("_JMP_TEST_RESULT", str(output))
    value = 'spaces "quotes"; & $literal \\ tail'
    script = (
        f"$value = '{value}'; "
        "[IO.File]::WriteAllText($env:_JMP_TEST_RESULT, $value, [Text.Encoding]::UTF8); exit 37"
    )

    result, stdout, stderr = await run_shell(command=(powershell, "-NoProfile", "-c", script))

    assert result == 37, (stdout, stderr)
    assert output.read_text(encoding="utf-8-sig") == value
