import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import anyio
import grpc
import pytest
from grpc.aio import AioRpcError
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc
from rich.console import Console

from jumpstarter.client.exceptions import LeaseError
from jumpstarter.client.lease import Lease, LeaseAcquisitionSpinner
from jumpstarter.common.exceptions import ExporterUnreachableError


class MockAioRpcError(AioRpcError):
    """Mock gRPC error for testing that properly inherits from AioRpcError."""

    def __init__(self, status_code, message=""):
        self._status_code = status_code
        self._message = message
        self._code = status_code
        self._details = message
        self._debug_error_string = ""

    def code(self):
        return self._status_code

    def details(self):
        return self._message


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

            with patch.object(spinner.console, "status") as mock_status, spinner as ctx_spinner:
                assert ctx_spinner is spinner
                assert spinner.start_time is not None
                mock_status.assert_not_called()

    def test_update_status_with_console(self):
        """Test status update when console is available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)

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
            spinner.start_time = datetime.now(tz=UTC)

            with caplog.at_level(logging.INFO):
                spinner.update_status("Test message")

            assert "Test message" in caplog.text
            assert spinner._current_message is None

    def test_tick_with_console_and_message(self):
        """Test tick update when console is available and message exists."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)
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
            spinner.start_time = datetime.now(tz=UTC)
            spinner._current_message = "[blue]Test message[/blue]"

            # Should not raise any exceptions or log anything
            spinner.tick()

    def test_tick_without_message(self):
        """Test tick update when no current message exists."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)
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
            spinner.start_time = datetime.now(tz=UTC) - timedelta(seconds=65)  # 1:05
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
            spinner.start_time = datetime.now(tz=UTC)

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
            spinner.start_time = datetime.now(tz=UTC)

            with caplog.at_level(logging.INFO):
                spinner.update_status("First message")

            assert "First message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_second_update_within_interval_not_logged(self, caplog):
        """Test that updates within 5 minutes are not logged."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)
            spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=2)  # 2 minutes ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("Second message")

            # Should not log because only 2 minutes have passed
            assert "Second message" not in caplog.text

    def test_throttling_update_after_interval_logged(self, caplog):
        """Test that updates after 5 minutes are logged."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)
            spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=6)  # 6 minutes ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("After interval message")

            assert "After interval message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_forced_update_always_logged(self, caplog):
        """Test that forced updates are always logged regardless of throttle interval."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)
            spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=1)  # 1 minute ago

            with caplog.at_level(logging.INFO):
                spinner.update_status("Forced message", force=True)

            assert "Forced message" in caplog.text
            assert spinner._last_log_time is not None

    def test_throttling_multiple_updates_only_logs_when_needed(self, caplog):
        """Test that multiple rapid updates only log at appropriate intervals."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=False):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)

            with caplog.at_level(logging.INFO):
                # First update should be logged
                spinner.update_status("Message 1")
                assert "Message 1" in caplog.text

                # Set last log time to recent
                spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=1)

                # Second update should not be logged (within interval)
                spinner.update_status("Message 2")
                assert "Message 2" not in caplog.text

                # Third update should not be logged (within interval)
                spinner.update_status("Message 3")
                assert "Message 3" not in caplog.text

                # Set last log time to past the interval
                spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=6)

                # Fourth update should be logged (past interval)
                spinner.update_status("Message 4")
                assert "Message 4" in caplog.text

    def test_throttling_not_applied_when_console_available(self):
        """Test that throttling is not applied when console is available."""
        with patch.object(LeaseAcquisitionSpinner, "_is_terminal_available", return_value=True):
            spinner = LeaseAcquisitionSpinner("test-lease")
            spinner.start_time = datetime.now(tz=UTC)

            mock_spinner = Mock()
            spinner.spinner = mock_spinner

            # Multiple updates should all call update() regardless of throttle
            spinner.update_status("Message 1")
            spinner.update_status("Message 2")
            spinner.update_status("Message 3")

            # All should be called even if we set a recent last_log_time
            spinner._last_log_time = datetime.now(tz=UTC) - timedelta(minutes=1)
            spinner.update_status("Message 4")

            assert mock_spinner.update.call_count == 4


class TestRequestAsyncOwnership:
    """Tests for lease ownership validation in request_async."""

    def _make_lease(self, *, name="test-lease", client_name="my-client"):
        lease = object.__new__(Lease)
        lease.name = name
        lease.client_name = client_name
        lease.selector = None
        lease.get = AsyncMock()
        return lease

    @pytest.mark.anyio
    async def test_raises_when_lease_belongs_to_different_client(self):
        """request_async should raise LeaseError when the lease belongs to another client."""
        lease = self._make_lease(client_name="my-client")
        # Not accessible: not the owner and not in the effective share set.
        lease.get.return_value = Mock(
            client="other-client",
            selector=None,
            effective_end_time=None,
            is_accessible_by=Mock(return_value=False),
        )

        with pytest.raises(LeaseError, match="belongs to client 'other-client'"):
            await lease.request_async()

    @pytest.mark.anyio
    async def test_allows_when_client_has_shared_access(self):
        """request_async should proceed when the client has effective shared access."""
        lease = self._make_lease(client_name="my-client")
        # Owned by someone else, but my-client is in the effective share set.
        lease.get.return_value = Mock(
            client="other-client",
            selector=None,
            effective_end_time=None,
            is_accessible_by=Mock(return_value=True),
        )
        lease._acquire = AsyncMock(return_value=lease)

        result = await lease.request_async()
        assert result is lease

    @pytest.mark.anyio
    async def test_skips_check_when_client_name_is_none(self):
        """request_async should skip ownership check when client_name is not set."""
        lease = self._make_lease(client_name=None)
        lease.get.return_value = Mock(client="other-client", selector=None, effective_end_time=None)
        lease._acquire = AsyncMock(return_value=lease)

        result = await lease.request_async()
        assert result is lease


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


class TestCreateDeprecatedLabels:
    """Tests for deprecation warnings emitted during Lease._create."""

    def _make_lease(self):
        lease = object.__new__(Lease)
        lease.selector = "device=ti-jacinto"
        lease.requested_exporter_name = None
        lease.duration = timedelta(minutes=30)
        lease.name = None
        lease.tags = {}
        lease.svc = Mock()
        return lease

    @pytest.mark.anyio
    async def test_warns_for_deprecated_selector_labels(self, caplog):
        lease = self._make_lease()
        created = Mock(
            deprecated_labels={"device": "Use -n <exporter name> instead of -l device=<exporter-name>"},
        )
        created.name = "lease-1"
        lease.svc.CreateLease = AsyncMock(return_value=created)

        with caplog.at_level(logging.WARNING):
            await lease._create()

        assert lease.name == "lease-1"
        assert "selector label 'device' is deprecated" in caplog.text
        assert "Use -n <exporter name>" in caplog.text

    @pytest.mark.anyio
    async def test_no_warning_when_no_deprecated_labels(self, caplog):
        lease = self._make_lease()
        created = Mock(deprecated_labels={})
        created.name = "lease-1"
        lease.svc.CreateLease = AsyncMock(return_value=created)

        with caplog.at_level(logging.WARNING):
            await lease._create()

        assert "deprecated" not in caplog.text


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
        response = Mock(effective_begin_time=None, duration=timedelta(minutes=30), effective_end_time=None)

        assert lease._get_lease_end_time(response) is None

    def test_returns_none_when_no_duration(self):
        lease = self._make_lease()
        response = Mock(
            effective_begin_time=datetime.now(tz=UTC),
            duration=None,
            effective_end_time=None,
        )

        assert lease._get_lease_end_time(response) is None

    def test_returns_effective_end_time_when_present(self):
        lease = self._make_lease()
        end_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        response = Mock(
            effective_begin_time=datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC),
            duration=timedelta(hours=1),
            effective_end_time=end_time,
        )

        assert lease._get_lease_end_time(response) is end_time

    def test_returns_effective_end_time_even_without_begin_or_duration(self):
        lease = self._make_lease()
        end_time = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        response = Mock(
            effective_begin_time=None,
            duration=None,
            effective_end_time=end_time,
        )

        assert lease._get_lease_end_time(response) is end_time

    def test_calculates_end_time_when_no_effective_end(self):
        lease = self._make_lease()
        begin = datetime(2025, 6, 1, 11, 0, 0, tzinfo=UTC)
        duration = timedelta(hours=2)
        response = Mock(
            effective_begin_time=begin,
            effective_duration=timedelta(hours=1),  # elapsed time, not used for calculation
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
                raise Exception("transient error")  # noqa: TRY002
            # Third call: return expired lease to exit the loop
            end_time = datetime.now(tz=UTC) - timedelta(seconds=10)
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
        future_end = datetime.now(tz=UTC) + timedelta(milliseconds=50)
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
            raise Exception("server unavailable")  # noqa: TRY002

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


@asynccontextmanager
async def _dial_controller(dial):
    class Controller(jumpstarter_pb2_grpc.ControllerServiceServicer):
        async def Dial(self, request, context):
            return await dial(request, context)

    server = grpc.aio.server()
    jumpstarter_pb2_grpc.add_ControllerServiceServicer_to_server(Controller(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            await asyncio.wait_for(channel.channel_ready(), timeout=2)
            yield jumpstarter_pb2_grpc.ControllerServiceStub(channel)
    finally:
        await server.stop(grace=None)


class TestDialWithRetry:
    """Tests for Lease._dial_with_retry UNAVAILABLE retry behavior."""

    def _make_lease_for_dial(self):
        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.dial_timeout = 5.0
        lease.lease_transferred = False
        lease.controller = Mock()
        return lease

    @pytest.mark.anyio
    async def test_dial_retries_unavailable_then_succeeds(self):
        """Dial returns UNAVAILABLE once then succeeds on retry."""
        lease = self._make_lease_for_dial()
        dial_call_count = 0

        async def mock_dial(request, *, timeout):
            nonlocal dial_call_count
            dial_call_count += 1
            if dial_call_count == 1:
                raise MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "temporarily unavailable")
            return Mock(router_endpoint="endpoint", router_token="token")

        lease.controller.Dial = mock_dial

        response = await lease._dial_with_retry()

        assert dial_call_count == 2
        assert response.router_endpoint == "endpoint"
        assert response.router_token == "token"

    @pytest.mark.anyio
    async def test_dial_unavailable_exceeds_timeout_raises_exporter_unreachable(self):
        """Dial returns UNAVAILABLE until dial_timeout is exceeded, raises ExporterUnreachableError."""

        lease = self._make_lease_for_dial()
        lease.dial_timeout = 0.5
        dial_call_count = 0

        async def mock_dial(request, *, timeout):
            nonlocal dial_call_count
            dial_call_count += 1
            raise MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "permanently unavailable")

        lease.controller.Dial = mock_dial

        with pytest.raises(ExporterUnreachableError):
            await lease._dial_with_retry()

        assert dial_call_count >= 2

    @pytest.mark.anyio
    async def test_dial_failed_precondition_exceeds_timeout_raises_exporter_unreachable(self):
        """Dial returns FAILED_PRECONDITION until dial_timeout is exceeded, raises ExporterUnreachableError."""

        lease = self._make_lease_for_dial()
        lease.dial_timeout = 0.5
        dial_call_count = 0

        async def mock_dial(request, *, timeout):
            nonlocal dial_call_count
            dial_call_count += 1
            raise MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "not ready")

        lease.controller.Dial = mock_dial

        with pytest.raises(ExporterUnreachableError):
            await lease._dial_with_retry()

        assert dial_call_count >= 2

    @pytest.mark.anyio
    async def test_offline_exporter_fails_immediately_so_shell_can_reacquire(self):
        lease = self._make_lease_for_dial()
        lease.controller.Dial = AsyncMock(
            side_effect=MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "exporter is offline")
        )

        with pytest.raises(ExporterUnreachableError, match="exporter is offline") as caught:
            await lease._dial_with_retry()
        assert type(caught.value) is ExporterUnreachableError
        lease.controller.Dial.assert_awaited_once()

    @pytest.mark.anyio
    async def test_dial_permission_denied_raises_exporter_unreachable_and_sets_transferred(self):
        """Dial returns permission denied error, raises ExporterUnreachableError and sets lease_transferred flag."""

        lease = self._make_lease_for_dial()

        async def mock_dial(request, *, timeout):
            raise MockAioRpcError(grpc.StatusCode.PERMISSION_DENIED, "permission denied")

        lease.controller.Dial = mock_dial

        with pytest.raises(ExporterUnreachableError) as exc_info:
            await lease._dial_with_retry()

        assert lease.lease_transferred is True
        assert "transferred to another client" in str(exc_info.value)

    @pytest.mark.anyio
    async def test_dial_unknown_error_raises_exporter_unreachable(self):
        """Dial returns unknown error, raises ExporterUnreachableError without retry."""

        lease = self._make_lease_for_dial()

        async def mock_dial(request, *, timeout):
            raise MockAioRpcError(grpc.StatusCode.INTERNAL, "something broke")

        lease.controller.Dial = mock_dial

        with pytest.raises(ExporterUnreachableError) as exc_info:
            await lease._dial_with_retry()

        assert lease.lease_transferred is False
        assert "lost" in str(exc_info.value).lower()


    @pytest.mark.anyio
    @pytest.mark.parametrize(("code", "details"), [
        (grpc.StatusCode.DEADLINE_EXCEEDED, "deadline exceeded"),
        (grpc.StatusCode.UNKNOWN, "oidc: authenticator not initialized"),
        (grpc.StatusCode.FAILED_PRECONDITION, "exporter is not ready (status: Available)"),
    ])
    async def test_dial_retries_recovery_errors(self, code, details):
        lease = self._make_lease_for_dial()
        response = Mock(router_endpoint="endpoint", router_token="token")
        lease.controller.Dial = AsyncMock(side_effect=[MockAioRpcError(code, details), response])
        with patch("jumpstarter.client.lease.sleep", new_callable=AsyncMock):
            assert await lease._dial_with_retry() is response
        assert lease.controller.Dial.await_count == 2

    @pytest.mark.anyio
    @pytest.mark.parametrize(("code", "details"), [
        (grpc.StatusCode.UNKNOWN, "unexpected server failure"),
        (grpc.StatusCode.FAILED_PRECONDITION, "lease has ended"),
        (grpc.StatusCode.UNAUTHENTICATED, "invalid credentials"),
    ])
    async def test_dial_does_not_retry_permanent_errors(self, code, details):
        lease = self._make_lease_for_dial()
        lease.controller.Dial = AsyncMock(side_effect=MockAioRpcError(code, details))
        with pytest.raises(ExporterUnreachableError):
            await lease._dial_with_retry()
        lease.controller.Dial.assert_awaited_once()

    @pytest.mark.anyio
    @pytest.mark.parametrize("codes", [
        [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.FAILED_PRECONDITION],
        [grpc.StatusCode.FAILED_PRECONDITION, grpc.StatusCode.UNAVAILABLE],
    ])
    async def test_timeout_after_mixed_recovery_errors(self, codes):
        lease = self._make_lease_for_dial()
        lease.dial_timeout = 0.5
        details = {
            grpc.StatusCode.UNAVAILABLE: "controller restarting",
            grpc.StatusCode.FAILED_PRECONDITION: "not ready",
        }
        lease.controller.Dial = AsyncMock(side_effect=[MockAioRpcError(code, details[code]) for code in codes])
        clock = Mock()
        clock.monotonic.return_value = 0

        async def advance(delay):
            clock.monotonic.return_value += delay

        with (
            patch("jumpstarter.client.lease.time", clock),
            patch("jumpstarter.client.lease.sleep", side_effect=advance),
            pytest.raises(ExporterUnreachableError) as caught,
        ):
            await lease._dial_with_retry()
        assert type(caught.value) is ExporterUnreachableError
        assert clock.monotonic.return_value == lease.dial_timeout
        assert lease.controller.Dial.await_count == 2

    @pytest.mark.anyio
    async def test_readiness_retries_log_at_debug(self, caplog):
        lease = self._make_lease_for_dial()
        lease.controller.Dial = AsyncMock(side_effect=[
            MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "exporter is not ready (status: Available)"),
            Mock(router_endpoint="endpoint", router_token="token"),
        ])
        with (
            caplog.at_level(logging.DEBUG, logger="jumpstarter.client.lease"),
            patch("jumpstarter.client.lease.sleep", new_callable=AsyncMock),
        ):
            await lease._dial_with_retry()
        retry_records = [r for r in caplog.records if "retrying Dial" in r.message]
        assert len(retry_records) == 1
        assert retry_records[0].levelno == logging.DEBUG

    @pytest.mark.anyio
    async def test_controller_retries_log_at_warning(self, caplog):
        lease = self._make_lease_for_dial()
        lease.controller.Dial = AsyncMock(side_effect=[
            MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "controller restarting"),
            Mock(router_endpoint="endpoint", router_token="token"),
        ])
        with (
            caplog.at_level(logging.WARNING, logger="jumpstarter.client.lease"),
            patch("jumpstarter.client.lease.sleep", new_callable=AsyncMock),
        ):
            await lease._dial_with_retry()
        retry_records = [r for r in caplog.records if "retrying Dial" in r.message]
        assert len(retry_records) == 1
        assert retry_records[0].levelno == logging.WARNING

    @pytest.mark.anyio
    async def test_dial_deadline_allows_controller_readiness_wait(self):
        lease = self._make_lease_for_dial()
        lease.dial_timeout = 40
        deadlines = []
        response = jumpstarter_pb2.DialResponse(router_endpoint="endpoint", router_token="token")

        async def dial(request, context):
            deadlines.append(context.time_remaining())
            return response

        async with _dial_controller(dial) as lease.controller:
            assert await asyncio.wait_for(lease._dial_with_retry(), timeout=2) == response

        assert len(deadlines) == 1
        assert 30 < deadlines[0] < lease.dial_timeout

    @pytest.mark.anyio
    async def test_hung_dial_is_retried_after_grpc_deadline(self, monkeypatch):
        monkeypatch.setattr("jumpstarter.client.lease._DIAL_ATTEMPT_TIMEOUT", 0.05)
        lease = self._make_lease_for_dial()
        cancelled = asyncio.Event()
        calls = 0
        response = jumpstarter_pb2.DialResponse(router_endpoint="endpoint", router_token="token")

        async def dial(request, context):
            nonlocal calls
            calls += 1
            if calls == 1:
                try:
                    await anyio.sleep_forever()
                finally:
                    cancelled.set()
            return response

        async with _dial_controller(dial) as lease.controller:
            assert await asyncio.wait_for(lease._dial_with_retry(), timeout=2) == response
            await asyncio.wait_for(cancelled.wait(), timeout=1)

        assert calls == 2

    @pytest.mark.anyio
    async def test_hung_dial_is_bounded_by_remaining_budget(self):
        lease = self._make_lease_for_dial()
        lease.dial_timeout = 0.05
        cancelled = asyncio.Event()
        calls = 0

        async def dial(request, context):
            nonlocal calls
            calls += 1
            try:
                await anyio.sleep_forever()
            finally:
                cancelled.set()

        async with _dial_controller(dial) as lease.controller:
            with pytest.raises(ExporterUnreachableError):
                await asyncio.wait_for(lease._dial_with_retry(), timeout=1)
            await asyncio.wait_for(cancelled.wait(), timeout=1)

        assert calls == 1

    @pytest.mark.anyio
    async def test_caller_cancellation_is_not_retried(self):
        lease = self._make_lease_for_dial()
        started = asyncio.Event()
        cancelled = asyncio.Event()
        calls = 0

        async def dial(request, context):
            nonlocal calls
            calls += 1
            started.set()
            try:
                await anyio.sleep_forever()
            finally:
                cancelled.set()

        async with _dial_controller(dial) as lease.controller:
            task = asyncio.create_task(lease._dial_with_retry())
            try:
                await asyncio.wait_for(started.wait(), timeout=1)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(cancelled.wait(), timeout=1)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

        assert calls == 1

    @pytest.mark.anyio
    async def test_recovery_errors_share_one_deadline(self):
        lease = self._make_lease_for_dial()
        lease.dial_timeout = 1
        errors = [
            MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "controller restarting"),
            MockAioRpcError(grpc.StatusCode.UNKNOWN, "oidc: authenticator not initialized"),
            MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "exporter is not ready (status: Available)"),
        ]
        lease.controller.Dial = AsyncMock(side_effect=errors)
        clock = Mock()
        clock.monotonic.return_value = 0

        async def advance(delay):
            clock.monotonic.return_value += delay

        with (
            patch("jumpstarter.client.lease.time", clock),
            patch("jumpstarter.client.lease.sleep", side_effect=advance),
            pytest.raises(ExporterUnreachableError),
        ):
            await lease._dial_with_retry()
        assert clock.monotonic.return_value == lease.dial_timeout
        assert lease.controller.Dial.await_count == 3


class TestRequestAsyncExpiredLease:
    """Tests for early detection of already-ended leases in request_async."""

    def _make_lease(self, *, name="test-lease", client_name="my-client"):
        lease = object.__new__(Lease)
        lease.name = name
        lease.client_name = client_name
        lease.selector = None
        lease.get = AsyncMock()
        return lease

    @pytest.mark.anyio
    async def test_raises_when_lease_has_effective_end_time(self):
        """request_async should raise LeaseError when the lease has already ended."""
        lease = self._make_lease()
        lease.get.return_value = Mock(
            effective_end_time=datetime.now(UTC),
            client="my-client",
            selector=None,
        )

        with pytest.raises(LeaseError, match="has already ended"):
            await lease.request_async()

    @pytest.mark.anyio
    async def test_passes_when_lease_has_no_effective_end_time(self):
        """request_async should proceed to _acquire when lease is still active."""
        lease = self._make_lease()
        lease.get.return_value = Mock(
            effective_end_time=None,
            client="my-client",
            selector=None,
        )
        lease._acquire = AsyncMock(return_value=lease)

        result = await lease.request_async()
        assert result is lease
        lease._acquire.assert_called_once()


class TestAcquireExpiredLease:
    """Tests for Ready=False detection in _acquire polling loop."""

    def _make_lease(self, *, name="test-lease"):

        lease = object.__new__(Lease)
        lease.name = name
        lease.acquisition_timeout = 5
        lease._get_with_retry = AsyncMock()
        return lease

    @pytest.mark.anyio
    async def test_raises_on_ready_false_expired(self):
        """_acquire should raise LeaseError when Ready=False with reason Expired."""
        from jumpstarter_protocol import kubernetes_pb2

        lease = self._make_lease()
        lease._get_with_retry.return_value = Mock(
            conditions=[
                kubernetes_pb2.Condition(
                    type="Ready", status="False", reason="Expired",
                    message="The lease has expired",
                ),
            ],
            exporter="test-exporter",
        )

        with pytest.raises(LeaseError, match="The lease has expired"):
            await lease._acquire()

    @pytest.mark.anyio
    async def test_raises_on_ready_false_released(self):
        """_acquire should raise LeaseError when Ready=False with reason Released."""
        from jumpstarter_protocol import kubernetes_pb2

        lease = self._make_lease()
        lease._get_with_retry.return_value = Mock(
            conditions=[
                kubernetes_pb2.Condition(
                    type="Ready", status="False", reason="Released",
                    message="The lease was marked for release",
                ),
            ],
            exporter="test-exporter",
        )

        with pytest.raises(LeaseError, match="The lease was marked for release"):
            await lease._acquire()


class TestServeUnixAsync:
    """Unit tests for Lease.serve_unix_async."""

    @pytest.mark.anyio
    async def test_initial_offline_dial_still_propagates(self):
        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.dial_timeout = 0.5
        lease.controller = Mock()
        lease.controller.Dial = AsyncMock(
            side_effect=MockAioRpcError(grpc.StatusCode.FAILED_PRECONDITION, "exporter is offline")
        )

        with pytest.raises(ExporterUnreachableError, match="exporter is offline"):
            async with lease.serve_unix_async():
                pytest.fail("Listener started despite initial Dial failure")
        lease.controller.Dial.assert_awaited_once()

    @pytest.mark.anyio
    async def test_serve_unix_async_readiness_check_and_per_connection_dial(self):
        """serve_unix_async calls readiness check once, then per-connection Dial for each socket connection."""

        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.tls_config = Mock()
        lease.grpc_options = {}
        lease.controller = Mock()

        # Both the readiness check and the per-connection dial go through _dial_with_retry
        dial_calls = 0

        async def mock_dial_with_retry():
            nonlocal dial_calls
            dial_calls += 1
            return Mock(router_endpoint="test-endpoint", router_token="test-token")

        # Mock connect_router_stream
        router_stream_calls = []

        @asynccontextmanager
        async def mock_connect_router_stream(endpoint, token, stream, tls_config, grpc_options):
            router_stream_calls.append((endpoint, token, tls_config, grpc_options))
            yield

        with (
            patch.object(lease, "_dial_with_retry", side_effect=mock_dial_with_retry),
            patch("jumpstarter.client.lease.connect_router_stream", side_effect=mock_connect_router_stream),
        ):
            async with lease.serve_unix_async() as socket_path:
                # Readiness check should have been called
                assert dial_calls == 1

                # Connect to the Unix socket
                async with await anyio.connect_unix(socket_path):
                    # Give the handler time to process
                    await anyio.sleep(0.1)

        # Verify per-connection Dial was called
        assert dial_calls == 2

        # Verify connect_router_stream was called with correct args
        assert len(router_stream_calls) == 1
        endpoint, token, tls_config, grpc_options = router_stream_calls[0]
        assert endpoint == "test-endpoint"
        assert token == "test-token"
        assert tls_config is lease.tls_config
        assert grpc_options is lease.grpc_options

    @pytest.mark.anyio
    @pytest.mark.parametrize(("code", "details", "transferred"), [
        (grpc.StatusCode.NOT_FOUND, "lease not found", False),
        (grpc.StatusCode.FAILED_PRECONDITION, "exporter is offline", False),
        (grpc.StatusCode.PERMISSION_DENIED, "permission denied", True),
    ])
    async def test_serve_unix_async_per_connection_failure_preserves_listener(
        self, code, details, transferred, caplog
    ):
        """A failed command closes its socket, and a later command can connect."""
        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.tls_config = Mock()
        lease.grpc_options = {}
        lease.controller = Mock()
        lease.dial_timeout = 0.5
        lease.lease_transferred = False

        # Readiness succeeds, the first command fails, and the next recovers.
        calls = {"count": 0}

        async def mock_dial(request, *, timeout):
            calls["count"] += 1
            if calls["count"] != 2:
                return Mock(router_endpoint="test-endpoint", router_token="test-token")
            raise AioRpcError(
                code=code,
                initial_metadata=None,  # type: ignore[arg-type]
                trailing_metadata=None,  # type: ignore[arg-type]
                details=details,
            )

        lease.controller.Dial = mock_dial
        served = anyio.Event()

        @asynccontextmanager
        async def mock_connect_router_stream(*args):
            served.set()
            yield

        with (
            patch("jumpstarter.client.lease.connect_router_stream", mock_connect_router_stream),
            anyio.fail_after(2),
            caplog.at_level(logging.WARNING, logger="jumpstarter.client.lease"),
        ):
            async with lease.serve_unix_async() as socket_path:
                async with await anyio.connect_unix(socket_path) as stream:
                    with pytest.raises(anyio.EndOfStream):
                        await stream.receive()
                async with await anyio.connect_unix(socket_path):
                    await served.wait()

        assert calls["count"] == 3
        assert lease.lease_transferred is transferred
        assert any("Closing connection after tunnel failure" in record.message for record in caplog.records)
        if transferred:
            assert any("transferred to another client" in record.message for record in caplog.records)

    @pytest.mark.anyio
    async def test_router_setup_failure_closes_only_its_connection(self, caplog):
        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.tls_config = Mock()
        lease.grpc_options = {}
        response = Mock(router_endpoint="endpoint", router_token="token")
        first_finished = anyio.Event()
        release_first = anyio.Event()
        router_calls = 0

        @asynccontextmanager
        async def connect_router(*args):
            nonlocal router_calls
            router_calls += 1
            call = router_calls
            stream = args[2]
            if call == 2:
                raise OSError("router setup failed")
            await stream.send(b"active" if call == 1 else b"recovered")
            try:
                yield
                if call == 1:
                    await release_first.wait()
            finally:
                if call == 1:
                    first_finished.set()

        with (
            patch.object(lease, "_dial_with_retry", new_callable=AsyncMock, return_value=response),
            patch("jumpstarter.client.lease.connect_router_stream", connect_router),
            caplog.at_level(logging.WARNING, logger="jumpstarter.client.lease"),
            anyio.fail_after(2),
        ):
            async with lease.serve_unix_async() as socket_path, await anyio.connect_unix(socket_path) as first:
                assert await first.receive() == b"active"
                async with await anyio.connect_unix(socket_path) as failed:
                    with pytest.raises(anyio.EndOfStream):
                        await failed.receive()
                assert not first_finished.is_set()
                async with await anyio.connect_unix(socket_path) as recovered:
                    assert await recovered.receive() == b"recovered"
                release_first.set()
                await first_finished.wait()

        assert router_calls == 3
        assert any("router setup failed" in record.message for record in caplog.records)

    @pytest.mark.anyio
    async def test_serve_unix_async_per_connection_dial_survives_transient_failure(self):
        """A per-connection Dial blip is retried instead of tearing down the session."""
        from grpc import StatusCode

        lease = object.__new__(Lease)
        lease.name = "test-lease"
        lease.exporter_name = "test-exporter"
        lease.tls_config = Mock()
        lease.grpc_options = {}
        lease.controller = Mock()
        lease.dial_timeout = 5.0

        calls = {"count": 0}

        async def mock_dial(request, *, timeout):
            calls["count"] += 1
            # readiness check, then one blip, then success
            if calls["count"] == 2:
                raise AioRpcError(
                    code=StatusCode.UNAVAILABLE,
                    initial_metadata=None,  # type: ignore[arg-type]
                    trailing_metadata=None,  # type: ignore[arg-type]
                    details="transient",
                )
            return Mock(router_endpoint="test-endpoint", router_token="test-token")

        lease.controller.Dial = mock_dial

        router_stream_calls = []

        @asynccontextmanager
        async def mock_connect_router_stream(endpoint, token, stream, tls_config, grpc_options):
            router_stream_calls.append(endpoint)
            yield

        with patch("jumpstarter.client.lease.connect_router_stream", side_effect=mock_connect_router_stream):
            async with lease.serve_unix_async() as socket_path, await anyio.connect_unix(socket_path):
                await anyio.sleep(1)

        # The connection was served despite the blip
        assert router_stream_calls == ["test-endpoint"]


@pytest.mark.anyio
async def test_dial_timeout_does_not_poison_next_shell_connection():
    lease = object.__new__(Lease)
    lease.name = "test-lease"
    lease.exporter_name = "test-exporter"
    lease.tls_config = Mock()
    lease.grpc_options = {}
    lease.dial_timeout = 0.02
    lease.controller = Mock()
    calls = 0
    recovered = False
    served = anyio.Event()

    async def dial(request, *, timeout):
        nonlocal calls
        calls += 1
        if calls != 1 and not recovered:
            raise MockAioRpcError(grpc.StatusCode.UNAVAILABLE, "controller restarting")
        return Mock(router_endpoint="endpoint", router_token="token")

    @asynccontextmanager
    async def connect_router(*args):
        served.set()
        yield

    lease.controller.Dial = dial
    with patch("jumpstarter.client.lease.connect_router_stream", connect_router), anyio.fail_after(1):
        async with lease.serve_unix_async() as path:
            async with await anyio.connect_unix(path) as stream:
                with pytest.raises(anyio.EndOfStream):
                    await stream.receive()
            recovered = True
            async with await anyio.connect_unix(path):
                await served.wait()
    assert calls == 3
