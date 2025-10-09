import logging
from unittest.mock import MagicMock

from .callbacks import ForceCallback, LoggingCallback, SilentCallback


class TestSilentCallback:
    """Test the SilentCallback class."""

    def test_silent_callback_progress(self):
        """Test that progress does nothing"""
        callback = SilentCallback()
        callback.progress("test message")  # Should not raise

    def test_silent_callback_success(self):
        """Test that success does nothing"""
        callback = SilentCallback()
        callback.success("test message")  # Should not raise

    def test_silent_callback_warning(self):
        """Test that warning does nothing"""
        callback = SilentCallback()
        callback.warning("test message")  # Should not raise

    def test_silent_callback_error(self):
        """Test that error does nothing"""
        callback = SilentCallback()
        callback.error("test message")  # Should not raise

    def test_silent_callback_confirm(self):
        """Test that confirm always returns True"""
        callback = SilentCallback()
        assert callback.confirm("Are you sure?") is True


class TestLoggingCallback:
    """Test the LoggingCallback class."""

    def test_logging_callback_with_default_logger(self):
        """Test LoggingCallback with default logger"""
        callback = LoggingCallback()
        assert callback.logger is not None

    def test_logging_callback_with_custom_logger(self):
        """Test LoggingCallback with custom logger"""
        logger = logging.getLogger("test")
        callback = LoggingCallback(logger)
        assert callback.logger == logger

    def test_logging_callback_progress(self):
        """Test that progress logs at INFO level"""
        mock_logger = MagicMock(spec=logging.Logger)
        callback = LoggingCallback(mock_logger)
        callback.progress("test message")
        mock_logger.info.assert_called_once_with("test message")

    def test_logging_callback_success(self):
        """Test that success logs at INFO level"""
        mock_logger = MagicMock(spec=logging.Logger)
        callback = LoggingCallback(mock_logger)
        callback.success("test message")
        mock_logger.info.assert_called_once_with("test message")

    def test_logging_callback_warning(self):
        """Test that warning logs at WARNING level"""
        mock_logger = MagicMock(spec=logging.Logger)
        callback = LoggingCallback(mock_logger)
        callback.warning("test message")
        mock_logger.warning.assert_called_once_with("test message")

    def test_logging_callback_error(self):
        """Test that error logs at ERROR level"""
        mock_logger = MagicMock(spec=logging.Logger)
        callback = LoggingCallback(mock_logger)
        callback.error("test message")
        mock_logger.error.assert_called_once_with("test message")

    def test_logging_callback_confirm(self):
        """Test that confirm logs and returns True"""
        mock_logger = MagicMock(spec=logging.Logger)
        callback = LoggingCallback(mock_logger)
        result = callback.confirm("Are you sure?")
        assert result is True
        mock_logger.info.assert_called_once_with("Confirmation requested: Are you sure? (auto-confirmed)")


class TestForceCallback:
    """Test the ForceCallback class."""

    def test_force_callback_with_default_output(self):
        """Test ForceCallback with default SilentCallback"""
        callback = ForceCallback()
        assert isinstance(callback.output_callback, SilentCallback)

    def test_force_callback_with_custom_output(self):
        """Test ForceCallback with custom output callback"""
        custom_callback = MagicMock()
        callback = ForceCallback(custom_callback)
        assert callback.output_callback == custom_callback

    def test_force_callback_progress(self):
        """Test that progress forwards to output callback"""
        mock_output = MagicMock()
        callback = ForceCallback(mock_output)
        callback.progress("test message")
        mock_output.progress.assert_called_once_with("test message")

    def test_force_callback_success(self):
        """Test that success forwards to output callback"""
        mock_output = MagicMock()
        callback = ForceCallback(mock_output)
        callback.success("test message")
        mock_output.success.assert_called_once_with("test message")

    def test_force_callback_warning(self):
        """Test that warning forwards to output callback"""
        mock_output = MagicMock()
        callback = ForceCallback(mock_output)
        callback.warning("test message")
        mock_output.warning.assert_called_once_with("test message")

    def test_force_callback_error(self):
        """Test that error forwards to output callback"""
        mock_output = MagicMock()
        callback = ForceCallback(mock_output)
        callback.error("test message")
        mock_output.error.assert_called_once_with("test message")

    def test_force_callback_confirm(self):
        """Test that confirm always returns True without forwarding"""
        mock_output = MagicMock()
        callback = ForceCallback(mock_output)
        result = callback.confirm("Are you sure?")
        assert result is True
        # Confirm should NOT be forwarded to output callback
        mock_output.confirm.assert_not_called()
