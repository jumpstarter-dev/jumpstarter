import asyncio
import logging
import sys
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import pytest
from rich.console import Console

from jumpstarter.client.lease import LeaseAcquisitionSpinner


class TestLeaseAcquisitionSpinner:
    """Test cases for LeaseAcquisitionSpinner class."""

    def test_init_with_lease_name(self):
        """Test spinner initialization with lease name."""
        spinner = LeaseAcquisitionSpinner("test-lease-123")
        assert spinner.lease_name == "test-lease-123"
        assert spinner.console is not None
        assert spinner.spinner is None
        assert spinner.start_time is None
        assert isinstance(spinner._should_show_spinner, bool)

    def test_init_without_lease_name(self):
        """Test spinner initialization without lease name."""
        spinner = LeaseAcquisitionSpinner()
        assert spinner.lease_name is None
        assert spinner.console is not None
        assert spinner.spinner is None
        assert spinner.start_time is None

    def test_is_terminal_available_with_tty(self):
        """Test terminal detection when TTY is available."""
        with patch.object(sys.stdout, 'isatty', return_value=True), \
             patch.object(sys.stderr, 'isatty', return_value=True):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is True

    def test_is_terminal_available_without_tty(self):
        """Test terminal detection when TTY is not available."""
        with patch.object(sys.stdout, 'isatty', return_value=False), \
             patch.object(sys.stderr, 'isatty', return_value=False):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is False

    def test_is_terminal_available_partial_tty(self):
        """Test terminal detection when only one stream is TTY."""
        with patch.object(sys.stdout, 'isatty', return_value=True), \
             patch.object(sys.stderr, 'isatty', return_value=False):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is False

    def test_context_manager_with_console(self):
        """Test context manager behavior when console is available."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, 'status') as mock_status:
                mock_spinner = Mock()
                mock_status.return_value = mock_spinner

                with spinner as ctx_spinner:
                    assert ctx_spinner is spinner
                    assert spinner.start_time is not None
                    mock_status.assert_called_once()
                    mock_spinner.start.assert_called_once()

                mock_spinner.stop.assert_called_once()

    def test_context_manager_without_console(self):
        """Test context manager behavior when console is not available."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, 'status') as mock_status:
                with spinner as ctx_spinner:
                    assert ctx_spinner is spinner
                    assert spinner.start_time is not None
                    mock_status.assert_not_called()

    def test_update_status_with_console(self):
        """Test status update when console is available."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            spinner.update_status("Test message")

            assert spinner._current_message == "[blue]Test message[/blue]"
            mock_spinner.update.assert_called_once()
            call_args = mock_spinner.update.call_args[0][0]
            assert "[blue]Test message[/blue]" in call_args
            assert "[dim](" in call_args

    def test_update_status_without_console(self, caplog):
        """Test status update when console is not available (should log)."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            with caplog.at_level(logging.INFO):
                spinner.update_status("Test message")

            assert "Test message" in caplog.text
            assert spinner._current_message is None

    def test_tick_with_console_and_message(self):
        """Test tick update when console is available and message exists."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._current_message = "[blue]Test message[/blue]"

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            spinner.tick()

            mock_spinner.update.assert_called_once()
            call_args = mock_spinner.update.call_args[0][0]
            assert "[blue]Test message[/blue]" in call_args
            assert "[dim](" in call_args

    def test_tick_without_console(self):
        """Test tick update when console is not available (should not log)."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._current_message = "[blue]Test message[/blue]"

            # Should not raise any exceptions or log anything
            spinner.tick()

    def test_tick_without_message(self):
        """Test tick update when no current message exists."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._current_message = None

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            spinner.tick()

            # Should not call update when no message
            mock_spinner.update.assert_not_called()

    def test_elapsed_time_formatting(self):
        """Test that elapsed time is formatted correctly."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now() - timedelta(seconds=65)  # 1:05
            spinner._current_message = "[blue]Test message[/blue]"

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            spinner.tick()

            call_args = mock_spinner.update.call_args[0][0]
            # Should contain time in format like "0:01:05"
            assert "[dim](" in call_args
            assert "[/dim]" in call_args

    @pytest.mark.asyncio
    async def test_integration_with_async_context(self):
        """Test integration with async context manager."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, 'status') as mock_status:
                mock_spinner = Mock()
                mock_status.return_value = mock_spinner

                async def test_async_usage():
                    with spinner as ctx_spinner:
                        ctx_spinner.update_status("Initial message")
                        await asyncio.sleep(0.1)  # Small delay
                        ctx_spinner.tick()
                        ctx_spinner.update_status("Updated message")

                await test_async_usage()

                # Verify all expected calls were made
                mock_status.assert_called_once()
                assert mock_spinner.start.call_count == 1
                assert mock_spinner.stop.call_count == 1
                # update_status calls update() for each status update, tick() calls update() once
                assert mock_spinner.update.call_count == 3  # 2 update_status calls + 1 tick call

    def test_message_preservation_across_ticks(self):
        """Test that the base message is preserved across multiple ticks."""
        with patch.object(LeaseAcquisitionSpinner, '_is_terminal_available', return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            # Set up mock before calling update_status
            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            spinner.update_status("Waiting for lease: Test condition")

            # Call tick multiple times
            for _ in range(3):
                spinner.tick()

            # All calls should preserve the base message
            assert mock_spinner.update.call_count == 4  # 1 update_status + 3 ticks
            for call in mock_spinner.update.call_args_list:
                call_args = call[0][0]
                assert "[blue]Waiting for lease: Test condition[/blue]" in call_args

    def test_console_initialization(self):
        """Test that console is properly initialized."""
        spinner = LeaseAcquisitionSpinner()
        assert isinstance(spinner.console, Console)

    def test_start_time_initialization_in_context(self):
        """Test that start_time is set when entering context."""
        spinner = LeaseAcquisitionSpinner("test-lease")
        assert spinner.start_time is None

        with spinner:
            assert spinner.start_time is not None
            assert isinstance(spinner.start_time, datetime)
