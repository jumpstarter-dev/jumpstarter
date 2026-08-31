"""Tests for opt.py utilities."""

import io
import logging
import sys

import click
import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.logging import RichHandler

from jumpstarter_cli_common.opt import (
    SourcePrefixFormatter,
    _opt_log_level_callback,
    opt_insecure_tls,
    validate_name,
)


class TestSourcePrefixFormatter:
    def test_prefix_at_beginning_of_message(self) -> None:
        """Issue A3: [exporter:beforeLease] prefix should appear at beginning of message.

        The SourcePrefixFormatter should prepend [logger_name] to the first
        message from a new source, ensuring the prefix appears at the start
        of the line, not appended at the end.
        """
        formatter = SourcePrefixFormatter()

        record = logging.LogRecord(
            name="exporter:beforeLease",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Powering on",
            args=None,
            exc_info=None,
        )
        formatted = formatter.format(record)

        assert "[exporter:beforeLease] Powering on" in formatted

    def test_prefix_omitted_on_consecutive_same_source(self) -> None:
        """Issue A4: [exporter:beforeLease] prefix should not repeat on every line.

        The SourcePrefixFormatter should only show the [logger_name] prefix on
        the first line of a consecutive same-source block. Subsequent lines
        from the same source should omit the prefix to reduce noise.
        """
        formatter = SourcePrefixFormatter()

        # First message from a source - should have prefix
        record1 = logging.LogRecord(
            name="exporter:beforeLease",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Line 1",
            args=None,
            exc_info=None,
        )
        formatted1 = formatter.format(record1)
        assert "[exporter:beforeLease]" in formatted1

        # Second message from same source - should NOT have prefix
        record2 = logging.LogRecord(
            name="exporter:beforeLease",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Line 2",
            args=None,
            exc_info=None,
        )
        formatted2 = formatter.format(record2)
        assert "[exporter:beforeLease]" not in formatted2
        assert "Line 2" in formatted2

        # Third message from different source - should have new prefix
        record3 = logging.LogRecord(
            name="different.source",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Line 3",
            args=None,
            exc_info=None,
        )
        formatted3 = formatter.format(record3)
        assert "[different.source]" in formatted3


def _make_insecure_tls_command():
    @click.command()
    @opt_insecure_tls
    def cmd(insecure_tls: bool):
        click.echo(f"insecure_tls={insecure_tls}")

    return cmd


class TestInsecureTlsOption:
    def test_insecure_tls_flag_is_accepted(self) -> None:
        runner = CliRunner()
        cmd = _make_insecure_tls_command()
        result = runner.invoke(cmd, ["--insecure-tls"])
        assert result.exit_code == 0
        assert "insecure_tls=True" in result.output

    def test_insecure_tls_flag_defaults_to_false(self) -> None:
        runner = CliRunner()
        cmd = _make_insecure_tls_command()
        result = runner.invoke(cmd, [])
        assert result.exit_code == 0
        assert "insecure_tls=False" in result.output

    def test_short_flag_k_is_accepted(self) -> None:
        runner = CliRunner()
        cmd = _make_insecure_tls_command()
        result = runner.invoke(cmd, ["-k"])
        assert result.exit_code == 0
        assert "insecure_tls=True" in result.output


class TestValidateName:
    def test_raises_on_none(self) -> None:
        with pytest.raises(click.UsageError, match="Missing required argument 'NAME'."):
            validate_name(None)

    def test_raises_on_empty_string(self) -> None:
        with pytest.raises(click.UsageError, match="Missing required argument 'NAME'."):
            validate_name("")

    def test_raises_on_whitespace_only(self) -> None:
        with pytest.raises(click.UsageError, match="Missing required argument 'NAME'."):
            validate_name("   ")

    def test_accepts_valid_name(self) -> None:
        validate_name("my-resource")


class TestLogHandlerStream:
    def test_logs_go_to_stderr_not_stdout(self, capsys) -> None:
        """Logs must not corrupt the JSON/YAML payload written to stdout.

        `-o json` consumers (IDE integrations, CI) parse stdout; a log line
        interleaved there leaves the output impossible to parse.
        """
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers.clear()
        try:
            _opt_log_level_callback(None, None, "INFO")
            logging.getLogger("jumpstarter.client.lease").info("Lease acquired successfully!")
            for handler in root.handlers:
                handler.flush()
            captured = capsys.readouterr()
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

        assert "Lease acquired" in captured.err
        assert captured.out == ""

    def test_logs_stay_off_stdout_when_a_handler_is_already_installed(self, capsys) -> None:
        """A root handler installed before the CLI ran must not keep stdout.

        logging.basicConfig does nothing when the root logger already has
        handlers, so simply configuring a stderr handler is not enough: the
        pre-existing one would go on writing into the -o json payload.
        """
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers.clear()
        # Something configured logging before us, pointing at stdout.
        root.addHandler(logging.StreamHandler(sys.stdout))
        try:
            _opt_log_level_callback(None, None, "INFO")
            logging.getLogger("jumpstarter.client.lease").info("Lease acquired successfully!")
            for handler in root.handlers:
                handler.flush()
            captured = capsys.readouterr()
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

        assert captured.out == ""
        assert "Lease acquired" in captured.err

    def test_unrelated_handlers_are_left_alone(self) -> None:
        """Only stdout writers are detached; other handlers are not ours to remove.

        pytest's own caplog handler lives on the root logger, and so may a
        file handler the user configured.
        """
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers.clear()
        elsewhere = logging.StreamHandler(io.StringIO())
        root.addHandler(elsewhere)
        try:
            _opt_log_level_callback(None, None, "INFO")
            assert elsewhere in root.handlers
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)

    def test_a_rich_handler_on_stdout_is_detached_too(self) -> None:
        """RichHandler holds a Console, not a stream, and still writes somewhere.

        The CLI installs one itself, so a second one left pointing at stdout
        would be the easiest way to reintroduce the corruption.
        """
        root = logging.getLogger()
        saved_handlers, saved_level = root.handlers[:], root.level
        root.handlers.clear()
        on_stdout = RichHandler(console=Console(file=sys.stdout))
        elsewhere = RichHandler(console=Console(file=io.StringIO()))
        root.addHandler(on_stdout)
        root.addHandler(elsewhere)
        try:
            _opt_log_level_callback(None, None, "INFO")
            assert on_stdout not in root.handlers
            assert elsewhere in root.handlers
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)
