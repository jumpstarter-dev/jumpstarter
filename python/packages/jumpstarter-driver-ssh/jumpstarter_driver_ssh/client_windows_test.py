"""Regressions observed with native Windows OpenSSH and a local SSH fixture."""

import logging
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from . import client as ssh_client
from .client import SSHCommandRunOptions, SSHWrapperClient


def make_client():
    client = object.__new__(SSHWrapperClient)
    client.children = {}
    client.logger = logging.getLogger(__name__)
    return client


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            r"C:\Windows\System32\OpenSSH\ssh.exe -o BatchMode=yes",
            [
                r"C:\Windows\System32\OpenSSH\ssh.exe",
                "-o",
                "BatchMode=yes",
            ],
        ),
        (
            '"C:\\Program Files\\SSH λ\\ssh.exe" -o "ProxyCommand=proxy --connect %h:%p"',
            [
                r"C:\Program Files\SSH λ\ssh.exe",
                "-o",
                "ProxyCommand=proxy --connect %h:%p",
            ],
        ),
        ("ssh -o 'UserKnownHostsFile=/dev/null'", ["ssh", "-o", "UserKnownHostsFile=/dev/null"]),
    ],
)
def test_windows_executable_paths_preserve_options(monkeypatch, command, expected):
    monkeypatch.setattr(ssh_client, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(SSHWrapperClient, "command", property(lambda self: command))
    monkeypatch.setattr(SSHWrapperClient, "username", property(lambda self: ""))
    client = make_client()
    assert client._build_ssh_command_args(22, None, []) == expected


@pytest.mark.parametrize(
    ("platform", "text", "encoding"),
    [
        ("win32", True, "utf-8"),
        ("win32", False, None),
        ("linux", True, None),
    ],
)
def test_text_capture_uses_utf8_only_on_windows(monkeypatch, platform, text, encoding):
    monkeypatch.setattr(ssh_client, "sys", SimpleNamespace(platform=platform))
    captured = {}

    def run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0, "café λ" if text else b"\xff", "")

    monkeypatch.setattr(ssh_client.subprocess, "run", run)
    client = make_client()
    result = client._execute_ssh_command(["ssh"], SSHCommandRunOptions(capture_as_text=text))
    assert captured.get("encoding") == encoding
    assert result.stdout == ("café λ" if text else b"\xff")


def test_identity_file_is_removed_when_command_raises():
    client = make_client()
    with (
        pytest.raises(RuntimeError, match="child failed"),
        client._temporary_identity_file("ephemeral test key") as name,
    ):
        path = Path(name)
        assert path.read_text() == "ephemeral test key"
        if ssh_client.sys.platform != "win32":
            assert path.stat().st_mode & 0o777 == 0o600
        raise RuntimeError("child failed")
    assert not path.exists()
    if ssh_client.sys.platform == "win32":
        assert not path.parent.exists()
