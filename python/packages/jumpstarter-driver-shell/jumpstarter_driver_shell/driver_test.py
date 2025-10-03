
import pytest

from .driver import Shell
from jumpstarter.common.utils import serve


def _collect_streaming_output(client, method_name, env_vars=None, *args):
    """Helper function to collect streaming output for testing"""
    stdout_parts = []
    stderr_parts = []
    final_returncode = None

    env_vars = env_vars or {}
    for stdout_chunk, stderr_chunk, returncode in client.streamingcall("call_method", method_name, env_vars, *args):
        if stdout_chunk:
            stdout_parts.append(stdout_chunk)
        if stderr_chunk:
            stderr_parts.append(stderr_chunk)
        if returncode is not None:
            final_returncode = returncode

    return "".join(stdout_parts), "".join(stderr_parts), final_returncode


@pytest.fixture
def client():
    instance = Shell(
        log_level="DEBUG",
        methods={
            "echo": "echo $1",
            "env": "echo $ENV1",
            "multi_line": "echo $1\necho $2\necho $3",
            "exit1": "exit 1",
            "stderr": "echo $1 >&2",
        },
    )
    with serve(instance) as client:
        yield client


def test_normal_args(client):
    stdout, stderr, returncode = _collect_streaming_output(client, "echo", {}, "hello")
    assert stdout == "hello\n"
    assert stderr == ""
    assert returncode == 0


def test_env_vars(client):
    stdout, stderr, returncode = _collect_streaming_output(client, "env", {"ENV1": "world"})
    assert stdout == "world\n"
    assert stderr == ""
    assert returncode == 0


def test_multi_line_scripts(client):
    stdout, stderr, returncode = _collect_streaming_output(client, "multi_line", {}, "a", "b", "c")
    assert stdout == "a\nb\nc\n"
    assert stderr == ""
    assert returncode == 0


def test_return_codes(client):
    stdout, stderr, returncode = _collect_streaming_output(client, "exit1")
    assert stdout == ""
    assert stderr == ""
    assert returncode == 1


def test_stderr(client):
    stdout, stderr, returncode = _collect_streaming_output(client, "stderr", {}, "error")
    assert stdout == ""
    assert stderr == "error\n"
    assert returncode == 0


def test_unknown_method(client):
    try:
        client.unknown()
    except AttributeError as e:
        assert "method unknown not found in" in str(e)
    else:
        raise AssertionError("Expected AttributeError")


def test_cli_interface(client):
    """Test that the CLI interface is created with all methods"""
    cli = client.cli()

    # Check that it's a Click group
    assert hasattr(cli, 'commands')

    # Check that all configured methods are available as commands
    expected_methods = {"echo", "env", "multi_line", "exit1", "stderr"}
    available_commands = set(cli.commands.keys())

    assert expected_methods == available_commands, f"Expected {expected_methods}, got {available_commands}"


def test_cli_method_execution(client):
    """Test that CLI methods can be executed"""
    cli = client.cli()

    # Test that CLI commands exist and have the correct structure
    echo_command = cli.commands['echo']
    assert echo_command.name == 'echo'


def test_cli_includes_all_methods():
    """Test that CLI includes all configured methods with proper names"""
    shell = Shell(
        methods={
            "echo": "echo",
            "env": "echo $ENV_VAR",
            "multi_line": "echo line1\necho line2",
            "exit1": "exit 1",
            "stderr": "echo 'error' 1>&2",
        }
    )

    with serve(shell) as client:
        cli = client.cli()

        # Check that all methods are in the CLI
        expected_methods = {"echo", "env", "multi_line", "exit1", "stderr"}
        available_commands = set(cli.commands.keys())

        assert available_commands == expected_methods, f"Expected {expected_methods}, got {available_commands}"


def test_cli_exit_codes():
    """Test that CLI methods correctly exit with shell command return codes"""
    shell = Shell(
        methods={
            "exit0": "exit 0",
            "exit1": "exit 1",
            "exit42": "exit 42",
        }
    )

    with serve(shell) as client:
        # Test successful command (exit 0)
        returncode = client.exit0()
        assert returncode == 0

        # Test failed command (exit 1)
        returncode = client.exit1()
        assert returncode == 1

        # Test custom exit code (exit 42)
        returncode = client.exit42()
        assert returncode == 42


def test_cli_custom_descriptions_unified_format():
    """Test that CLI methods use custom descriptions with unified format"""
    shell = Shell(
        methods={
            "echo": {
                "command": "echo",
                "description": "Custom echo description"
            },
            "test_method": {
                "command": "echo 'test'",
                "description": "Test method with custom description"
            },
        }
    )

    with serve(shell) as client:
        cli = client.cli()

        # Check that custom descriptions are used
        echo_cmd = cli.commands['echo']
        assert echo_cmd.help == "Custom echo description", f"Expected custom description, got {echo_cmd.help}"

        test_cmd = cli.commands['test_method']
        assert test_cmd.help == "Test method with custom description"


def test_cli_default_descriptions():
    """Test that CLI methods use default descriptions when not configured"""
    shell = Shell(
        methods={
            "echo": "echo",
            "test_method": "echo 'test'",
        }
        # No descriptions configured
    )

    with serve(shell) as client:
        cli = client.cli()

        # Check that default descriptions are used
        echo_cmd = cli.commands['echo']
        assert echo_cmd.help == "Execute the echo shell method"

        test_cmd = cli.commands['test_method']
        assert test_cmd.help == "Execute the test_method shell method"


def test_get_method_description_unified():
    """Test the get_method_description export method with unified format"""
    shell = Shell(
        methods={
            "method1": {
                "command": "echo",
                "description": "Custom description for method1"
            },
            "method2": "ls",  # String format, should use default description
        }
    )

    with serve(shell) as client:
        # Test with custom description (unified format)
        assert client.call("get_method_description", "method1") == "Custom description for method1"

        # Test with default description (string format)
        assert client.call("get_method_description", "method2") == "Execute the method2 shell method"


def test_mixed_format_methods():
    """Test that both string and dict formats work together"""
    shell = Shell(
        methods={
            "simple": "echo 'simple'",
            "detailed": {
                "command": "echo 'detailed'",
                "description": "A detailed command with description"
            },
            "default_cmd": {
                # No command specified - should use default "echo Hello"
                "description": "Method using default command"
            }
        }
    )

    with serve(shell) as client:
        # Test string format works
        returncode = client.simple()
        assert returncode == 0

        # Test dict format works
        returncode = client.detailed()
        assert returncode == 0

        # Test default command works
        returncode = client.default_cmd()
        assert returncode == 0

        # Test CLI descriptions
        cli = client.cli()
        assert cli.commands['simple'].help == "Execute the simple shell method"
        assert cli.commands['detailed'].help == "A detailed command with description"
        assert cli.commands['default_cmd'].help == "Method using default command"
