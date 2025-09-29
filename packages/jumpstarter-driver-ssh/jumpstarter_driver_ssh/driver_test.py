"""Tests for the SSH wrapper driver"""

from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter_driver_ssh.driver import SSHWrapper

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve


def test_ssh_wrapper_defaults():
    """Test SSH wrapper with default configuration"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    # Test that the instance was created correctly
    assert instance.default_username == ""
    assert instance.ssh_command.startswith("ssh")

    # Test that the client class is correct
    assert instance.client() == "jumpstarter_driver_ssh.client.SSHWrapperClient"


def test_ssh_wrapper_configuration_error():
    """Test SSH wrapper raises error when tcp child is missing"""
    with pytest.raises(ConfigurationError):
        SSHWrapper(
            children={},  # Missing tcp child
            default_username=""
        )


def test_ssh_command_with_default_username():
    """Test SSH command execution with default username provided"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with default username
            result = client.run(False, ["hostname"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include -l testuser
            assert "-l" in call_args
            assert "testuser" in call_args
            assert call_args[call_args.index("-l") + 1] == "testuser"

            # Should include the actual hostname (127.0.0.1) at the end, and preserve "hostname" as a command
            assert "127.0.0.1" in call_args
            assert "hostname" in call_args  # Should be preserved as command argument

            assert result == 0


def test_ssh_command_without_default_username():
    """Test SSH command execution without default username"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command without default username
            result = client.run(False, ["hostname"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should NOT include -l flag
            assert "-l" not in call_args

            # Should include the actual hostname (127.0.0.1) at the end, and preserve "hostname" as a command
            assert "127.0.0.1" in call_args
            assert "hostname" in call_args  # Should be preserved as command argument

            assert result == 0


def test_ssh_command_with_user_override():
    """Test SSH command execution with -l flag overriding default username"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with -l flag overriding default username
            result = client.run(False, ["-l", "myuser", "hostname"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include -l myuser (not testuser)
            assert "-l" in call_args
            assert "myuser" in call_args
            assert "testuser" not in call_args
            assert call_args[call_args.index("-l") + 1] == "myuser"

            # Should include the actual hostname (127.0.0.1) at the end, and preserve "hostname" as a command
            assert "127.0.0.1" in call_args
            assert "hostname" in call_args  # Should be preserved as command argument

            assert result == 0


def test_ssh_command_with_port():
    """Test SSH command execution with custom port"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=2222)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Mock the TcpPortforwardAdapter to return the expected port
            with patch('jumpstarter_driver_ssh.client.TcpPortforwardAdapter') as mock_adapter:
                mock_adapter.return_value.__enter__.return_value = ("127.0.0.1", 2222)
                mock_adapter.return_value.__exit__.return_value = None

                # Test SSH command with custom port
                result = client.run(False, ["hostname"])

                # Verify subprocess.run was called
                assert mock_run.called
                call_args = mock_run.call_args[0][0]  # First positional argument

                # Should include -p 2222
                assert "-p" in call_args
                assert "2222" in call_args
                assert call_args[call_args.index("-p") + 1] == "2222"

                # Should include -l testuser
                assert "-l" in call_args
                assert "testuser" in call_args

                # Should include the actual hostname (127.0.0.1) at the end
                assert "127.0.0.1" in call_args
                assert "hostname" in call_args  # Should be preserved as command argument

                assert result == 0


def test_ssh_command_with_direct_flag():
    """Test SSH command execution with --direct flag"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="192.168.1.100", port=22)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Mock the tcp.address() method
            with patch.object(client.tcp, 'address', return_value="tcp://192.168.1.100:22"):
                # Test SSH command with direct flag
                result = client.run(True, ["hostname"])

                # Verify subprocess.run was called
                assert mock_run.called
                call_args = mock_run.call_args[0][0]  # First positional argument

                # Should include -l testuser
                assert "-l" in call_args
                assert "testuser" in call_args

                # Should include the actual hostname (192.168.1.100) at the end, and preserve "hostname" as a command
                assert "192.168.1.100" in call_args
                assert "hostname" in call_args  # Should be preserved as command argument

                assert result == 0


def test_ssh_command_error_handling():
    """Test SSH command error handling when SSH is not found"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = FileNotFoundError("SSH not found")

            # Test SSH command error handling
            result = client.run(False, ["hostname"])

            # Should return error code 127
            assert result == 127


def test_ssh_command_with_multiple_ssh_options():
    """Test SSH command execution with multiple SSH options"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with multiple SSH options
            result = client.run(False, [
                "-o", "StrictHostKeyChecking=no", "-i", "/path/to/key", "command", "arg1", "arg2"
            ])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include SSH options
            assert "-o" in call_args
            assert "StrictHostKeyChecking=no" in call_args
            assert "-i" in call_args
            assert "/path/to/key" in call_args

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args
            # Should preserve command arguments
            assert "command" in call_args
            assert "arg1" in call_args
            assert "arg2" in call_args

            assert result == 0


def test_ssh_command_with_unknown_option_treated_as_command():
    """Test SSH command execution with unknown option treated as command"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with unknown option
            result = client.run(False, ["-l", "user", "-unknown", "command", "arg1"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include known SSH options
            assert "-l" in call_args
            assert "user" in call_args

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args
            # Should treat everything after -l user as command (including -unknown)
            assert "-unknown" in call_args
            assert "command" in call_args
            assert "arg1" in call_args

            assert result == 0


def test_ssh_command_with_no_ssh_options():
    """Test SSH command execution with no SSH options, all arguments are command"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username=""
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with no SSH options
            result = client.run(False, ["command", "arg1", "arg2"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args
            # Should preserve all command arguments
            assert "command" in call_args
            assert "arg1" in call_args
            assert "arg2" in call_args

            assert result == 0


def test_ssh_command_with_command_l_flag_does_not_interfere_with_username_injection():
    """Test that command -l flags don't interfere with SSH username injection"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with -l flag in the command (like ls -la -l ajo)
            result = client.run(False, ["ls", "-la", "-l", "ajo"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include -l testuser (SSH login flag)
            assert "-l" in call_args
            assert "testuser" in call_args
            assert call_args[call_args.index("-l") + 1] == "testuser"

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args

            # Should preserve command arguments including the -l flag for ls
            assert "ls" in call_args
            assert "-la" in call_args
            assert "-l" in call_args  # This should be the ls -l flag, not SSH -l
            assert "ajo" in call_args

            # Verify that the SSH -l flag comes before the hostname, and command -l comes after
            ssh_l_index = call_args.index("-l")
            hostname_index = call_args.index("127.0.0.1")
            command_l_index = call_args.index("-l", ssh_l_index + 1)  # Find second -l

            assert ssh_l_index < hostname_index < command_l_index

            assert result == 0
