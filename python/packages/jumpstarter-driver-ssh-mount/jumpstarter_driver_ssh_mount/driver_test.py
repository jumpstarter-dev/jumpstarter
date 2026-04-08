"""Tests for the SSH mount driver"""

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
    """Test successful sshfs mount"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__.return_value = ("127.0.0.1", 2222)
                        mock_adapter.return_value.__exit__.return_value = None

                        client.mount("/tmp/test-mount", remote_path="/home/user")

                        assert mock_run.called
                        call_args = mock_run.call_args[0][0]
                        assert call_args[0] == "sshfs"
                        assert "testuser@127.0.0.1:/home/user" in call_args
                        assert "/tmp/test-mount" in call_args
                        assert "-p" in call_args
                        assert "2222" in call_args


def test_mount_sshfs_with_identity():
    """Test sshfs mount with SSH identity"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(ssh_identity=TEST_SSH_KEY)},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__.return_value = ("127.0.0.1", 22)
                        mock_adapter.return_value.__exit__.return_value = None

                        client.mount("/tmp/test-mount")

                        assert mock_run.called
                        call_args = mock_run.call_args[0][0]
                        # Should include IdentityFile option
                        identity_opts = [
                            call_args[i + 1] for i in range(len(call_args) - 1)
                            if call_args[i] == "-o" and call_args[i + 1].startswith("IdentityFile=")
                        ]
                        assert len(identity_opts) == 1


def test_mount_sshfs_allow_other_fallback():
    """Test sshfs mount falls back when allow_other fails"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                # First call fails with allow_other error, second succeeds
                mock_run.side_effect = [
                    MagicMock(returncode=1, stdout="", stderr="allow_other: permission denied"),
                    MagicMock(returncode=0, stdout="", stderr=""),
                ]
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__.return_value = ("127.0.0.1", 22)
                        mock_adapter.return_value.__exit__.return_value = None

                        client.mount("/tmp/test-mount")

                        assert mock_run.call_count == 2
                        # Second call should not have allow_other
                        second_call_args = mock_run.call_args_list[1][0][0]
                        assert "allow_other" not in second_call_args


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


def test_cli_has_mount_umount_commands():
    """Test that the CLI exposes mount and umount subcommands"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        cli = client.cli()
        # The CLI should be a click Group with mount and umount commands
        assert hasattr(cli, 'commands') or hasattr(cli, 'list_commands')
        from click.testing import CliRunner
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert "mount" in result.output
        assert "umount" in result.output
