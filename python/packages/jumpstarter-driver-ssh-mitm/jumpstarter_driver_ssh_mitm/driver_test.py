"""Tests for the SSH MITM driver"""

import threading
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter_driver_ssh_mitm.client import SSHMITMCommandRunResult
from jumpstarter_driver_ssh_mitm.driver import SSHMITM, SSHMITMError

from jumpstarter.client.core import DriverMethodNotImplemented
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve

# Test SSH key content (valid Ed25519 format header, content is placeholder)
TEST_SSH_KEY = """-----BEGIN OPENSSH PRIVATE KEY-----
b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW
QyNTUxOQAAACBHK2n0Z+2k2LXuT7+0zTcSCfprKPDR+9xG7nXZ7zRy5AAAAJgq0lzTKtJc
0wAAAAtzc2gtZWQyNTUxOQAAACBHK2n0Z+2k2LXuT7+0zTcSCfprKPDR+9xG7nXZ7zRy5A
AAAEBpIq2lZeL9Ey+OQhKfhIIhK1U0rkqMjFolbvQZ8qGVnkcraeRn7aTYte5Pv7TNNxIJ
+mso8NH73EbuddnvNHLkAAAADXRlc3RAZXhhbXBsZQECAwQF
-----END OPENSSH PRIVATE KEY-----
"""


class TestSSHMITMDriver:
    """Tests for SSHMITM driver configuration and setup"""

    def test_defaults(self):
        """Test SSH MITM with default configuration"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="",
            ssh_identity=TEST_SSH_KEY
        )

        assert instance.default_username == ""
        assert instance.ssh_identity == TEST_SSH_KEY
        assert instance.client() == "jumpstarter_driver_ssh_mitm.client.SSHMITMClient"

    def test_configuration_error_missing_tcp(self):
        """Test SSH MITM raises error when tcp child is missing"""
        with pytest.raises(ConfigurationError, match="'tcp' child is required"):
            SSHMITM(
                children={},
                default_username="",
                ssh_identity=TEST_SSH_KEY
            )

    def test_configuration_error_missing_identity(self):
        """Test SSH MITM raises error when identity is missing"""
        with pytest.raises(ConfigurationError, match="Either ssh_identity or ssh_identity_file must be provided"):
            SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username=""
            )

    def test_configuration_error_both_identities(self):
        """Test SSH MITM raises error when both identity options are provided"""
        with pytest.raises(ConfigurationError, match="Cannot specify both ssh_identity and ssh_identity_file"):
            SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="",
                ssh_identity=TEST_SSH_KEY,
                ssh_identity_file="/path/to/key"
            )

    def test_get_default_username(self):
        """Test getting default username via gRPC"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY
        )

        with serve(instance) as client:
            username = client.call("get_default_username")
            assert username == "testuser"

    def test_identity_file(self, tmp_path):
        """Test SSH MITM with identity file"""
        temp_file_path = tmp_path / "_test_key"
        temp_file_path.write_text(TEST_SSH_KEY)

        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file=str(temp_file_path),
        )

        # Internal access should work
        assert instance.get_ssh_identity() == TEST_SSH_KEY

        # gRPC access should fail
        with serve(instance) as client:
            with pytest.raises(DriverMethodNotImplemented):
                client.call("get_ssh_identity")


def _create_mock_ssh_client(exit_code=0, stdout=b"", stderr=b""):
    """Helper to create a mock paramiko SSHClient."""
    mock_client = MagicMock()
    
    mock_stdout = MagicMock()
    mock_stdout.channel.recv_exit_status.return_value = exit_code
    mock_stdout.read.return_value = stdout
    
    mock_stderr = MagicMock()
    mock_stderr.read.return_value = stderr
    
    mock_client.exec_command.return_value = (MagicMock(), mock_stdout, mock_stderr)
    return mock_client


class TestSSHMITMClient:
    """Tests for SSHMITMClient"""

    def test_execute_command(self):
        """Test execute_command gRPC method"""
        mock_ssh_client = _create_mock_ssh_client(
            exit_code=0,
            stdout=b"test-hostname\n",
            stderr=b""
        )
        mock_pkey = MagicMock()

        # Patch at module level before serving
        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient', return_value=mock_ssh_client), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY
            )

            with serve(instance) as client:
                result = client.execute(["hostname"])

                assert isinstance(result, SSHMITMCommandRunResult)
                assert result.return_code == 0
                assert result.stdout == "test-hostname\n"
                assert result.stderr == ""

    def test_run_alias(self):
        """Test that run() is an alias for execute()"""
        mock_ssh_client = _create_mock_ssh_client(
            exit_code=0,
            stdout=b"output",
            stderr=b""
        )
        mock_pkey = MagicMock()

        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient', return_value=mock_ssh_client), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY
            )

            with serve(instance) as client:
                # run() should work the same as execute()
                result = client.run(["echo", "hello"])
                assert isinstance(result, SSHMITMCommandRunResult)
                assert result.return_code == 0

    def test_execute_command_failure(self):
        """Test execute_command handles failures"""
        mock_ssh_client = _create_mock_ssh_client(
            exit_code=1,
            stdout=b"",
            stderr=b"command not found"
        )
        mock_pkey = MagicMock()

        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient', return_value=mock_ssh_client), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY
            )

            with serve(instance) as client:
                result = client.execute(["nonexistent"])

                assert result.return_code == 1
                assert result.stderr == "command not found"

    def test_execute_multiple_args(self):
        """Test execute with multiple command arguments"""
        mock_ssh_client = _create_mock_ssh_client(
            exit_code=0,
            stdout=b"file1.txt\nfile2.txt\n",
            stderr=b""
        )
        mock_pkey = MagicMock()

        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient', return_value=mock_ssh_client), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY
            )

            with serve(instance) as client:
                result = client.execute(["ls", "-la", "/tmp"])

                assert result.return_code == 0
                # Verify the command was joined correctly
                mock_ssh_client.exec_command.assert_called_with("ls -la /tmp")

    def test_execute_args_looking_like_options(self):
        """Test execute with arguments that look like Click options (e.g., ps -ef)"""
        mock_ssh_client = _create_mock_ssh_client(
            exit_code=0,
            stdout=b"PID TTY TIME CMD\n",
            stderr=b""
        )
        mock_pkey = MagicMock()

        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient', return_value=mock_ssh_client), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY
            )

            with serve(instance) as client:
                # Test ps -ef where -e and -f are separate arguments that could be mistaken for options
                result = client.execute(["ps", "-e", "-f"])

                assert result.return_code == 0
                # Verify the command was joined correctly with shlex.join
                mock_ssh_client.exec_command.assert_called_with("ps -e -f")

    def test_execute_empty_command(self):
        """Test execute handles empty argument list"""
        mock_ssh_client = _create_mock_ssh_client(exit_code=0, stdout=b"", stderr=b"")
        mock_pkey = MagicMock()

        with patch(
            "jumpstarter_driver_ssh_mitm.driver.paramiko.SSHClient", return_value=mock_ssh_client
        ), patch(
            "jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key",
            return_value=mock_pkey,
        ):
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="testuser",
                ssh_identity=TEST_SSH_KEY,
            )

            with serve(instance) as client:
                result = client.execute([])

                assert result.return_code == 1
                assert "No command provided" in result.stderr


class TestSSHMITMSecurity:
    """Security-focused tests"""

    def test_key_not_accessible_via_grpc(self):
        """Verify SSH key cannot be retrieved via gRPC"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            ssh_identity=TEST_SSH_KEY
        )

        with serve(instance) as client:
            with pytest.raises(DriverMethodNotImplemented):
                client.call("get_ssh_identity")

        assert instance.get_ssh_identity() == TEST_SSH_KEY

    def test_key_accessible_internally(self):
        """Verify key is accessible on driver (server) side"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            ssh_identity=TEST_SSH_KEY
        )

        # Internal access works
        assert instance.get_ssh_identity() == TEST_SSH_KEY


class TestSSHMITMCleanup:
    """Tests for resource cleanup"""

    def test_close_cleans_up(self):
        """Test that close() cleans up resources"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY
        )

        # Should not raise
        instance.close()

    def test_identity_file_not_found(self):
        """Test error handling when identity file doesn't exist"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file="/nonexistent/path/to/key"
        )

        # Calling get_ssh_identity should raise ConfigurationError
        with pytest.raises(ConfigurationError):
            instance.get_ssh_identity()


class TestSSHMITMKeyTypes:
    """Tests for SSH key type detection"""

    def test_load_ed25519_key(self):
        """Test loading Ed25519 key"""
        mock_pkey = MagicMock()
        
        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', return_value=mock_pkey):
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY
            )
            
            key = instance._load_private_key(TEST_SSH_KEY)
            assert key == mock_pkey

    def test_load_rsa_key_fallback(self):
        """Test RSA key loading when Ed25519 fails"""
        import paramiko
        mock_rsa_key = MagicMock()
        
        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', 
                   side_effect=paramiko.SSHException("Not Ed25519")), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.RSAKey.from_private_key', 
                   return_value=mock_rsa_key):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY
            )
            
            key = instance._load_private_key(TEST_SSH_KEY)
            assert key == mock_rsa_key

    def test_unsupported_key_type(self):
        """Test error when key type is not supported"""
        import paramiko
        
        with patch('jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key', 
                   side_effect=paramiko.SSHException("Not Ed25519")), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.RSAKey.from_private_key', 
                   side_effect=paramiko.SSHException("Not RSA")), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.ECDSAKey.from_private_key', 
                   side_effect=paramiko.SSHException("Not ECDSA")), \
             patch('jumpstarter_driver_ssh_mitm.driver.paramiko.DSSKey.from_private_key', 
                   side_effect=paramiko.SSHException("Not DSS")):
            
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY
            )
            
            with pytest.raises(SSHMITMError, match="unsupported key type"):
                instance._load_private_key(TEST_SSH_KEY)


class TestSSHMITMStream:
    """Tests for stream/connect behavior"""

    @pytest.mark.anyio
    async def test_connect_starts_session(self, monkeypatch):
        """Ensure connect stream spins up the handler thread"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="tester",
            ssh_identity=TEST_SSH_KEY,
        )

        started = threading.Event()

        def fake_handle_session(self, transport):
            started.set()

        instance._handle_session = fake_handle_session.__get__(instance, SSHMITM)

        class DummyTransport:
            def __init__(self, sock):
                self.sock = sock

            def close(self):
                pass

        monkeypatch.setattr(
            "jumpstarter_driver_ssh_mitm.driver.paramiko.Transport", DummyTransport
        )

        async with instance.connect() as stream:
            await stream.aclose()

        assert started.is_set()
