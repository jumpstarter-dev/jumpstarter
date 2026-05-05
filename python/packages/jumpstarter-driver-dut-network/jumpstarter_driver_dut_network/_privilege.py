"""Privilege escalation helpers for running commands that require root."""

import functools
import logging
import os
import signal
import subprocess

logger = logging.getLogger(__name__)


@functools.cache
def _needs_sudo() -> bool:
    return os.getuid() != 0


@functools.cache
def has_passwordless_sudo() -> bool:
    """Return True if the process is root or can use sudo without a password."""
    if not _needs_sudo():
        return True
    result = subprocess.run(
        ["sudo", "-n", "true"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def has_privileges() -> bool:
    """Return True if the process can run privileged network commands."""
    return os.getuid() == 0 or has_passwordless_sudo()


def sudo_cmd(cmd: list[str]) -> list[str]:
    """Prepend sudo to a command list when not running as root."""
    if _needs_sudo():
        return ["sudo"] + cmd
    return cmd


def signal_pid(pid: int, sig: int = signal.SIGTERM) -> None:
    """Send a signal to a process, using sudo kill if not root."""
    try:
        os.kill(pid, sig)
    except PermissionError:
        sig_name = {signal.SIGTERM: "TERM", signal.SIGKILL: "KILL", signal.SIGHUP: "HUP"}.get(sig, str(sig))
        subprocess.run(sudo_cmd(["kill", f"-{sig_name}", str(pid)]), check=False)
