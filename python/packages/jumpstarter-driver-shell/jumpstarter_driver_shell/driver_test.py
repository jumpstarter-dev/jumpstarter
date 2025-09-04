
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

    # Test that we can get the echo command
    echo_command = cli.commands.get('echo')
    assert echo_command is not None
    assert echo_command.name == 'echo'


def test_cli_includes_all_methods():
    """Test that CLI includes all methods"""
    from .driver import Shell
    from jumpstarter.common.utils import serve

    shell_instance = Shell(
        log_level="DEBUG",
        methods={
            "method1": "echo method1",
            "method2": "echo method2",
            "method3": "echo method3",
        },
    )

    with serve(shell_instance) as test_client:
        cli = test_client.cli()
        available_commands = set(cli.commands.keys())

        # All methods should be available
        expected_methods = {"method1", "method2", "method3"}
        assert available_commands == expected_methods, f"Expected {expected_methods}, got {available_commands}"


def test_cli_exit_codes():
    """Test that CLI commands preserve shell command exit codes"""
    import click

    from .driver import Shell
    from jumpstarter.common.utils import serve

    # Create a shell instance with methods that have different exit codes
    shell_instance = Shell(
        log_level="DEBUG",
        methods={
            "success": "exit 0",
            "fail_1": "exit 1",
            "fail_42": "exit 42",
        },
    )

    with serve(shell_instance) as test_client:
        cli = test_client.cli()

        # Test successful command (exit 0) - should not raise
        success_cmd = cli.commands['success']
        try:
            success_cmd.callback([], [])  # Call with empty args and env
        except click.exceptions.Exit:
            raise AssertionError("Success command should not raise Exit exception") from None

        # Test command that exits with code 1 - should raise Exit(1)
        fail1_cmd = cli.commands['fail_1']
        try:
            fail1_cmd.callback([], [])
            raise AssertionError("Command should have raised Exit exception")
        except click.exceptions.Exit as e:
            assert e.exit_code == 1

        # Test command that exits with code 42 - should raise Exit(42)
        fail42_cmd = cli.commands['fail_42']
        try:
            fail42_cmd.callback([], [])
            raise AssertionError("Command should have raised Exit exception")
        except click.exceptions.Exit as e:
            assert e.exit_code == 42
