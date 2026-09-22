"""
CLI tests for PySerial driver.

Tests the Click CLI interface including the pipe command.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import click
import pytest
from anyio import BrokenResourceError, EndOfStream, Event, fail_after
from click.testing import CliRunner

from .driver import PySerial
from jumpstarter.common.utils import serve


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def pyserial_client():
    """Fixture to create a PySerial client with loop:// URL for testing."""
    instance = PySerial(url="loop://")
    with serve(instance) as client:
        yield client


def test_pipe_command_append_requires_output(pyserial_client):
    """Test that --append requires --output."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    # Mock the portal to prevent actual execution
    with patch.object(pyserial_client, "portal"):
        result = runner.invoke(cli, ["pipe", "--append"])
        assert result.exit_code != 0
        assert "--append requires --output" in result.output


def test_pipe_command_input_and_no_input_conflict(pyserial_client):
    """Test that --input and --no-input cannot be used together."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    # Mock the portal to prevent actual execution
    with patch.object(pyserial_client, "portal"):
        result = runner.invoke(cli, ["pipe", "--input", "--no-input"])
        assert result.exit_code != 0
        assert "Cannot use both --input and --no-input" in result.output


def test_pipe_command_with_output_file(pyserial_client):
    """Test pipe command with output file option."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with runner.isolated_filesystem(), patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt  # Simulate Ctrl+C to exit

        # Use --no-input to explicitly disable input detection
        runner.invoke(cli, ["pipe", "-o", "test.log", "--no-input"])

        # Should have attempted to call _pipe_serial
        assert mock_call.called
        # Check the arguments passed
        args = mock_call.call_args[0]
        assert args[1] == "test.log"  # output file
        assert args[2] is False  # input_enabled
        assert args[3] is False  # append


def test_pipe_command_with_append(pyserial_client):
    """Test pipe command with append option."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with runner.isolated_filesystem(), patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe", "-o", "test.log", "-a"])

        assert mock_call.called
        args = mock_call.call_args[0]
        assert args[1] == "test.log"  # output file
        assert args[3] is True  # append


def test_pipe_command_with_input_flag(pyserial_client):
    """Test pipe command with --input flag."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe", "-i"])

        assert mock_call.called
        args = mock_call.call_args[0]
        assert args[2] is True  # input_enabled


def test_pipe_command_with_no_input_flag(pyserial_client):
    """Test pipe command with --no-input flag."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe", "--no-input"])

        assert mock_call.called
        args = mock_call.call_args[0]
        assert args[2] is False  # input_enabled


def test_pipe_command_with_no_output_flag(pyserial_client):
    """Test pipe command with --no-output flag."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe", "--no-output", "--input"])

        assert mock_call.called
        args = mock_call.call_args[0]
        assert args[2] is True  # input_enabled
        assert args[4] is True  # no_output


def test_pipe_command_no_output_conflict_with_output(pyserial_client):
    """Test that --no-output and --output cannot be used together."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client, "portal"):
        result = runner.invoke(cli, ["pipe", "--no-output", "-o", "test.log"])
        assert result.exit_code != 0
        assert "Cannot use both --no-output and --output" in result.output


def test_pipe_command_no_output_conflict_with_append(pyserial_client):
    """Test that --no-output and --append cannot be used together."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client, "portal"):
        result = runner.invoke(cli, ["pipe", "--no-output", "--append"])
        assert result.exit_code != 0
        assert "Cannot use both --no-output and --append" in result.output


def test_pipe_command_no_output_requires_input(pyserial_client):
    """Test that --no-output requires stdin input."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client, "portal"):
        result = runner.invoke(cli, ["pipe", "--no-output", "--no-input"])
        assert result.exit_code != 0
        assert "--no-output requires stdin input" in result.output


def test_pipe_command_stdin_auto_detection(pyserial_client):
    """Test that pipe command auto-detects piped stdin with CliRunner."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    # CliRunner doesn't provide a TTY by default, so stdin.isatty() returns False
    # This simulates the behavior when stdin is piped
    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe"])

        assert mock_call.called
        args = mock_call.call_args[0]
        # Should auto-enable input when stdin is not a TTY (CliRunner default behavior)
        assert args[2] is True  # input_enabled


def test_pipe_command_no_auto_detection_with_no_input_flag(pyserial_client):
    """Test that pipe command doesn't enable input with --no-input flag."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        runner.invoke(cli, ["pipe", "--no-input"])

        assert mock_call.called
        args = mock_call.call_args[0]
        # Should NOT enable input when --no-input is specified
        assert args[2] is False  # input_enabled


def test_pipe_command_status_messages(pyserial_client):
    """Test that pipe command prints appropriate status messages."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        # Test read-only mode (with --no-input flag)
        result = runner.invoke(cli, ["pipe", "--no-input"])
        assert "Reading from serial port" in result.output
        assert "Ctrl+C to exit" in result.output

        # Test bidirectional mode (CliRunner stdin is not a TTY, so it auto-detects)
        result = runner.invoke(cli, ["pipe"])
        assert "Bidirectional mode" in result.output or "auto-detected" in result.output

        # Test fire-and-forget mode
        result = runner.invoke(cli, ["pipe", "--no-output", "--input"])
        assert "Fire-and-forget mode" in result.output


def test_pipe_command_with_file_and_input(pyserial_client):
    """Test pipe command with both file output and input."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with runner.isolated_filesystem(), patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        with patch("sys.stdin.isatty", return_value=False):
            runner.invoke(cli, ["pipe", "-o", "test.log"])

            assert mock_call.called
            args = mock_call.call_args[0]
            assert args[1] == "test.log"  # output file
            assert args[2] is True  # input_enabled (auto-detected)
            assert args[3] is False  # append


def test_pipe_command_keyboard_interrupt_handling(pyserial_client):
    """Test that pipe command handles KeyboardInterrupt gracefully."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        result = runner.invoke(cli, ["pipe"])

        # Should exit cleanly after KeyboardInterrupt
        assert "Stopped" in result.output or result.exit_code == 0


def test_pipe_command_mode_descriptions(pyserial_client):
    """Test that pipe command shows correct mode descriptions."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    with patch.object(pyserial_client.portal, "call") as mock_call:
        mock_call.side_effect = KeyboardInterrupt

        # Test auto-detected mode (CliRunner stdin is not a TTY)
        result = runner.invoke(cli, ["pipe"])
        assert "auto-detected" in result.output.lower()

        # Test forced input mode
        result = runner.invoke(cli, ["pipe", "-i"])
        assert "forced input" in result.output.lower() or "bidirectional" in result.output.lower()

        # Test read-only mode (with --no-input flag)
        result = runner.invoke(cli, ["pipe", "--no-input"])
        assert "read-only" in result.output.lower()


def test_pipe_observe_ignores_redirected_stdin(pyserial_client):
    with patch.object(pyserial_client.portal, "call") as call:
        result = CliRunner().invoke(pyserial_client.cli(), ["pipe", "--observe"], input="command\n")

    assert result.exit_code == 0
    call.assert_called_once()
    assert call.call_args.args[2] is False
    assert call.call_args.args[5] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("observe", "input_enabled", "expected_method"),
    [(False, False, "observe"), (True, False, "observe"), (False, True, "connect")],
)
async def test_pipe_serial_selects_stream_mode(pyserial_client, observe, input_enabled, expected_method):
    methods = []
    stdin_started = Event()

    @asynccontextmanager
    async def stream_async(*, method):
        methods.append(method)
        yield object()

    async def serial_to_output(*_args):
        if input_enabled:
            with fail_after(1):
                await stdin_started.wait()

    async def stdin_to_serial(_stream):
        stdin_started.set()
        return (0, 0)

    with (
        patch.object(pyserial_client, "stream_async", side_effect=stream_async),
        patch.object(
            pyserial_client, "_serial_to_output", new_callable=AsyncMock, side_effect=serial_to_output
        ) as output,
        patch.object(pyserial_client, "_stdin_to_serial", new_callable=AsyncMock, side_effect=stdin_to_serial) as stdin,
    ):
        await pyserial_client._pipe_serial(input_enabled=input_enabled, observe=observe)

    assert methods == [expected_method]
    output.assert_awaited_once()
    assert stdin.await_count == int(input_enabled)


@pytest.mark.parametrize("held", [False, True])
def test_console_status_reports_write_token_without_identity(pyserial_client, held):
    status = {
        "write_token_held": held,
        "observer_count": 2,
        "total_clients": 3,
        "reader_running": True,
        "scrollback_bytes": 12,
    }
    with patch.object(pyserial_client, "call", return_value=status):
        result = CliRunner().invoke(pyserial_client.cli(), ["console-status"])

    assert result.exit_code == 0
    assert f"Write token held: {'yes' if held else 'no'}" in result.output
    assert "Observers: 2" in result.output
    assert "Total clients: 3" in result.output
    assert "Reader running: True" in result.output
    assert "Scrollback: 12 bytes" in result.output


def test_console_command_structure(pyserial_client):
    """Test that console command has the correct structure and start-console alias exists."""
    cli = pyserial_client.cli()

    # Primary command is "console"
    console_cmd = cli.commands["console"]
    assert console_cmd is not None
    assert hasattr(console_cmd, "callback")

    # Backward-compat alias "start-console" should also exist (hidden)
    alias_cmd = cli.commands["start-console"]
    assert alias_cmd is not None
    assert alias_cmd.hidden is True


def test_cli_base_command(pyserial_client):
    """Test that base CLI command works."""
    runner = CliRunner()
    cli = pyserial_client.cli()

    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Serial port client" in result.output or "Commands:" in result.output


@pytest.mark.anyio
async def test_serial_to_output_handles_end_of_stream(pyserial_client):
    """Test that _serial_to_output handles EndOfStream gracefully."""
    # Create a mock stream that raises EndOfStream
    mock_stream = AsyncMock()
    mock_stream.receive.side_effect = EndOfStream

    # Capture the click output
    from click.testing import CliRunner

    runner = CliRunner()
    with runner.isolated_filesystem():
        # Call _serial_to_output directly
        await pyserial_client._serial_to_output(mock_stream, None, False)

    # The method should have caught EndOfStream and not raised it
    # (we're testing that it doesn't propagate)


@pytest.mark.anyio
async def test_serial_to_output_handles_broken_resource(pyserial_client):
    """A broken serial stream must fail the command."""
    # Create a mock stream that raises BrokenResourceError
    mock_stream = AsyncMock()
    mock_stream.receive.side_effect = BrokenResourceError

    with pytest.raises(click.ClickException, match="Serial connection lost"):
        await pyserial_client._serial_to_output(mock_stream, None, False)


@pytest.mark.anyio
async def test_serial_to_output_end_of_stream_with_file(pyserial_client):
    """Test that _serial_to_output handles EndOfStream when writing to file."""
    # Create a mock stream that raises EndOfStream
    mock_stream = AsyncMock()
    mock_stream.receive.side_effect = EndOfStream

    from click.testing import CliRunner

    runner = CliRunner()
    with runner.isolated_filesystem():
        await pyserial_client._serial_to_output(mock_stream, "test.log", False)

        # The file should have been created (even if empty)
        import os

        assert os.path.exists("test.log")


@pytest.mark.anyio
async def test_serial_to_output_broken_resource_with_file(pyserial_client):
    """Test that _serial_to_output handles BrokenResourceError when writing to file."""
    # Create a mock stream that raises BrokenResourceError
    mock_stream = AsyncMock()
    mock_stream.receive.side_effect = BrokenResourceError

    from click.testing import CliRunner

    runner = CliRunner()
    with runner.isolated_filesystem():
        with pytest.raises(click.ClickException, match="Serial connection lost"):
            await pyserial_client._serial_to_output(mock_stream, "test.log", False)

        # The file should have been created (even if empty)
        import os

        assert os.path.exists("test.log")


@pytest.mark.anyio
async def test_serial_to_output_receives_data_then_end_of_stream(pyserial_client):
    """Test that _serial_to_output successfully receives data before EndOfStream."""
    # Create a mock stream that returns data then raises EndOfStream
    mock_stream = AsyncMock()
    mock_stream.receive.side_effect = [b"Hello", b"World", EndOfStream]

    from click.testing import CliRunner

    runner = CliRunner()
    with runner.isolated_filesystem():
        # Call _serial_to_output with a file output
        await pyserial_client._serial_to_output(mock_stream, "test.log", False)

        # Verify the file contains the data
        with open("test.log", "rb") as f:  # noqa: ASYNC230
            content = f.read()
            assert content == b"HelloWorld"
