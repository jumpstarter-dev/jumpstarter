import os
import signal
from unittest.mock import MagicMock, patch

from . import _privilege


class TestHasPasswordlessSudo:
    def test_returns_true_when_root(self):
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "getuid", return_value=0):
            assert _privilege.has_passwordless_sudo() is True
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()

    def test_returns_true_when_sudo_works(self):
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()
        mock_result = MagicMock(returncode=0)
        with patch.object(os, "getuid", return_value=1000), \
             patch("subprocess.run", return_value=mock_result):
            assert _privilege.has_passwordless_sudo() is True
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()

    def test_returns_false_when_sudo_fails(self):
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()
        mock_result = MagicMock(returncode=1)
        with patch.object(os, "getuid", return_value=1000), \
             patch("subprocess.run", return_value=mock_result):
            assert _privilege.has_passwordless_sudo() is False
        _privilege.has_passwordless_sudo.cache_clear()
        _privilege._needs_sudo.cache_clear()


class TestHasPrivileges:
    def test_returns_true_when_root(self):
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "getuid", return_value=0):
            assert _privilege.has_privileges() is True
        _privilege._needs_sudo.cache_clear()


class TestSudoCmd:
    def test_prepends_sudo_when_not_root(self):
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "getuid", return_value=1000):
            assert _privilege.sudo_cmd(["ls"]) == ["sudo", "ls"]
        _privilege._needs_sudo.cache_clear()

    def test_no_sudo_when_root(self):
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "getuid", return_value=0):
            assert _privilege.sudo_cmd(["ls"]) == ["ls"]
        _privilege._needs_sudo.cache_clear()


class TestSignalPid:
    def test_sends_signal_directly(self):
        with patch.object(os, "kill") as mock_kill:
            _privilege.signal_pid(1234, signal.SIGTERM)
            mock_kill.assert_called_once_with(1234, signal.SIGTERM)

    def test_falls_back_to_sudo_kill_on_permission_error(self):
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "kill", side_effect=PermissionError), \
             patch.object(os, "getuid", return_value=1000), \
             patch("subprocess.run") as mock_run:
            _privilege.signal_pid(1234, signal.SIGTERM)
            mock_run.assert_called_once_with(
                ["sudo", "kill", "-TERM", "1234"], check=False
            )
        _privilege._needs_sudo.cache_clear()

    def test_sudo_kill_with_sighup(self):
        _privilege._needs_sudo.cache_clear()
        with patch.object(os, "kill", side_effect=PermissionError), \
             patch.object(os, "getuid", return_value=1000), \
             patch("subprocess.run") as mock_run:
            _privilege.signal_pid(5678, signal.SIGHUP)
            mock_run.assert_called_once_with(
                ["sudo", "kill", "-HUP", "5678"], check=False
            )
        _privilege._needs_sudo.cache_clear()
