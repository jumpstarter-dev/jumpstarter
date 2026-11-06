
import os
import signal
import time

import pytest
from anyio import CancelScope

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


def _alive(pid):
    """Whether pid is running; a zombie waiting to be reaped is not."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    if not os.path.isdir("/proc"):
        return True
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, ProcessLookupError):
        # Reaped since the kill() above
        return False


def _wait_until_gone(pid, timeout=10):
    deadline = time.monotonic() + timeout
    while _alive(pid):
        assert time.monotonic() < deadline, f"process {pid} still running after {timeout}s"
        time.sleep(0.05)


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


def test_cli_returns_the_method_return_code_as_an_int():
    """j exits with what the CLI returns, which must be an int: driver results arrive as floats"""
    shell = Shell(methods={"exit2": "exit 2", "timeout": {"command": "sleep 30", "timeout": 1}})

    with serve(shell) as client:
        cli = client.cli()
        for method, expected in (("exit2", 2), ("timeout", 199)):
            result = cli([method], standalone_mode=False)
            assert type(result) is int and result == expected, f"{method}: {result!r}"


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


def test_methods_description_populated():
    """Test that methods_description is populated from methods configuration"""
    shell = Shell(
        methods={
            "method1": {
                "command": "echo",
                "description": "Custom description for method1"
            },
            "method2": "ls",  # String format, no description in methods_description
        }
    )

    with serve(shell) as client:
        # Test that custom descriptions are in methods_description
        assert "method1" in client.methods_description
        assert client.methods_description["method1"] == "Custom description for method1"

        # Test that string-format methods are not in methods_description
        # (will fall back to default in client)
        assert "method2" not in client.methods_description


def test_blocked_env_vars(client):
    """Test that known-dangerous environment variables are rejected"""
    blocked_exact = ["LD_PRELOAD", "LD_LIBRARY_PATH", "PATH", "PYTHONPATH",
                     "BASH_ENV", "KUBECONFIG", "HOME"]
    for var in blocked_exact:
        with pytest.raises(Exception, match="blocked for security reasons"):
            _collect_streaming_output(client, "env", {var: "malicious"})


def test_blocked_env_var_prefixes(client):
    """Test that env vars with dangerous prefixes are rejected"""
    blocked_prefixed = ["LD_AUDIT", "LD_DEBUG", "BASH_FUNC_myfunc"]
    for var in blocked_prefixed:
        with pytest.raises(Exception, match="blocked for security reasons"):
            _collect_streaming_output(client, "env", {var: "malicious"})


def test_safe_env_vars_allowed(client):
    """Test that legitimate environment variables still work"""
    stdout, _stderr, returncode = _collect_streaming_output(client, "env", {"ENV1": "safe_value"})
    assert stdout == "safe_value\n"
    assert returncode == 0


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


@pytest.mark.parametrize(
    "command",
    [
        "sleep 60 & echo $!; wait",
        # Ignores SIGTERM and no longer holds the method's output, so only SIGKILL stops it
        "( trap '' TERM; exec sleep 60 ) >/dev/null 2>&1 & echo $!; wait",
    ],
)
def test_cancelled_call_kills_the_process_group(command):
    """Cancelling a call before its script ends, as a client disconnect or Ctrl-C does, kills its process group"""
    shell = Shell(methods={"hold": command})

    async def start_then_cancel(client):
        pid = None
        with CancelScope() as scope:
            async for stdout, _stderr, _returncode in client.streamingcall_async("call_method", "hold", {}):
                pid = int(stdout)
                # Delivered while the client waits for the next chunk, which cancels the RPC
                scope.cancel()
        return pid

    with serve(shell) as client:
        pid = client.portal.call(start_then_cancel, client)
        try:
            _wait_until_gone(pid)
        finally:
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)


def test_timeout_does_not_wait_for_unread_output():
    """A timed-out method that filled its output pipe is cleaned up without waiting out the grace period"""
    shell = Shell(methods={"chatty": {"command": "head -c 600000 /dev/zero | tr '\\0' a", "timeout": 1}})

    with serve(shell) as client:
        start = time.monotonic()
        _stdout, stderr, returncode = _collect_streaming_output(client, "chatty")
        elapsed = time.monotonic() - start

    assert returncode == 199, stderr
    assert elapsed < 4, f"cleanup took {elapsed:.1f}s"
