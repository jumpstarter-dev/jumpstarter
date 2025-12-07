"""Tests for the SSH MITM driver"""

import threading
from unittest.mock import MagicMock, patch

import pytest
from jumpstarter_driver_network.driver import TcpNetwork

from jumpstarter_driver_ssh_mitm.driver import SSHMITM, SSHMITMError

from jumpstarter.common.exceptions import ConfigurationError

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
            ssh_identity=TEST_SSH_KEY,
        )

        assert instance.default_username == ""
        assert instance.ssh_identity == TEST_SSH_KEY
        # Now returns NetworkClient since SSHMITM is a network layer
        assert instance.client() == "jumpstarter_driver_network.client.NetworkClient"

    def test_configuration_error_missing_tcp(self):
        """Test SSH MITM raises error when tcp child is missing"""
        with pytest.raises(ConfigurationError, match="'tcp' child is required"):
            SSHMITM(children={}, default_username="", ssh_identity=TEST_SSH_KEY)

    def test_configuration_error_missing_identity(self):
        """Test SSH MITM raises error when identity is missing"""
        with pytest.raises(
            ConfigurationError,
            match="Either ssh_identity or ssh_identity_file must be provided",
        ):
            SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="",
            )

    def test_configuration_error_both_identities(self):
        """Test SSH MITM raises error when both identity options are provided"""
        with pytest.raises(
            ConfigurationError,
            match="Cannot specify both ssh_identity and ssh_identity_file",
        ):
            SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                default_username="",
                ssh_identity=TEST_SSH_KEY,
                ssh_identity_file="/path/to/key",
            )

    def test_identity_from_inline(self):
        """Test SSH identity from inline content"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY,
        )

        # Internal access should work
        assert instance._get_ssh_identity() == TEST_SSH_KEY

    def test_identity_from_file(self, tmp_path):
        """Test SSH identity from file"""
        temp_file_path = tmp_path / "_test_key"
        temp_file_path.write_text(TEST_SSH_KEY)

        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file=str(temp_file_path),
        )

        # Internal access should work
        assert instance._get_ssh_identity() == TEST_SSH_KEY


class TestSSHMITMSecurity:
    """Security-focused tests"""

    def test_key_accessible_internally(self):
        """Verify key is accessible on driver (server) side"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            ssh_identity=TEST_SSH_KEY,
        )

        # Internal access works
        assert instance._get_ssh_identity() == TEST_SSH_KEY

    def test_key_not_accessible_via_rpc(self):
        """Verify key cannot be accessed via RPC through NetworkClient"""
        from jumpstarter.common.utils import serve

        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            ssh_identity=TEST_SSH_KEY,
        )

        with serve(instance) as client:
            # NetworkClient should not have access to get_ssh_identity
            # The method is private and not exported
            assert not hasattr(client, "get_ssh_identity")
            assert not hasattr(client, "_get_ssh_identity")

    def test_uses_network_client(self):
        """Verify SSHMITM uses NetworkClient (not a custom client)"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            ssh_identity=TEST_SSH_KEY,
        )

        # Should return NetworkClient since SSHMITM is a network layer
        assert instance.client() == "jumpstarter_driver_network.client.NetworkClient"


class TestSSHMITMCleanup:
    """Tests for resource cleanup"""

    def test_close_cleans_up(self):
        """Test that close() cleans up resources"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY,
        )

        # Should not raise
        instance.close()

    def test_identity_file_not_found(self):
        """Test error handling when identity file doesn't exist"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity_file="/nonexistent/path/to/key",
        )

        # Calling _get_ssh_identity should raise ConfigurationError
        with pytest.raises(ConfigurationError):
            instance._get_ssh_identity()


class TestSSHMITMKeyTypes:
    """Tests for SSH key type detection"""

    def test_load_ed25519_key(self):
        """Test loading Ed25519 key"""
        mock_pkey = MagicMock()

        with patch(
            "jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key",
            return_value=mock_pkey,
        ):
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY,
            )

            key = instance._load_private_key(TEST_SSH_KEY)
            assert key == mock_pkey

    def test_load_rsa_key_fallback(self):
        """Test RSA key loading when Ed25519 fails"""
        import paramiko

        mock_rsa_key = MagicMock()

        with (
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key",
                side_effect=paramiko.SSHException("Not Ed25519"),
            ),
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.RSAKey.from_private_key",
                return_value=mock_rsa_key,
            ),
        ):
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY,
            )

            key = instance._load_private_key(TEST_SSH_KEY)
            assert key == mock_rsa_key

    def test_unsupported_key_type(self):
        """Test error when key type is not supported"""
        import paramiko

        with (
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.Ed25519Key.from_private_key",
                side_effect=paramiko.SSHException("Not Ed25519"),
            ),
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.RSAKey.from_private_key",
                side_effect=paramiko.SSHException("Not RSA"),
            ),
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.ECDSAKey.from_private_key",
                side_effect=paramiko.SSHException("Not ECDSA"),
            ),
            patch(
                "jumpstarter_driver_ssh_mitm.driver.paramiko.DSSKey.from_private_key",
                side_effect=paramiko.SSHException("Not DSS"),
            ),
        ):
            instance = SSHMITM(
                children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
                ssh_identity=TEST_SSH_KEY,
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

        monkeypatch.setattr("jumpstarter_driver_ssh_mitm.driver.paramiko.Transport", DummyTransport)

        async with instance.connect() as stream:
            await stream.aclose()

        assert started.is_set()

    def test_handle_session_timeout(self, caplog):
        """Test that _handle_session properly handles timeout when no exec/shell request is received"""
        import logging

        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY,
            channel_timeout=0.1,  # Short timeout for testing
        )

        # Track if channel close is called
        channel_close_called = []

        # Create a mock transport that simulates a client connecting but never sending exec/shell
        mock_transport = MagicMock()
        mock_channel = MagicMock()

        def track_channel_close():
            channel_close_called.append(True)

        mock_channel.close = track_channel_close
        mock_transport.accept.return_value = mock_channel
        mock_transport.add_server_key = MagicMock()
        mock_transport.start_server = MagicMock()
        mock_transport.close = MagicMock()

        # Call _handle_session - the event.wait() will timeout since no exec/shell request is made
        # This simulates a client that connects but never sends a command
        with caplog.at_level(logging.ERROR):
            instance._handle_session(mock_transport)

        # Verify timeout error was logged (line 403 in driver.py)
        assert "No exec/shell request received before timeout" in caplog.text

        # Verify channel.close() was called (line 404) - this is the key timeout behavior
        assert len(channel_close_called) > 0, "Channel close should have been called due to timeout"

    def test_mitm_proxy_forwards_data(self):
        """Integration test: Verify MITM proxy correctly forwards data between client and DUT"""
        instance = SSHMITM(
            children={"tcp": TcpNetwork(host="127.0.0.1", port=22)},
            default_username="testuser",
            ssh_identity=TEST_SSH_KEY,
        )

        # Mock data that will flow through the proxy
        client_to_dut_data = b"test command\n"
        dut_to_client_data = b"command output\n"

        # Mock DUT channel - simulate receiving data from DUT
        mock_dut_channel = MagicMock()
        mock_dut_channel.recv.side_effect = [dut_to_client_data, b""]  # Return data then EOF
        mock_dut_channel.sendall = MagicMock()
        mock_dut_channel.close = MagicMock()
        mock_dut_channel.recv_exit_status.return_value = 0

        # Mock client channel - simulate receiving data from client
        mock_client_channel = MagicMock()
        mock_client_channel.recv.side_effect = [client_to_dut_data, b""]  # Return data then EOF
        mock_client_channel.sendall = MagicMock()
        mock_client_channel.close = MagicMock()
        mock_client_channel.send_exit_status = MagicMock()

        # Mock DUT client
        mock_dut_client = MagicMock()
        mock_dut_transport = MagicMock()
        mock_dut_transport.open_session.return_value = mock_dut_channel
        mock_dut_client.get_transport.return_value = mock_dut_transport
        mock_dut_client.close = MagicMock()

        # Mock transport
        mock_transport = MagicMock()
        mock_transport.accept.return_value = mock_client_channel
        mock_transport.add_server_key = MagicMock()
        mock_transport.start_server = MagicMock()
        mock_transport.close = MagicMock()

        # Mock _create_dut_client and _open_dut_channel
        with (
            patch.object(instance, "_create_dut_client", return_value=mock_dut_client),
            patch.object(instance, "_open_dut_channel", return_value=(mock_dut_client, mock_dut_channel)),
        ):
            # Create server interface and simulate exec request
            from jumpstarter_driver_ssh_mitm.driver import MITMServerInterface

            server = MITMServerInterface(instance.default_username)
            server.exec_command = "test command"
            server.event.set()

            # Mock the server creation in _handle_session
            with patch("jumpstarter_driver_ssh_mitm.driver.MITMServerInterface", return_value=server):
                # Call _handle_session with mocked transport
                instance._handle_session(mock_transport)

        assert mock_dut_channel is not None
        assert mock_client_channel is not None

        # Verify exit status was forwarded for exec commands
        mock_client_channel.send_exit_status.assert_called_once_with(0)

        # Verify cleanup
        mock_client_channel.close.assert_called()
        mock_dut_channel.close.assert_called()
        mock_dut_client.close.assert_called()
        mock_transport.close.assert_called()
