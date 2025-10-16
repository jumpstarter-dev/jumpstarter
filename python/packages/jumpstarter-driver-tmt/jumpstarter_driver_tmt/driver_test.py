from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from jumpstarter_driver_network.driver import TcpNetwork

from .client import replace_provision_args
from .driver import TMT
from jumpstarter.common.utils import serve


def test_drivers_tmt():
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        assert client.ssh.address() == "tcp://127.0.0.1:22"


def test_drivers_tmt_cli():
    """Test the CLI functionality with tmt command and arguments"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        # Test the CLI tmt command without arguments
        runner = CliRunner()
        cli = client.cli()

        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0  # Success return code
            result = runner.invoke(cli, [])
            assert result.exit_code == 0
            mock_run_tmt.assert_called_once()

        # Test the CLI tmt command with arguments
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0  # Success return code
            result = runner.invoke(cli, ["test", "arg1", "arg2"])
            assert result.exit_code == 0
            mock_run_tmt.assert_called_once()


def test_drivers_tmt_cli_with_options():
    """Test the CLI functionality with various options"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        runner = CliRunner()
        cli = client.cli()

        # Test with --forward-ssh flag
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0  # Success return code
            result = runner.invoke(cli, ["--forward-ssh", "test"])
            assert result.exit_code == 0
            mock_run_tmt.assert_called_once()

        # Test with custom username and password
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0  # Success return code
            result = runner.invoke(
                cli, ["--tmt-username", "custom_user", "--tmt-password", "custom_pass", "test"]
            )
            assert result.exit_code == 0
            mock_run_tmt.assert_called_once()

        # Test with custom tmt command
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0  # Success return code
            result = runner.invoke(cli, ["--tmt-cmd", "custom-tmt", "test"])
            assert result.exit_code == 0
            mock_run_tmt.assert_called_once()


def test_drivers_tmt_cli_error_handling():
    """Test CLI error handling when TMT command fails"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        runner = CliRunner()
        cli = client.cli()

        # Test CLI with non-zero return code
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 1  # Error return code
            result = runner.invoke(cli, ["test"])
            assert result.exit_code == 1
            mock_run_tmt.assert_called_once()


def test_drivers_tmt_cli_tmt_on_exporter():
    """Test CLI with --tmt-on-exporter flag"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        runner = CliRunner()
        cli = client.cli()

        # Test CLI with --tmt-on-exporter flag (should abort)
        result = runner.invoke(cli, ["--tmt-on-exporter", "test"])
        assert result.exit_code == 1  # click.Abort() returns exit code 1
        assert "TMT will be run on the exporter" in result.output
        assert "Aborted!" in result.output


def test_drivers_tmt_client_methods():
    """Test the client methods directly"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        # Test run_tmt method with default parameters
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0
            result = client.run_tmt_local(False, "tmt", None, None, [])
            assert result == 0
            mock_run_tmt.assert_called_once()

        # Test run_tmt method with custom parameters
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0
            result = client.run_tmt_local(True, "custom-tmt", "user", "pass", ["arg1", "arg2"])
            assert result == 0
            mock_run_tmt.assert_called_once()


def test_drivers_tmt_run_tmt_with_forward_ssh():
    """Test run_tmt method with SSH forwarding"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        with patch('jumpstarter_driver_tmt.client.TcpPortforwardAdapter') as mock_adapter:
            mock_adapter.return_value.__enter__.return_value = ("localhost", 2222)
            with patch.object(client, '_run_tmt_local') as mock_run_tmt:
                    mock_run_tmt.return_value = 0
                    result = client.run_tmt_local(True, "tmt", "user", "pass", ["arg1"])
                    assert result == 0
                    mock_run_tmt.assert_called_once_with(
                        "localhost", 2222, "tmt", "user", "pass", "", ["arg1"]
                    )


def test_drivers_tmt_run_tmt_with_direct_address():
    """Test run_tmt method with direct address connection"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0
            result = client.run_tmt_local(False, "tmt", "user", "pass", ["arg1"])
            assert result == 0
            mock_run_tmt.assert_called_once_with(
                "127.0.0.1", 22, "tmt", "user", "pass", "", ["arg1"]
            )


def test_drivers_tmt_run_tmt_fallback_to_forwarding():
    """Test run_tmt method fallback to SSH forwarding when address() fails"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        # Mock ssh.address() to raise DriverMethodNotImplemented
        from jumpstarter.client.core import DriverMethodNotImplemented
        client.ssh.address = MagicMock(side_effect=DriverMethodNotImplemented("Method not implemented"))

        with patch('jumpstarter_driver_tmt.client.TcpPortforwardAdapter') as mock_adapter:
            mock_adapter.return_value.__enter__.return_value = ("localhost", 2222)
            with patch.object(client, '_run_tmt_local') as mock_run_tmt:
                    mock_run_tmt.return_value = 0
                    result = client.run_tmt_local(False, "tmt", "user", "pass", ["arg1"])
                    assert result == 0
                    mock_run_tmt.assert_called_once_with(
                        "localhost", 2222, "tmt", "user", "pass", "", ["arg1"]
                    )


def test_drivers_tmt_run_tmt_internal():
    """Test the internal _run_tmt method"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        with patch('subprocess.run') as mock_subprocess:
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_subprocess.return_value = mock_result

            result = client._run_tmt_local("localhost", 2222, "tmt", "user", "pass", "j power cycle", ["test", "arg"])

            assert result == 0
            mock_subprocess.assert_called_once()
            # Verify the command and args passed to subprocess.run
            call_args = mock_subprocess.call_args[0][0]
            assert call_args[0] == "tmt"
            assert "test" in call_args
            assert "arg" in call_args


def test_drivers_tmt_run_tmt_internal_with_error():
    """Test the internal _run_tmt method with error return code"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        with patch('subprocess.run') as mock_subprocess:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_subprocess.return_value = mock_result

            result = client._run_tmt_local("localhost", 2222, "tmt", "user", "pass", "j power cycle", ["test"])

            assert result == 1
            mock_subprocess.assert_called_once()


def test_drivers_tmt_driver_exports():
    """Test the driver export methods"""
    instance = TMT(
        children={"ssh": TcpNetwork(host="127.0.0.1", port=22)},
        reboot_cmd="custom reboot",
        default_username="testuser",
        default_password="testpass"
    )

    with serve(instance) as client:
        # Test get_reboot_cmd
        reboot_cmd = client.call("get_reboot_cmd")
        assert reboot_cmd == "custom reboot"

        # Test get_default_user_pass
        username, password = client.call("get_default_user_pass")
        assert username == "testuser"
        assert password == "testpass"


def test_drivers_tmt_driver_defaults():
    """Test the driver with default values"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        # Test default reboot_cmd
        reboot_cmd = client.call("get_reboot_cmd")
        assert reboot_cmd == ""

        # Test default username and password
        username, password = client.call("get_default_user_pass")
        assert username == ""
        assert password == ""


def test_drivers_tmt_configuration_error():
    """Test that ConfigurationError is raised when ssh child is missing"""
    from jumpstarter.common.exceptions import ConfigurationError

    with pytest.raises(ConfigurationError, match="'ssh' child is required"):
        TMT(children={})


def test_replace_provision_args_no_provision():
    """Test replace_provision_args when no provision section exists"""
    args = ["discover", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "j power cycle")

    expected = ["discover", "prepare", "execute"]
    assert result == expected
    logger.debug.assert_called_with("Provision or run section not found, ignoring")


def test_replace_provision_args_with_provision():
    """Test replace_provision_args when provision section exists"""
    args = ["discover", "provision", "-h", "old_host", "-g", "old_ip", "-P", "old_port", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "new_host", 2222, "new_user", "new_pass", "j power cycle")

    expected = [
        "discover", "provision", "-h", "connect", "-g", "new_host", "-P", "2222",
        "-u", "new_user", "-p", "new_pass", "--feeling-safe", "--hard-reboot", "j power cycle", "prepare", "execute"
    ]
    assert result == expected


def test_replace_provision_args_without_username_password():
    """Test replace_provision_args without username and password"""
    args = ["discover", "provision", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, None, None, "j power cycle")

    expected = [
        "discover", "provision", "-h", "connect", "-g", "host", "-P", "22",
        "--feeling-safe", "--hard-reboot", "j power cycle", "prepare", "execute"
    ]
    assert result == expected


def test_replace_provision_args_complex():
    """Test replace_provision_args with complex provision section"""
    args = [
        "--root", ".", "-c", "tracing=off", "provision", "-h", "connect", "-g", "192.168.1.1",
        "-P", "22", "-u", "root", "-p", "password", "prepare", "execute"
    ]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "new_host", 2222, "new_user", "new_pass", "j power cycle")

    expected = [
        "--root", ".", "-c", "tracing=off", "provision", "-h", "connect", "-g", "new_host",
        "-P", "2222", "-u", "new_user", "-p", "new_pass", "--feeling-safe", "--hard-reboot", "j power cycle", "prepare",
        "execute"
    ]
    assert result == expected


def test_replace_provision_args_with_tmt_run_commands():
    """Test replace_provision_args with TMT run commands in provision section"""
    args = ["provision", "plan", "test", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "j power cycle")

    expected = [
        "provision", "-h", "connect", "-g", "host", "-P", "22", "-u", "user", "-p", "pass",
        "--feeling-safe", "--hard-reboot", "j power cycle", "plan", "test", "execute"
    ]
    assert result == expected


def test_replace_provision_args_with_run_command():
    """Test replace_provision_args with 'run' command (should add provision)"""
    args = ["discover", "run", "test", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "j power cycle")

    expected = [
        "discover", "run", "test", "execute", "provision", "-h", "connect", "-g", "host",
        "-P", "22", "-u", "user", "-p", "pass", "--feeling-safe", "--hard-reboot", "j power cycle"
    ]
    assert result == expected
    logger.debug.assert_called_with("Run section found, adding provision arguments")


def test_replace_provision_args_no_provision_no_run():
    """Test replace_provision_args with no provision or run section (should ignore)"""
    args = ["discover", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "j power cycle")

    expected = ["discover", "prepare", "execute"]
    assert result == expected
    logger.debug.assert_called_with("Provision or run section not found, ignoring")


def test_replace_provision_args_with_hard_reboot():
    """Test replace_provision_args with hard reboot command"""
    args = ["discover", "provision", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "custom reboot command")

    expected = [
        "discover", "provision", "-h", "connect", "-g", "host", "-P", "22",
        "-u", "user", "-p", "pass", "--feeling-safe", "--hard-reboot", "custom reboot command", "prepare", "execute"
    ]
    assert result == expected


def test_replace_provision_args_without_hard_reboot():
    """Test replace_provision_args without hard reboot command (empty string)"""
    args = ["discover", "provision", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", "")

    expected = [
        "discover", "provision", "-h", "connect", "-g", "host", "-P", "22",
        "-u", "user", "-p", "pass", "prepare", "execute"
    ]
    assert result == expected


def test_replace_provision_args_without_hard_reboot_none():
    """Test replace_provision_args without hard reboot command (None)"""
    args = ["discover", "provision", "prepare", "execute"]
    logger = MagicMock()
    result = replace_provision_args(logger, args, "host", 22, "user", "pass", None)

    expected = [
        "discover", "provision", "-h", "connect", "-g", "host", "-P", "22",
        "-u", "user", "-p", "pass", "prepare", "execute"
    ]
    assert result == expected


def test_drivers_tmt_logging_functionality():
    """Test logging functionality in TMT client"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        with patch('jumpstarter_driver_tmt.client.TcpPortforwardAdapter') as mock_adapter:
            mock_adapter.return_value.__enter__.return_value = ("localhost", 2222)
            with patch.object(client, '_run_tmt_local') as mock_run_tmt:
                mock_run_tmt.return_value = 0

                # Test that debug logging is called for SSH port forwarding
                with patch.object(client.logger, 'debug') as mock_debug:
                    client.run_tmt_local(True, "tmt", "user", "pass", ["arg1"])
                    mock_debug.assert_any_call("Using SSH port forwarding for TMT connection")
                    mock_debug.assert_any_call("SSH port forward established - host: localhost, port: 2222")

        # Test that debug logging is called for direct connection
        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0
            with patch.object(client.logger, 'debug') as mock_debug:
                client.run_tmt_local(False, "tmt", "user", "pass", ["arg1"])
                mock_debug.assert_any_call("Using direct SSH connection for tmt - host: 127.0.0.1, port: 22")

        # Test that warning logging is called for fallback
        from jumpstarter.client.core import DriverMethodNotImplemented
        client.ssh.address = MagicMock(side_effect=DriverMethodNotImplemented("Method not implemented"))

        with patch('jumpstarter_driver_tmt.client.TcpPortforwardAdapter') as mock_adapter:
            mock_adapter.return_value.__enter__.return_value = ("localhost", 2222)
            with patch.object(client, '_run_tmt_local') as mock_run_tmt:
                mock_run_tmt.return_value = 0
                with patch.object(client.logger, 'warning') as mock_warning:
                    client.run_tmt_local(False, "tmt", "user", "pass", ["arg1"])
                    mock_warning.assert_called_once_with(
                        "Direct address connection failed (Method not implemented), falling back to SSH port forwarding"
                    )


def test_drivers_tmt_cli_logging():
    """Test logging in CLI functionality"""
    instance = TMT(children={"ssh": TcpNetwork(host="127.0.0.1", port=22)})

    with serve(instance) as client:
        runner = CliRunner()
        cli = client.cli()

        with patch.object(client, '_run_tmt_local') as mock_run_tmt:
            mock_run_tmt.return_value = 0
            with patch.object(client.logger, 'debug') as mock_debug:
                result = runner.invoke(cli, ["test"])
                assert result.exit_code == 0
                mock_debug.assert_called_with("TMT result: 0")
