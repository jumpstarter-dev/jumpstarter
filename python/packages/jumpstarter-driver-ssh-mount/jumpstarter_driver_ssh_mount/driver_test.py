import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork
from jumpstarter_driver_ssh.driver import SSHWrapper

from jumpstarter_driver_ssh_mount.driver import SSHMount

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

# Test SSH key content used in multiple tests
TEST_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "test-key-content\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


def _make_ssh_child(default_username="testuser", ssh_identity=None, ssh_identity_file=None,
                    host="127.0.0.1", port=22):
    """Helper to create an SSHWrapper driver instance for use as a child of SSHMount."""
    kwargs = {
        "default_username": default_username,
        "children": {"tcp": TcpNetwork(host=host, port=port)},
    }
    if ssh_identity is not None:
        kwargs["ssh_identity"] = ssh_identity
    if ssh_identity_file is not None:
        kwargs["ssh_identity_file"] = ssh_identity_file
    return SSHWrapper(**kwargs)


def test_ssh_mount_requires_ssh_child():
    """Test that SSHMount driver requires an ssh child"""
    with pytest.raises(ConfigurationError, match="'ssh' child is required"):
        SSHMount()


def test_mount_sshfs_not_installed():
    """Test mount fails gracefully when sshfs is not installed"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with pytest.raises(Exception, match="sshfs is not installed"):
                client.mount("/tmp/test-mount")


def test_mount_sshfs_success():
    """Test successful sshfs mount via port forwarding with subshell"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # sshfs already exited
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    # Test run succeeds, then foreground popen exits immediately (simulated)
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]  # wait returns immediately (exited)

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 2222))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            # The foreground popen will fail because sshfs exits immediately,
                            # which raises ClickException. That's expected in unit tests
                            # where sshfs isn't really running.
                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount", remote_path="/home/user")

                            # Verify test run was called with correct args
                            test_run_args = mock_run.call_args_list[0][0][0]
                            assert test_run_args[0] == "sshfs"
                            assert "testuser@127.0.0.1:/home/user" in test_run_args
                            assert os.path.realpath("/tmp/test-mount") in test_run_args
                            assert "-p" in test_run_args
                            assert "2222" in test_run_args
                            # -f should NOT be in the test run (it's removed for validation)
                            assert "-f" not in test_run_args


def test_mount_sshfs_with_identity():
    """Test sshfs mount with SSH identity"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            test_run_args = mock_run.call_args_list[0][0][0]
                            identity_opts = [
                                test_run_args[i + 1] for i in range(len(test_run_args) - 1)
                                if test_run_args[i] == "-o" and test_run_args[i + 1].startswith("IdentityFile=")
                            ]
                            assert len(identity_opts) == 1


def test_mount_sshfs_allow_other_fallback():
    """Test sshfs mount falls back when allow_other fails, removing both -o and allow_other"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    # First test run fails with allow_other, second succeeds
                    mock_run.side_effect = [
                        MagicMock(returncode=1, stdout="", stderr="allow_other: permission denied"),
                        MagicMock(returncode=0, stdout="", stderr=""),  # retry without allow_other
                        MagicMock(returncode=0, stdout="", stderr=""),  # force_umount after test
                        MagicMock(returncode=0, stdout="", stderr=""),  # force_umount after popen
                    ]
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            # Second test run should not have allow_other
                            second_call_args = mock_run.call_args_list[1][0][0]
                            assert "allow_other" not in second_call_args
                            # Verify no orphaned -o flags
                            for i, arg in enumerate(second_call_args):
                                if arg == "-o":
                                    assert i + 1 < len(second_call_args), "Orphaned -o flag found"
                                    assert not second_call_args[i + 1].startswith("-"), \
                                        f"Orphaned -o flag followed by {second_call_args[i + 1]}"


def test_mount_sshfs_generic_failure():
    """Test mount failure with a non-allow_other error"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Connection refused"
                )
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        with pytest.raises(Exception, match="sshfs mount failed"):
                            client.mount("/tmp/test-mount")

                        # Should only have been called once (no retry)
                        assert mock_run.call_count == 1


def test_mount_sshfs_direct_success():
    """Test sshfs mount using direct TCP address"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(host="10.0.0.1", port=2222)},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with pytest.raises(Exception, match="sshfs mount failed"):
                            client.mount("/tmp/test-mount", direct=True)

                        test_run_args = mock_run.call_args_list[0][0][0]
                        assert test_run_args[0] == "sshfs"
                        assert "testuser@10.0.0.1:/" in test_run_args
                        assert "-p" in test_run_args
                        assert "2222" in test_run_args


def test_mount_sshfs_direct_fallback_to_portforward():
    """Test that direct mount falls back to port forwarding on failure"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0
        mock_proc.stderr = None

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                    mock_proc.wait.side_effect = [None]

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 3333))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            original_ssh = client.ssh

                            class FakeTcp:
                                def address(self):
                                    raise ValueError("not available")

                            class FakeSsh:
                                def __getattr__(self, name):
                                    if name == "tcp":
                                        return FakeTcp()
                                    return getattr(original_ssh, name)

                            with patch.object(client, 'ssh', FakeSsh()):
                                with pytest.raises(Exception, match="sshfs mount failed"):
                                    client.mount("/tmp/test-mount", direct=True)

                            test_run_args = mock_run.call_args_list[0][0][0]
                            # Should have used port forwarding (port 3333)
                            assert "3333" in test_run_args


def test_mount_foreground_mode():
    """Test that foreground flag blocks on sshfs without spawning subshell"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Still running
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # First wait (startup check) - still running
            None,  # Second wait (foreground blocking) - exited
        ]
        mock_proc.returncode = 0

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            client.mount("/tmp/test-mount", foreground=True)

                            # Should have waited on sshfs (foreground mode)
                            assert mock_proc.wait.call_count >= 2
                            # Port forward should be cleaned up
                            mock_adapter.return_value.__exit__.assert_called()


def test_mount_subshell_mode():
    """Test that default mode spawns a subshell"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, 0]  # Running, then exited after subshell
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired("sshfs", 1),  # Startup check - still running
        ]
        mock_proc.returncode = 0

        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                with patch('subprocess.Popen', return_value=mock_proc):
                    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                    with patch('os.makedirs'):
                        with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                            mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                            mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                            with patch.object(client, '_run_subshell') as mock_subshell:
                                client.mount("/tmp/test-mount")

                                # Subshell should have been called
                                resolved = os.path.realpath("/tmp/test-mount")
                                mock_subshell.assert_called_once_with(resolved, "/")


def test_mount_cleanup_on_failure():
    """Test that identity file is cleaned up when mount fails"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=1, stdout="", stderr="Connection refused"
                )
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        with patch('os.unlink') as mock_unlink:
                            with pytest.raises(Exception, match="sshfs mount failed"):
                                client.mount("/tmp/test-mount")

                            # Identity file should be cleaned up on failure
                            assert mock_unlink.called


def test_umount_with_fusermount():
    """Test unmount using fusermount"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "/usr/bin/fusermount"
                assert "-u" in call_args


def test_umount_with_system_umount_fallback():
    """Test unmount falls back to system umount when fusermount is not available"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value=None):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount")

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "umount"


def test_umount_lazy():
    """Test lazy unmount"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount("/tmp/test-mount", lazy=True)

                assert mock_run.called
                call_args = mock_run.call_args[0][0]
                assert "-z" in call_args


def test_umount_failure():
    """Test unmount failure"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not mounted")

                with pytest.raises(Exception, match="Unmount failed"):
                    client.umount("/tmp/test-mount")


def test_cli_has_mount_and_umount_flag():
    """Test that the CLI exposes mount command with --umount and --foreground flags"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "mountpoint" in result.output.lower() or "MOUNTPOINT" in result.output
        assert "--umount" in result.output
        assert "--foreground" in result.output


def test_cli_dispatches_mount():
    """Test that CLI invocation with a mountpoint dispatches to self.mount()"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()

        with patch.object(client, 'mount') as mock_mount:
            result = runner.invoke(cli, ["/tmp/test-cli-mount", "-r", "/home"])
            assert result.exit_code == 0
            mock_mount.assert_called_once_with(
                "/tmp/test-cli-mount",
                remote_path="/home",
                direct=False,
                foreground=False,
                extra_args=[],
            )


def test_cli_dispatches_umount():
    """Test that CLI invocation with --umount dispatches to self.umount()"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        from click.testing import CliRunner
        runner = CliRunner()

        with patch.object(client, 'umount') as mock_umount:
            result = runner.invoke(cli, ["--umount", "/tmp/test-cli-mount", "--lazy"])
            assert result.exit_code == 0
            mock_umount.assert_called_once_with("/tmp/test-cli-mount", lazy=True)
