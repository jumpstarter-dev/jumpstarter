import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

from .client import BleWriteNotifyStreamClient
from .console import BleConsole


def test_client_import_and_cli_help_without_posix_terminal_modules():
    # Pexpect loads its optional PTY implementation on POSIX; isolate this client's imports.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from types import SimpleNamespace; "
                "import jumpstarter_driver_network.adapters; "
                "sys.modules['termios'] = None; sys.modules['tty'] = None; "
                "from jumpstarter_driver_ble.client import BleWriteNotifyStreamClient; "
                "from click.testing import CliRunner; "
                "client = SimpleNamespace(description=None, methods_description={}); "
                "result = CliRunner().invoke(BleWriteNotifyStreamClient.cli(client), ['--help']); "
                "assert result.exit_code == 0, result.output; "
                "assert 'info' in result.output"
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
    monkeypatch.setitem(sys.modules, "termios", None)
    monkeypatch.setitem(sys.modules, "tty", None)
    client = SimpleNamespace(description=None, methods_description={}, portal=Mock())

    result = CliRunner().invoke(BleWriteNotifyStreamClient.cli(client), ["console"])

    assert result.exit_code == 1
    assert "Interactive BLE console is not available on this platform" in result.output
    assert "Use the stream/pexpect API instead" in result.output
    client.portal.call.assert_not_called()


def test_console_restores_posix_terminal_on_error(monkeypatch):
    original = object()
    termios = SimpleNamespace(tcgetattr=Mock(return_value=original), tcsetattr=Mock(), TCSADRAIN=1)
    tty = SimpleNamespace(setraw=Mock())
    monkeypatch.setitem(sys.modules, "termios", termios)
    monkeypatch.setitem(sys.modules, "tty", tty)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: 7))

    with pytest.raises(RuntimeError, match="stream failed"), BleConsole(Mock()).setraw():
        raise RuntimeError("stream failed")

    tty.setraw.assert_called_once_with(7)
    termios.tcsetattr.assert_called_once_with(7, termios.TCSADRAIN, original)
