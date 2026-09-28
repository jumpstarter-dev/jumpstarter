import io
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from click.testing import CliRunner

from . import console as console_module
from .client import Console, PySerialClient


def test_client_import_and_cli_help_without_platform_terminal_dependencies():
    # Pexpect loads its optional PTY implementation on POSIX; isolate this client's imports.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from types import SimpleNamespace; "
                "import jumpstarter_driver_network.adapters; "
                "sys.modules['termios'] = None; sys.modules['tty'] = None; "
                "sys.modules['prompt_toolkit'] = None; "
                "from jumpstarter_driver_pyserial.client import Console, PySerialClient; "
                "import jumpstarter_driver_pyserial.windows_console; "
                "assert sys.modules['prompt_toolkit'] is None; "
                "from click.testing import CliRunner; "
                "client = SimpleNamespace(description=None, methods_description={}); "
                "result = CliRunner().invoke(PySerialClient.cli(client), ['--help']); "
                "assert result.exit_code == 0, result.output; "
                "assert 'pipe' in result.output"
            ),
        ],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_console_reports_missing_terminal_backend_only_when_invoked(monkeypatch):
    monkeypatch.setattr(console_module, "sys", SimpleNamespace(platform="linux", stdin=sys.stdin))
    monkeypatch.setitem(sys.modules, "termios", None)
    monkeypatch.setitem(sys.modules, "tty", None)
    client = SimpleNamespace(description=None, methods_description={}, portal=Mock())

    result = CliRunner().invoke(PySerialClient.cli(client), ["console"])

    assert result.exit_code == 1
    assert "Interactive serial console is not available on this platform" in result.output
    assert "Use 'pipe' or the stream/pexpect API instead" in result.output
    client.portal.call.assert_not_called()


def test_console_restores_posix_terminal_on_error(monkeypatch):
    original = object()
    termios = SimpleNamespace(tcgetattr=Mock(return_value=original), tcsetattr=Mock(), TCSADRAIN=1)
    tty = SimpleNamespace(setraw=Mock())
    monkeypatch.setitem(sys.modules, "termios", termios)
    monkeypatch.setitem(sys.modules, "tty", tty)
    monkeypatch.setattr(
        console_module, "sys", SimpleNamespace(platform="linux", stdin=SimpleNamespace(fileno=lambda: 7))
    )

    with pytest.raises(RuntimeError, match="stream failed"), Console(Mock()).setraw():
        raise RuntimeError("stream failed")

    tty.setraw.assert_called_once_with(7)
    termios.tcsetattr.assert_called_once_with(7, termios.TCSADRAIN, original)


@pytest.mark.parametrize("observe", [False, True])
def test_binary_pipe_to_file_without_terminal_backend(monkeypatch, tmp_path, observe):
    monkeypatch.setitem(sys.modules, "termios", None)
    monkeypatch.setitem(sys.modules, "tty", None)
    chunks = [b"\x00\xff\r\n", bytes(range(256))]
    stream = SimpleNamespace(receive=AsyncMock(side_effect=[*chunks, anyio.EndOfStream]))
    methods = []

    @asynccontextmanager
    async def stream_async(*, method):
        methods.append(method)
        yield stream

    client = object.__new__(PySerialClient)
    client.stream_async = stream_async
    destination = tmp_path / "serial.bin"

    async def pipe():
        await client._pipe_serial(output_file=str(destination), observe=observe)

    anyio.run(pipe)

    assert destination.read_bytes() == b"".join(chunks)
    assert methods == ["observe" if observe else "connect"]


def test_binary_stdin_pipe_preserves_bytes_and_signals_eof(monkeypatch):
    monkeypatch.setitem(sys.modules, "termios", None)
    monkeypatch.setitem(sys.modules, "tty", None)
    payload = bytes(range(256)) * 9
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(payload)))
    stream = SimpleNamespace(send=AsyncMock(), send_eof=AsyncMock())
    client = object.__new__(PySerialClient)

    counts = anyio.run(client._stdin_to_serial, stream)

    assert counts == (len(payload), len(payload))
    assert b"".join(call.args[0] for call in stream.send.await_args_list) == payload
    stream.send_eof.assert_awaited_once()
