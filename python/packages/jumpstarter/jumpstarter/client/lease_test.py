import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock, patch

import pytest
from rich.console import Console

from jumpstarter.client.lease import Lease, LeaseAcquisitionSpinner


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
        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch.object(sys.stderr, "isatty", return_value=True),
        ):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is True

    def test_is_terminal_available_without_tty(self):
        """Test terminal detection when TTY is not available."""
        with (
            patch.object(sys.stdout, "isatty", return_value=False),
            patch.object(sys.stderr, "isatty", return_value=False),
        ):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is False

    def test_is_terminal_available_partial_tty(self):
        """Test terminal detection when only one stream is TTY."""
        with (
            patch.object(sys.stdout, "isatty", return_value=True),
            patch.object(sys.stderr, "isatty", return_value=False),
        ):
            spinner = LeaseAcquisitionSpinner()
            assert spinner._is_terminal_available() is False

    def test_context_manager_with_console(self):
        """Test context manager behavior when console is available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, "status") as mock_status:
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, "status") as mock_status:
                with spinner as ctx_spinner:
                    assert ctx_spinner is spinner
                    assert spinner.start_time is not None
                    mock_status.assert_not_called()

    def test_update_status_with_console(self):
        """Test status update when console is available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            with caplog.at_level(logging.INFO):
                spinner.update_status("Test message")

            assert "Test message" in caplog.text
            assert spinner._current_message is None

    def test_tick_with_console_and_message(self):
        """Test tick update when console is available and message exists."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._current_message = "[blue]Test message[/blue]"

            # Should not raise any exceptions or log anything
            spinner.tick()

    def test_tick_without_message(self):
        """Test tick update when no current message exists."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")

            with patch.object(spinner.console, "status") as mock_status:
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
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
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

    def test_throttling_first_update_logged(self, caplog):
        """Test that the first update is always logged when console is not available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            with caplog.at_level(logging.INFO):
                spinner.update_status("First message")

            assert "First message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_second_update_within_interval_not_logged(self, caplog):
        """Test that updates within 5 minutes are not logged."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._last_log_time = datetime.now() - timedelta(minutes=2)  # 2 minutes ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("Second message")

            # Should not log because only 2 minutes have passed
            assert "Second message" not in caplog.text

    def test_throttling_update_after_interval_logged(self, caplog):
        """Test that updates after 5 minutes are logged."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._last_log_time = datetime.now() - timedelta(minutes=6)  # 6 minutes ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("After interval message")

            assert "After interval message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_forced_update_always_logged(self, caplog):
        """Test that forced updates are always logged regardless of throttle interval."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()
            spinner._last_log_time = datetime.now() - timedelta(minutes=1)  # 1 minute ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("Forced message", force=True)

            assert "Forced message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_multiple_updates_only_logs_when_needed(self, caplog):
        """Test that multiple rapid updates only log at appropriate intervals."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            with caplog.at_level(logging.INFO):
                # First update should be logged
                spinner.update_status("Message 1")
                assert "Message 1" in caplog.text

                # Set last log time to recent
                spinner._last_log_time = datetime.now() - timedelta(minutes=1)

                # Second update should not be logged (within interval)
                spinner.update_status("Message 2")
                assert "Message 2" not in caplog.text

                # Third update should not be logged (within interval)
                spinner.update_status("Message 3")
                assert "Message 3" not in caplog.text

                # Set last log time to past the interval
                spinner._last_log_time = datetime.now() - timedelta(minutes=6)

                # Fourth update should be logged (past interval)
                spinner.update_status("Message 4")
                assert "Message 4" in caplog.text

    def test_throttling_not_applied_when_console_available(self):
        """Test that throttling is not applied when console is available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now()

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            # Multiple updates should all call update() regardless of throttle
            spinner.update_status("Message 1")
            spinner.update_status("Message 2")
            spinner.update_status("Message 3")

            # All should be called even if we set a recent last_log_time
            spinner._last_log_time = datetime.now() - timedelta(minutes=1)
            spinner.update_status("Message 4")

            assert mock_spinner.update.call_count == 4


class TestRefreshChannel:
    """Tests for Lease.refresh_channel."""

    def _make_lease(self):
        """Create a Lease with mocked dependencies."""
        channel = Mock(name="original_channel")
        lease = object.__new__(Lease)
        lease.channel = channel
        lease.namespace = "default"
        lease.controller = Mock(name="original_controller")
        lease.svc = Mock(name="original_svc")
        return lease

    @patch("jumpstarter.client.lease.ClientService")
    @patch("jumpstarter.client.lease.jumpstarter_pb2_grpc.ControllerServiceStub")
    def test_replaces_channel_and_stubs(self, mock_stub_cls, mock_svc_cls):
        lease = self._make_lease()
        new_channel = Mock(name="new_channel")

        lease.refresh_channel(new_channel)

        assert lease.channel is new_channel
        mock_stub_cls.assert_called_once_with(new_channel)
        assert lease.controller is mock_stub_cls.return_value
        mock_svc_cls.assert_called_once()


class TestNotifyLeaseEnding:
    """Tests for Lease._notify_lease_ending."""

    def _make_lease(self):
        lease = object.__new__(Lease)
        lease.lease_ending_callback = None
        return lease

    def test_calls_callback_when_set(self):
        lease = self._make_lease()
        callback = Mock()
        lease.lease_ending_callback = callback
        remaining = timedelta(minutes=3)

        lease._notify_lease_ending(remaining)

        callback.assert_called_once_with(lease, remaining)

    def test_noop_when_no_callback(self):
        lease = self._make_lease()

        # Should not raise
        lease._notify_lease_ending(timedelta(0))


class TestGetLeaseEndTime:
    """Tests for Lease._get_lease_end_time."""

    def _make_lease(self):
        return object.__new__(Lease)

    def test_returns_none_when_no_begin_time(self):
        lease = self._make_lease()
        response = Mock(effective_begin_time=None, effective_duration=timedelta(minutes=30))

        assert lease._get_lease_end_time(response) is None

    def test_returns_none_when_no_duration(self):
        lease = self._make_lease()
        response = Mock(
            effective_begin_time=datetime.now(tz=timezone.utc),
            effective_duration=None,
        )

        assert lease._get_lease_end_time(response) is None

    def test_returns_effective_end_time_when_present(self):
        lease = self._make_lease()
        end_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        response = Mock(
            effective_begin_time=datetime(2025, 6, 1, 11, 0, 0, tzinfo=timezone.utc),
            effective_duration=timedelta(hours=1),
            effective_end_time=end_time,
        )

        assert lease._get_lease_end_time(response) is end_time

    def test_calculates_end_time_when_no_effective_end(self):
        lease = self._make_lease()
        begin = datetime(2025, 6, 1, 11, 0, 0, tzinfo=timezone.utc)
        duration = timedelta(hours=2)
        response = Mock(
            effective_begin_time=begin,
            effective_duration=duration,
            effective_end_time=None,
            duration=duration,
        )

        result = lease._get_lease_end_time(response)

        assert result == begin + duration


class TestMonitorAsyncError:
    """Tests for the error handling in monitor_async."""

    def _make_lease_for_monitor(self):
        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.lease_ending_callback = None
        lease.get = AsyncMock()
        return lease

    @pytest.mark.anyio
    async def test_continues_on_get_failure_without_end_time(self):
        """When get() fails and we have no end time, monitor retries."""
        lease = self._make_lease_for_monitor()
        call_count = 0

        async def failing_get():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise Exception("transient error")
            # Third call: return expired lease to exit the loop
            end_time = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
            return Mock(
                effective_begin_time=end_time - timedelta(hours=1),
                effective_duration=timedelta(hours=1),
                effective_end_time=end_time,
            )

        lease.get = failing_get

        with patch("jumpstarter.client.lease.sleep", new_callable=AsyncMock):
            async with lease.monitor_async():
                pass

        assert call_count == 3  # two failures + one success

    @pytest.mark.anyio
    async def test_estimates_expiry_from_last_known_end_time(self, caplog):
        """When get() fails after we've seen an end time, use cached value."""
        lease = self._make_lease_for_monitor()
        callback = Mock()
        lease.lease_ending_callback = callback

        # End time slightly in the future so the monitor caches it and sleeps
        future_end = datetime.now(tz=timezone.utc) + timedelta(milliseconds=50)
        call_count = 0

        async def get_then_fail():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return Mock(
                    effective_begin_time=future_end - timedelta(hours=1),
                    effective_duration=timedelta(hours=1),
                    effective_end_time=None,
                    duration=timedelta(hours=1),
                )
            raise Exception("server unavailable")

        lease.get = get_then_fail

        with caplog.at_level(logging.WARNING):
            async with lease.monitor_async():
                # Keep the body alive long enough for the monitor to loop
                # through the first get(), sleep, second get() (fails), and
                # error handler using the cached end time.
                await asyncio.sleep(0.2)

        # Should have gone through the error handler using cached end time
        assert call_count >= 2
        callback.assert_called()
        _, remain_arg = callback.call_args[0]
        assert remain_arg == timedelta(0)
