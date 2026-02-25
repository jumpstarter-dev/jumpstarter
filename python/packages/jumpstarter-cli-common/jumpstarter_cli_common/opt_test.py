"""Tests for SourcePrefixFormatter in opt.py."""

import logging

from jumpstarter_cli_common.opt import SourcePrefixFormatter


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
