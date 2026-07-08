"""Tests for LogHandler and log source routing."""

import logging
from collections import deque
from unittest.mock import MagicMock

import pytest

from jumpstarter.common import LogSource
from jumpstarter.exporter.logging import LogHandler, get_logger


class TestLogHandler:
    def test_init_with_default_source(self) -> None:
        """Test that LogHandler initializes with the correct default source."""
        queue = deque()
        handler = LogHandler(queue)

        assert handler.queue is queue
        assert handler.source == LogSource.UNSPECIFIED

    def test_init_with_specified_source(self) -> None:
        """Test that LogHandler initializes with a specified source."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        assert handler.source == LogSource.DRIVER

    def test_emit_appends_to_queue(self) -> None:
        """Test that emit() adds LogStreamResponse to the queue."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        handler.emit(record)

        assert len(queue) == 1
        response = queue[0]
        assert response.message == "Test message"
        assert response.severity == "INFO"
        assert response.source == LogSource.SYSTEM.value

    def test_prepare_creates_log_stream_response(self) -> None:
        """Test that prepare() creates a LogStreamResponse with correct fields."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        record = logging.LogRecord(
            name="driver.test",
            level=logging.WARNING,
            pathname="driver.py",
            lineno=42,
            msg="Warning: %s",
            args=("something",),
            exc_info=None,
        )

        response = handler.prepare(record)

        assert response.message == "Warning: something"
        assert response.severity == "WARNING"
        assert response.source == LogSource.DRIVER.value
        assert response.uuid == ""

    def test_add_child_handler(self) -> None:
        """Test that add_child_handler() registers logger-to-source mapping."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        handler.add_child_handler("hook.before", LogSource.BEFORE_LEASE_HOOK)

        assert "hook.before" in handler._child_handlers
        assert handler._child_handlers["hook.before"] == LogSource.BEFORE_LEASE_HOOK

    def test_remove_child_handler(self) -> None:
        """Test that remove_child_handler() removes the mapping."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        handler.add_child_handler("hook.before", LogSource.BEFORE_LEASE_HOOK)
        handler.remove_child_handler("hook.before")

        assert "hook.before" not in handler._child_handlers

    def test_remove_child_handler_nonexistent_no_error(self) -> None:
        """Test that remove_child_handler() handles nonexistent loggers gracefully."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        # Should not raise an error
        handler.remove_child_handler("nonexistent.logger")

    def test_get_source_for_record_default(self) -> None:
        """Test that get_source_for_record() returns default source for unmapped loggers."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="random.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        source = handler.get_source_for_record(record)

        assert source == LogSource.SYSTEM

    def test_get_source_for_record_child_mapping(self) -> None:
        """Test that get_source_for_record() uses child handler mapping."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)
        handler.add_child_handler("hook.before", LogSource.BEFORE_LEASE_HOOK)

        record = logging.LogRecord(
            name="hook.before.subprocess",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        source = handler.get_source_for_record(record)

        assert source == LogSource.BEFORE_LEASE_HOOK

    def test_get_source_for_record_exact_match(self) -> None:
        """Test that get_source_for_record() works with exact logger name match."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)
        handler.add_child_handler("hook.after", LogSource.AFTER_LEASE_HOOK)

        record = logging.LogRecord(
            name="hook.after",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test",
            args=(),
            exc_info=None,
        )

        source = handler.get_source_for_record(record)

        assert source == LogSource.AFTER_LEASE_HOOK

    def test_emit_different_log_levels(self) -> None:
        """Test that emit() preserves different log levels."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        levels = [
            (logging.DEBUG, "DEBUG"),
            (logging.INFO, "INFO"),
            (logging.WARNING, "WARNING"),
            (logging.ERROR, "ERROR"),
            (logging.CRITICAL, "CRITICAL"),
        ]

        for level, expected_severity in levels:
            record = logging.LogRecord(
                name="test.logger",
                level=level,
                pathname="test.py",
                lineno=1,
                msg=f"{expected_severity} message",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        assert len(queue) == 5
        for i, (_, expected_severity) in enumerate(levels):
            assert queue[i].severity == expected_severity


class TestLogHandlerContextManager:
    def test_context_log_source_adds_mapping(self) -> None:
        """Test that context_log_source() adds mapping within context."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        with handler.context_log_source("hook.before", LogSource.BEFORE_LEASE_HOOK):
            assert "hook.before" in handler._child_handlers
            assert handler._child_handlers["hook.before"] == LogSource.BEFORE_LEASE_HOOK

    def test_context_log_source_removes_on_exit(self) -> None:
        """Test that context_log_source() removes mapping on context exit."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        with handler.context_log_source("hook.before", LogSource.BEFORE_LEASE_HOOK):
            pass

        assert "hook.before" not in handler._child_handlers

    def test_context_log_source_handles_exception(self) -> None:
        """Test that context_log_source() removes mapping even on exception."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        with pytest.raises(ValueError):
            with handler.context_log_source("hook.before", LogSource.BEFORE_LEASE_HOOK):
                assert "hook.before" in handler._child_handlers
                raise ValueError("Test exception")

        assert "hook.before" not in handler._child_handlers

    def test_context_log_source_routes_logs_correctly(self) -> None:
        """Test that logs are routed correctly within context."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        with handler.context_log_source("hook.before", LogSource.BEFORE_LEASE_HOOK):
            record = logging.LogRecord(
                name="hook.before.script",
                level=logging.INFO,
                pathname="hook.py",
                lineno=1,
                msg="Hook output",
                args=(),
                exc_info=None,
            )
            handler.emit(record)

        assert len(queue) == 1
        assert queue[0].source == LogSource.BEFORE_LEASE_HOOK.value


class TestGetLogger:
    def test_get_logger_returns_logger(self) -> None:
        """Test that get_logger() returns a Python logger."""
        logger = get_logger("test.module")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_get_logger_registers_with_session(self) -> None:
        """Test that get_logger() registers source with session when provided."""
        mock_session = MagicMock()

        logger = get_logger("driver.test", source=LogSource.DRIVER, session=mock_session)

        mock_session.add_logger_source.assert_called_once_with("driver.test", LogSource.DRIVER)
        assert isinstance(logger, logging.Logger)

    def test_get_logger_without_session(self) -> None:
        """Test that get_logger() works without session."""
        logger = get_logger("standalone.logger", source=LogSource.SYSTEM)

        assert isinstance(logger, logging.Logger)
        # Should not raise any error


class TestLogHandlerNewFields:
    """Tests for LogStreamResponse enrichment fields (Phase 2)."""

    def test_prepare_populates_driver_type(self) -> None:
        """Test that driver_type extra is mapped to the proto field."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        record = logging.LogRecord(
            name="driver.power", level=logging.INFO,
            pathname="driver.py", lineno=1, msg="Op started", args=(), exc_info=None,
        )
        record.driver_type = "power"

        response = handler.prepare(record)

        assert response.driver_type == "power"

    def test_prepare_populates_operation(self) -> None:
        """Test that operation extra is mapped to the proto field."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        record = logging.LogRecord(
            name="driver.power", level=logging.INFO,
            pathname="driver.py", lineno=1, msg="Op started", args=(), exc_info=None,
        )
        record.operation = "power_on"

        response = handler.prepare(record)

        assert response.operation == "power_on"

    def test_prepare_sets_timestamp(self) -> None:
        """Test that timestamp is always set from record.created."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1, msg="Test", args=(), exc_info=None,
        )

        response = handler.prepare(record)

        assert response.HasField("timestamp")
        assert response.timestamp.seconds > 0

    def test_prepare_populates_structured_fields(self) -> None:
        """Test that result, error_type, and correlation fields go into structured_fields."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        record = logging.LogRecord(
            name="driver.storage", level=logging.WARNING,
            pathname="driver.py", lineno=1, msg="Op failed", args=(), exc_info=None,
        )
        record.result = "failure"
        record.error_type = "timeout"
        record.lease_id = "lease-abc"

        response = handler.prepare(record)

        assert response.structured_fields["result"] == "failure"
        assert response.structured_fields["error_type"] == "timeout"
        assert response.structured_fields["lease_id"] == "lease-abc"

    def test_prepare_omits_empty_structured_fields(self) -> None:
        """Test that structured_fields is empty when no relevant extras are set."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1, msg="Plain msg", args=(), exc_info=None,
        )

        response = handler.prepare(record)

        assert len(response.structured_fields) == 0

    def test_prepare_without_driver_type_leaves_field_unset(self) -> None:
        """Test that driver_type is not set when extra is absent."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1, msg="Test", args=(), exc_info=None,
        )

        response = handler.prepare(record)

        assert not response.HasField("driver_type")

    def test_prepare_all_error_type_categories(self) -> None:
        """Test that all defined error_type categories are captured correctly."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.DRIVER)

        error_types = [
            "not_implemented", "validation_error", "timeout",
            "connection_error", "device_error", "internal_error",
        ]
        for error_type in error_types:
            record = logging.LogRecord(
                name="driver.test", level=logging.WARNING,
                pathname="driver.py", lineno=1, msg="Op failed", args=(), exc_info=None,
            )
            record.error_type = error_type
            record.result = "failure"
            record.operation = "test_op"
            record.driver_type = "power"

            response = handler.prepare(record)

            assert response.structured_fields["error_type"] == error_type
            assert response.structured_fields["result"] == "failure"
            assert response.driver_type == "power"
            assert response.operation == "test_op"

    def test_prepare_namespace_in_structured_fields(self) -> None:
        """Test that namespace extra is included in structured_fields."""
        queue = deque()
        handler = LogHandler(queue, source=LogSource.SYSTEM)

        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="test.py", lineno=1, msg="Test", args=(), exc_info=None,
        )
        record.namespace = "production"

        response = handler.prepare(record)

        assert response.structured_fields["namespace"] == "production"
