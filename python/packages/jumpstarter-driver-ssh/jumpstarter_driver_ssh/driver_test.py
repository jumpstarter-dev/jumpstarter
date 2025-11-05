"""Tests for the SSH wrapper driver"""

from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter_driver_ssh.driver import SSHWrapper

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

# Test SSH key content used in multiple tests
TEST_SSH_KEY = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "test-key-content\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


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


def test_ssh_identity_string_configuration():
    """Test SSH wrapper with ssh_identity string configuration"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity=TEST_SSH_KEY
    )

    # Test that the instance was created correctly
    assert instance.ssh_identity == TEST_SSH_KEY
    assert instance.ssh_identity_file is None

    # Test that the client class is correct
    assert instance.client() == "jumpstarter_driver_ssh.client.SSHWrapperClient"


def test_ssh_identity_file_configuration():
    """Test SSH wrapper with ssh_identity_file configuration"""
    import os
    import tempfile

    # Create a temporary file with SSH key content
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_test_key') as temp_file:
        temp_file.write(TEST_SSH_KEY)
        temp_file_path = temp_file.name

    try:
        instance = SSHWrapper(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file=temp_file_path
        )

        # Test that the instance was created correctly
        # ssh_identity should be None until first use (lazy loading)
        assert instance.ssh_identity is None
        assert instance.ssh_identity_file == temp_file_path

        # Test that get_ssh_identity() reads the file on first use
        identity = instance.get_ssh_identity()
        assert identity == TEST_SSH_KEY

        # Test that ssh_identity is now cached
        assert instance.ssh_identity == TEST_SSH_KEY

        # Test that the client class is correct
        assert instance.client() == "jumpstarter_driver_ssh.client.SSHWrapperClient"
    finally:
        # Clean up the temporary file
        os.unlink(temp_file_path)


def test_ssh_identity_validation_error():
    """Test SSH wrapper raises error when both ssh_identity and ssh_identity_file are provided"""
    with pytest.raises(ConfigurationError, match="Cannot specify both ssh_identity and ssh_identity_file"):
        SSHWrapper(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity="test-key-content",
            ssh_identity_file="/path/to/key"
        )


def test_ssh_identity_file_read_error():
    """Test SSH wrapper raises error when ssh_identity_file cannot be read on first use"""
    # Instance creation should succeed (lazy loading)
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity_file="/nonexistent/path/to/key"
    )

    # Error should be raised when get_ssh_identity() is called
    with pytest.raises(ConfigurationError, match="Failed to read ssh_identity_file"):
        instance.get_ssh_identity()


def test_ssh_command_with_identity_string():
    """Test SSH command execution with ssh_identity string"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity=TEST_SSH_KEY
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command with identity string
            result = client.run(False, ["hostname"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should include -i flag with temporary identity file
            assert "-i" in call_args
            identity_file_index = call_args.index("-i")
            identity_file_path = call_args[identity_file_index + 1]

            # The identity file should be a temporary file
            assert identity_file_path.endswith("_ssh_key")
            assert "/tmp" in identity_file_path or "/var/tmp" in identity_file_path

            # Should include -l testuser
            assert "-l" in call_args
            assert "testuser" in call_args

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args
            assert "hostname" in call_args

            assert result == 0


def test_ssh_command_with_identity_file():
    """Test SSH command execution with ssh_identity_file"""
    import os
    import tempfile

    # Create a temporary file with SSH key content
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='_test_key') as temp_file:
        temp_file.write(TEST_SSH_KEY)
        temp_file_path = temp_file.name

    try:
        instance = SSHWrapper(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file=temp_file_path
        )

        with serve(instance) as client:
            with patch('subprocess.run') as mock_run:
                mock_run.return_value = MagicMock(returncode=0)

                # Test SSH command with identity file
                result = client.run(False, ["hostname"])

                # Verify subprocess.run was called
                assert mock_run.called
                call_args = mock_run.call_args[0][0]  # First positional argument

                # Should include -i flag with temporary identity file
                assert "-i" in call_args
                identity_file_index = call_args.index("-i")
                identity_file_path = call_args[identity_file_index + 1]

                # The identity file should be a temporary file (not the original file)
                assert identity_file_path.endswith("_ssh_key")
                assert "/tmp" in identity_file_path or "/var/tmp" in identity_file_path
                assert identity_file_path != temp_file_path

                # Should include -l testuser
                assert "-l" in call_args
                assert "testuser" in call_args

                # Should include the actual hostname (127.0.0.1) at the end
                assert "127.0.0.1" in call_args
                assert "hostname" in call_args

                assert result == 0
    finally:
        # Clean up the temporary file
        os.unlink(temp_file_path)


def test_ssh_command_without_identity():
    """Test SSH command execution without identity (should not include -i flag)"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser"
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            # Test SSH command without identity
            result = client.run(False, ["hostname"])

            # Verify subprocess.run was called
            assert mock_run.called
            call_args = mock_run.call_args[0][0]  # First positional argument

            # Should NOT include -i flag
            assert "-i" not in call_args

            # Should include -l testuser
            assert "-l" in call_args
            assert "testuser" in call_args

            # Should include the actual hostname (127.0.0.1) at the end
            assert "127.0.0.1" in call_args
            assert "hostname" in call_args

            assert result == 0


def test_ssh_identity_temp_file_creation_and_cleanup():
    """Test that temporary identity file is created and cleaned up properly"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity=TEST_SSH_KEY
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            with patch('tempfile.NamedTemporaryFile') as mock_temp_file:
                with patch('os.chmod') as mock_chmod:
                    with patch('os.unlink') as mock_unlink:
                        # Mock the temporary file
                        mock_temp_file_instance = MagicMock()
                        mock_temp_file_instance.name = "/tmp/test_ssh_key_12345"
                        mock_temp_file_instance.write = MagicMock()
                        mock_temp_file_instance.close = MagicMock()
                        mock_temp_file.return_value = mock_temp_file_instance

                        # Test SSH command with identity
                        result = client.run(False, ["hostname"])

                        # Verify temporary file was created
                        mock_temp_file.assert_called_once_with(mode='w', delete=False, suffix='_ssh_key')
                        mock_temp_file_instance.write.assert_called_once_with(TEST_SSH_KEY)
                        mock_temp_file_instance.close.assert_called_once()

                        # Verify proper permissions were set
                        mock_chmod.assert_called_once_with("/tmp/test_ssh_key_12345", 0o600)

                        # Verify temporary file was cleaned up
                        mock_unlink.assert_called_once_with("/tmp/test_ssh_key_12345")

                        assert result == 0


def test_ssh_identity_temp_file_creation_error():
    """Test error handling when temporary identity file creation fails"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity=TEST_SSH_KEY
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            with patch('tempfile.NamedTemporaryFile') as mock_temp_file:
                mock_temp_file.side_effect = OSError("Permission denied")

                # Test SSH command with identity should raise an error
                # The exception will be wrapped in an ExceptionGroup due to the context manager
                with pytest.raises(ExceptionGroup) as exc_info:
                    client.run(False, ["hostname"])

                # Check that the original OSError is in the exception group
                assert any(isinstance(e, OSError) and "Permission denied" in str(e) for e in exc_info.value.exceptions)


def test_ssh_identity_temp_file_cleanup_error():
    """Test error handling when temporary identity file cleanup fails"""
    instance = SSHWrapper(
        children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
        default_username="testuser",
        ssh_identity=TEST_SSH_KEY
    )

    with serve(instance) as client:
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0)

            with patch('tempfile.NamedTemporaryFile') as mock_temp_file:
                with patch('os.chmod') as mock_chmod:
                    with patch('os.unlink') as mock_unlink:
                        # Mock the temporary file
                        mock_temp_file_instance = MagicMock()
                        mock_temp_file_instance.name = "/tmp/test_ssh_key_12345"
                        mock_temp_file_instance.write = MagicMock()
                        mock_temp_file_instance.close = MagicMock()
                        mock_temp_file.return_value = mock_temp_file_instance

                        # Mock cleanup failure
                        mock_unlink.side_effect = OSError("Permission denied")

                        # Test SSH command with identity - should still succeed but log warning
                        with patch.object(client, 'logger') as mock_logger:
                            result = client.run(False, ["hostname"])

                            # Verify chmod was called
                            mock_chmod.assert_called_once_with("/tmp/test_ssh_key_12345", 0o600)

                            # Verify warning was logged
                            mock_logger.warning.assert_called_once()
                            warning_call = mock_logger.warning.call_args[0][0]
                            assert "Failed to clean up temporary identity file" in warning_call
                            assert "/tmp/test_ssh_key_12345" in warning_call

                            assert result == 0
