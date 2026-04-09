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
    """Test successful sshfs mount via port forwarding"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 2222))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

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
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

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
    """Test sshfs mount falls back when allow_other fails, removing both -o and allow_other"""
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
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 22))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        client.mount("/tmp/test-mount")

                        assert mock_run.call_count == 2
                        # Second call should not have allow_other or its preceding -o
                        second_call_args = mock_run.call_args_list[1][0][0]
                        assert "allow_other" not in second_call_args
                        # Verify no orphaned -o flags: every -o should be followed by a value
                        for i, arg in enumerate(second_call_args):
                            if arg == "-o":
                                assert i + 1 < len(second_call_args), "Orphaned -o flag found"
                                assert second_call_args[i + 1] != "-o", "Orphaned -o flag found"
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
    """Test successful sshfs mount using direct TCP address"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child(host="10.0.0.1", port=2222)},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.makedirs'):
                    client.mount("/tmp/test-mount", direct=True)

                    assert mock_run.called
                    call_args = mock_run.call_args[0][0]
                    assert call_args[0] == "sshfs"
                    assert "testuser@10.0.0.1:/" in call_args
                    assert "-p" in call_args
                    assert "2222" in call_args


def test_mount_sshfs_direct_fallback_to_portforward():
    """Test that direct mount falls back to port forwarding on failure"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        with patch.object(client, '_find_executable', return_value="/usr/bin/sshfs"):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.makedirs'):
                    with patch('jumpstarter_driver_ssh_mount.client.TcpPortforwardAdapter') as mock_adapter:
                        mock_adapter.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 3333))
                        mock_adapter.return_value.__exit__ = MagicMock(return_value=None)

                        # Make the tcp.address() call raise to trigger fallback
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
                            client.mount("/tmp/test-mount", direct=True)

                        assert mock_run.called
                        call_args = mock_run.call_args[0][0]
                        # Should have used port forwarding (port 3333)
                        assert "3333" in call_args


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
        # No fusermount found at all
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


def test_umount_cleans_up_tracked_resources():
    """Test that umount cleans up identity files and port forwards for tracked mounts"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        # Simulate a tracked mount
        mock_adapter = MagicMock()
        client._active_mounts["/tmp/test-mount"] = MagicMock(
            identity_file="/tmp/fake_key",
            port_forward=mock_adapter,
        )

        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch('os.unlink') as mock_unlink:
                    client.umount("/tmp/test-mount")

                    # Identity file should be cleaned up
                    mock_unlink.assert_called_once_with("/tmp/fake_key")
                    # Port forward should be closed
                    mock_adapter.__exit__.assert_called_once()
                    # Mount should be removed from tracking
                    assert "/tmp/test-mount" not in client._active_mounts


def test_umount_all_session_mounts():
    """Test that umount with no args unmounts all tracked mounts"""
    instance = SSHMount(
        children={"ssh": _make_ssh_child()},
    )

    with serve(instance) as client:
        # Simulate two tracked mounts
        client._active_mounts["/tmp/mount1"] = MagicMock(
            identity_file=None, port_forward=None,
        )
        client._active_mounts["/tmp/mount2"] = MagicMock(
            identity_file=None, port_forward=None,
        )

        def _fake_find(name):
            return "/usr/bin/fusermount" if name == "fusermount" else None

        with patch.object(client, '_find_executable', side_effect=_fake_find):
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

                client.umount()

                # Both mounts should have been unmounted
                assert mock_run.call_count == 2
                assert len(client._active_mounts) == 0


def test_cli_has_mount_and_umount_flag():
    """Test that the CLI exposes mount command with --umount flag"""
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
