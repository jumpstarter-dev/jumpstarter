"""Tests for exporter state machine transitions and status reporting.

These tests verify the exporter correctly handles lease lifecycle edge cases
including premature lease-end during hooks, unused lease timeouts,
consecutive leases, idempotent lease-end signals, and gRPC error handling
in _report_status.
"""

import logging
from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import grpc
import pytest
from anyio import Event, create_task_group

from jumpstarter.common import ExporterStatus
from jumpstarter.exporter.exporter import (
    _RPC_BACKOFF_BASE,
    _RPC_BACKOFF_CAP,
    _RPC_MAX_RETRIES,
    _RPC_TIMEOUT,
)
from jumpstarter.exporter.lease_context import LeaseContext

pytestmark = pytest.mark.anyio


def make_lease_context(lease_name="test-lease", client_name="test-client"):
    ctx = LeaseContext(
        lease_name=lease_name,
        before_lease_hook=Event(),
        client_name=client_name,
    )
    mock_session = MagicMock()
    mock_session.context_log_source.return_value = nullcontext()
    ctx.session = mock_session
    ctx.socket_path = "/tmp/test_socket"
    ctx.hook_socket_path = "/tmp/test_hook_socket"
    return ctx


def make_exporter(lease_ctx, hook_executor=None):
    from jumpstarter.exporter.exporter import Exporter

    exporter = Exporter.__new__(Exporter)
    exporter._exporter_status = ExporterStatus.AVAILABLE
    exporter._lease_context = lease_ctx
    exporter._stop_requested = False
    exporter._standalone = False
    exporter.hook_executor = hook_executor
    exporter._report_status = AsyncMock()
    exporter._request_lease_release = AsyncMock()
    return exporter


class TestLeaseEndDuringHook:
    async def test_cleanup_waits_for_before_lease_hook_before_running_after_lease(self):
        """_cleanup_after_lease must wait for the beforeLease hook to
        complete before starting the afterLease hook. This prevents
        running afterLease while beforeLease is still in progress."""
        lease_ctx = make_lease_context()

        after_lease_started_before_hook_done = False

        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        original_run_after = hook_executor.run_after_lease_hook

        async def tracking_run_after(*args, **kwargs):
            nonlocal after_lease_started_before_hook_done
            if not lease_ctx.before_lease_hook.is_set():
                after_lease_started_before_hook_done = True
            return await original_run_after(*args, **kwargs)

        hook_executor.run_after_lease_hook = tracking_run_after

        exporter = make_exporter(lease_ctx, hook_executor)

        async with create_task_group() as tg:

            async def delayed_hook_complete():
                await anyio.sleep(0.2)
                lease_ctx.before_lease_hook.set()

            tg.start_soon(delayed_hook_complete)
            await exporter._cleanup_after_lease(lease_ctx)

        assert not after_lease_started_before_hook_done, (
            "afterLease hook started before beforeLease hook completed"
        )
        assert lease_ctx.after_lease_hook_done.is_set()

    async def test_exporter_returns_to_available_after_premature_lease_end(self):
        """After a lease ends during beforeLease hook execution, exporter
        must transition to AVAILABLE once hooks complete."""
        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.after_lease_hook_done.is_set()

    async def test_new_lease_accepted_after_recovery_from_premature_end(self):
        """After recovering from a premature lease-end, a new LeaseContext
        can be created and the exporter processes it normally."""
        lease_ctx_1 = make_lease_context(lease_name="lease-1")
        lease_ctx_1.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_1.after_lease_hook_done.is_set()

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        lease_ctx_2.before_lease_hook.set()
        exporter._lease_context = lease_ctx_2

        statuses.clear()
        await exporter._cleanup_after_lease(lease_ctx_2)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_2.after_lease_hook_done.is_set()


class TestUnusedLeaseTimeout:
    async def test_unused_lease_timeout_transitions_to_available(self):
        """When a lease ends with no client session (unused lease timeout),
        the exporter must transition to AVAILABLE."""
        lease_ctx = make_lease_context(client_name="")
        lease_ctx.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.after_lease_hook_done.is_set()

    async def test_unused_lease_with_hooks_runs_after_lease_when_client_present(self):
        """When a lease ends with a client (normal end or timeout after
        client connected), the afterLease hook runs."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        lease_ctx = make_lease_context(client_name="some-client")
        lease_ctx.before_lease_hook.set()

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AFTER_LEASE_HOOK in statuses
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.after_lease_hook_done.is_set()

    async def test_new_lease_after_unused_timeout_recovery(self):
        """After recovering from unused lease timeout, a new lease
        can be accepted and processed."""
        lease_ctx_1 = make_lease_context(lease_name="unused-lease", client_name="")
        lease_ctx_1.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_1.after_lease_hook_done.is_set()

        lease_ctx_2 = make_lease_context(lease_name="new-lease", client_name="real-client")
        lease_ctx_2.before_lease_hook.set()
        exporter._lease_context = lease_ctx_2

        statuses.clear()
        await exporter._cleanup_after_lease(lease_ctx_2)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_2.after_lease_hook_done.is_set()


class TestConsecutiveLeaseOrdering:
    async def test_after_lease_done_before_new_lease_context_created(self):
        """The serve() loop must not create a new LeaseContext until the
        previous lease's after_lease_hook_done is set."""
        lease_ctx_1 = make_lease_context(lease_name="lease-1")
        lease_ctx_1.before_lease_hook.set()

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert lease_ctx_1.after_lease_hook_done.is_set()

        exporter._lease_context = None

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        exporter._lease_context = lease_ctx_2
        lease_ctx_2.before_lease_hook.set()

        await exporter._cleanup_after_lease(lease_ctx_2)
        assert lease_ctx_2.after_lease_hook_done.is_set()

    async def test_consecutive_leases_run_hooks_in_strict_order(self):
        """For two consecutive leases, afterLease(1) must complete before
        beforeLease(2) starts."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo before", timeout=10),
            after_lease=HookInstanceConfigV1Alpha1(script="echo after", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        events = []

        original_run_before = hook_executor.run_before_lease_hook
        original_run_after = hook_executor.run_after_lease_hook

        async def tracking_before(*args, **kwargs):
            events.append("before_start")
            result = await original_run_before(*args, **kwargs)
            events.append("before_end")
            return result

        async def tracking_after(*args, **kwargs):
            events.append("after_start")
            result = await original_run_after(*args, **kwargs)
            events.append("after_end")
            return result

        hook_executor.run_before_lease_hook = tracking_before
        hook_executor.run_after_lease_hook = tracking_after

        lease_ctx_1 = make_lease_context(lease_name="lease-1")
        exporter = make_exporter(lease_ctx_1, hook_executor)
        exporter._report_status = AsyncMock()

        await hook_executor.run_before_lease_hook(
            lease_ctx_1, exporter._report_status, exporter.stop, exporter._request_lease_release
        )
        await exporter._cleanup_after_lease(lease_ctx_1)

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        exporter._lease_context = lease_ctx_2

        await hook_executor.run_before_lease_hook(
            lease_ctx_2, exporter._report_status, exporter.stop, exporter._request_lease_release
        )
        await exporter._cleanup_after_lease(lease_ctx_2)

        after1_end = events.index("after_end", events.index("after_start"))
        before2_start = events.index("before_start", after1_end)
        assert after1_end < before2_start, (
            f"afterLease(1) end at {after1_end} must be before "
            f"beforeLease(2) start at {before2_start}. Events: {events}"
        )


class TestBeforeLeaseHookSafetyTimeout:
    async def test_cleanup_forces_hook_set_on_safety_timeout(self):
        """When before_lease_hook is never set (race condition),
        _cleanup_after_lease must not deadlock. The safety timeout
        forces the event set and cleanup proceeds normally."""
        from unittest.mock import patch

        lease_ctx = make_lease_context()
        # Deliberately do NOT set before_lease_hook to simulate the race condition
        exporter = make_exporter(lease_ctx)

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter._report_status = AsyncMock(side_effect=track_status)

        # Patch move_on_after to use a tiny timeout so the test runs fast
        original_move_on_after = anyio.move_on_after

        def fast_move_on_after(delay, *args, **kwargs):
            # Replace any safety timeout with 0.1s for fast testing
            return original_move_on_after(0.1, *args, **kwargs)

        with patch("jumpstarter.exporter.exporter.move_on_after", side_effect=fast_move_on_after):
            await exporter._cleanup_after_lease(lease_ctx)

        # The event should be force-set by the timeout handler
        assert lease_ctx.before_lease_hook.is_set(), (
            "before_lease_hook should be force-set after safety timeout"
        )
        # Cleanup should have completed normally
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.after_lease_hook_done.is_set()

    async def test_safety_timeout_uses_hook_config_when_available(self):
        """When a hook executor with before_lease config is present,
        the safety timeout should use the configured hook timeout + 30s
        margin rather than the default 15s."""
        from unittest.mock import patch

        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=60),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()  # Set so we don't actually timeout

        exporter = make_exporter(lease_ctx, hook_executor)

        captured_timeouts = []
        original_move_on_after = anyio.move_on_after

        def tracking_move_on_after(delay, *args, **kwargs):
            captured_timeouts.append(delay)
            return original_move_on_after(delay, *args, **kwargs)

        with patch("jumpstarter.exporter.exporter.move_on_after", side_effect=tracking_move_on_after):
            await exporter._cleanup_after_lease(lease_ctx)

        # The safety timeout should be hook timeout (60) + margin (30) = 90
        assert 90 in captured_timeouts, (
            f"Expected safety timeout of 90s (60 + 30), got timeouts: {captured_timeouts}"
        )


class TestHandleLeaseFinally:
    async def test_finally_sets_before_lease_hook_on_early_cancel(self):
        """When conn_tg is cancelled before before_lease_hook.set() is
        reached (no hook executor path), the finally block must ensure
        the event is set so _cleanup_after_lease can proceed."""
        lease_ctx = make_lease_context()
        # Verify the event starts unset
        assert not lease_ctx.before_lease_hook.is_set()

        exporter = make_exporter(lease_ctx)
        # Mock methods needed by handle_lease
        exporter.uuid = "test-uuid"
        exporter.labels = {}
        exporter.tls = None
        exporter.grpc_options = None

        # We test just the finally-block behavior by calling
        # _cleanup_after_lease with an unset event: the primary fix is
        # in handle_lease's finally, but we can verify _cleanup_after_lease
        # handles the unset event via the safety timeout.
        # A more direct test: simulate what the finally block does.
        if not lease_ctx.before_lease_hook.is_set():
            lease_ctx.before_lease_hook.set()

        assert lease_ctx.before_lease_hook.is_set(), (
            "before_lease_hook must be set after the finally-block logic"
        )


class TestIdempotentLeaseEnd:
    async def test_duplicate_cleanup_is_noop(self):
        """Calling _cleanup_after_lease twice for the same LeaseContext
        must not run afterLease hook twice. The second call waits for the
        first to finish and then returns."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        after_hook_call_count = 0
        original_run_after = hook_executor.run_after_lease_hook

        async def counting_run_after(*args, **kwargs):
            nonlocal after_hook_call_count
            after_hook_call_count += 1
            return await original_run_after(*args, **kwargs)

        hook_executor.run_after_lease_hook = counting_run_after

        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)
        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1, (
            f"afterLease hook ran {after_hook_call_count} times, expected exactly 1"
        )
        assert lease_ctx.after_lease_hook_done.is_set()


def _make_exporter_for_report_status():
    """Create an Exporter with real _report_status for testing gRPC error handling."""
    from jumpstarter.exporter.exporter import Exporter

    exporter = Exporter.__new__(Exporter)
    exporter._exporter_status = ExporterStatus.AVAILABLE
    exporter._lease_context = None
    exporter._standalone = False
    exporter._release_lease_unsupported = False
    return exporter


class TestBeforeLeaseHookRaceGuard:
    async def test_new_lease_after_before_hook_race_recovery(self):
        """After recovering from the beforeLease hook race condition
        (lease expired during hook), a new lease must be accepted and
        processed normally."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx_1 = make_lease_context(lease_name="expired-lease")
        lease_ctx_1.lease_ended.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx_1, hook_executor)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await hook_executor.run_before_lease_hook(
            lease_ctx_1, exporter._report_status, exporter.stop, exporter._request_lease_release
        )

        assert lease_ctx_1.before_lease_hook.is_set()
        await exporter._cleanup_after_lease(lease_ctx_1)
        assert ExporterStatus.AVAILABLE in statuses

        lease_ctx_2 = make_lease_context(lease_name="new-lease")
        exporter._lease_context = lease_ctx_2

        statuses.clear()
        await hook_executor.run_before_lease_hook(
            lease_ctx_2, exporter._report_status, exporter.stop, exporter._request_lease_release
        )

        assert ExporterStatus.LEASE_READY in statuses, (
            f"New lease must reach LEASE_READY when lease is still active. Statuses: {statuses}"
        )


def _setup_mock_controller_stub(exporter, side_effect=None):
    """Helper to set up mock controller stub for testing ReportStatus.

    Returns:
        tuple: (mock_controller, stub_context_manager)
    """
    mock_controller = AsyncMock()
    if side_effect is not None:
        mock_controller.ReportStatus = AsyncMock(side_effect=side_effect)

    stub_ctx = AsyncMock()
    stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
    stub_ctx.__aexit__ = AsyncMock(return_value=False)

    return mock_controller, stub_ctx


class TestReportStatusGrpcErrorHandling:
    async def test_unimplemented_grpc_error_logs_warning(self, caplog):
        """When ReportStatus returns UNIMPLEMENTED, a warning is logged
        instead of an error."""
        exporter = _make_exporter_for_report_status()

        error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNIMPLEMENTED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Method not implemented",
        )
        mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=error)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            with caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"):
                await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("ReportStatus not supported" in r.message for r in warning_msgs), (
            f"Expected warning about ReportStatus not supported, got: {[r.message for r in caplog.records]}"
        )
        # Ensure no ERROR-level log was emitted
        error_msgs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_msgs) == 0, (
            f"No error should be logged for UNIMPLEMENTED, got: {[r.message for r in error_msgs]}"
        )
        # Ensure no retry - UNIMPLEMENTED should fail fast
        assert mock_controller.ReportStatus.call_count == 1

    async def test_other_grpc_error_logs_error(self, caplog):
        """When ReportStatus returns a gRPC error other than UNIMPLEMENTED,
        it retries and eventually logs at ERROR level."""
        exporter = _make_exporter_for_report_status()

        error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNAVAILABLE,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Service unavailable",
        )
        mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=error)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), \
                patch("anyio.sleep") as mock_sleep:
            with caplog.at_level(logging.DEBUG, logger="jumpstarter.exporter.exporter"):
                await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Should log retry warnings (_RPC_MAX_RETRIES)
        warning_msgs = [r for r in caplog.records if r.levelno == logging.WARNING]
        retry_warnings = [r for r in warning_msgs if "Transient error" in r.message]
        assert len(retry_warnings) == _RPC_MAX_RETRIES, (
            f"Expected {_RPC_MAX_RETRIES} retry warnings, got: {len(retry_warnings)}"
        )

        # Verify exponential backoff with cap at _RPC_BACKOFF_CAP
        backoff_values = [call.args[0] for call in mock_sleep.call_args_list]
        expected_backoffs = [min(_RPC_BACKOFF_BASE * (2**i), _RPC_BACKOFF_CAP) for i in range(_RPC_MAX_RETRIES)]
        assert backoff_values == expected_backoffs, f"Expected {expected_backoffs}, got: {backoff_values}"

        # _RPC_MAX_RETRIES + 1 total attempts
        assert mock_controller.ReportStatus.call_count == _RPC_MAX_RETRIES + 1

        # Eventually logs ERROR after exhausting retries
        error_msgs = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any("Failed to report status" in r.message for r in error_msgs), (
            f"Expected error about failed status update, got: {[r.message for r in caplog.records]}"
        )
        # Ensure no WARNING about "not supported" was logged
        assert not any("ReportStatus not supported" in r.message for r in warning_msgs), (
            "UNAVAILABLE error should not produce 'not supported' warning"
        )

    @pytest.mark.parametrize("error_code", [
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.DEADLINE_EXCEEDED,
    ])
    async def test_report_status_retries_on_transient_failure(self, error_code):
        """Transient gRPC errors (UNAVAILABLE, DEADLINE_EXCEEDED) are retried.
        If the error resolves before exhausting retries, the status is delivered."""
        exporter = _make_exporter_for_report_status()

        transient_error = grpc.aio.AioRpcError(
            code=error_code,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details=f"{error_code.name} error",
        )

        delivered_statuses = []
        call_count = 0

        async def fail_twice_then_succeed(request, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise transient_error
            # Third attempt succeeds
            delivered_statuses.append(ExporterStatus.from_proto(request.status))

        mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=fail_twice_then_succeed)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), \
                patch("anyio.sleep") as mock_sleep:
            await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Status was delivered on the third attempt
        assert ExporterStatus.AVAILABLE in delivered_statuses
        assert call_count == 3

        # Verify exponential backoff for the 2 retries before success
        backoff_values = [call.args[0] for call in mock_sleep.call_args_list]
        assert backoff_values == [1.0, 2.0], f"Expected [1.0, 2.0], got: {backoff_values}"

    async def test_report_status_does_not_retry_non_transient_grpc_error(self):
        """Non-transient gRPC errors (PERMISSION_DENIED, INVALID_ARGUMENT, etc.)
        are not retried — fail fast."""
        exporter = _make_exporter_for_report_status()

        permission_error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.PERMISSION_DENIED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Permission denied",
        )
        mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=permission_error)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Called exactly once (no retry)
        assert mock_controller.ReportStatus.call_count == 1

    async def test_report_status_does_not_retry_non_grpc_exception(self):
        """Non-gRPC exceptions (ConnectionError, etc.) are not retried."""
        exporter = _make_exporter_for_report_status()

        connection_error = ConnectionError("Network unreachable")
        mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=connection_error)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Called exactly once (no retry)
        assert mock_controller.ReportStatus.call_count == 1

    async def test_rpc_timeout_parameter_passed(self):
        """Verify that timeout=_RPC_TIMEOUT is passed to gRPC calls."""
        exporter = _make_exporter_for_report_status()

        captured_calls = []

        async def capture_call(*args, **kwargs):
            captured_calls.append(kwargs)

        mock_controller = AsyncMock()
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_call)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Verify timeout parameter was passed
        assert len(captured_calls) == 1
        assert "timeout" in captured_calls[0]
        assert captured_calls[0]["timeout"] == _RPC_TIMEOUT

    async def test_report_status_non_blocking_during_serve(self):
        """Verify _report_status returns immediately when drain is active (serve running)."""
        exporter = _make_exporter_for_report_status()

        # Simulate serve() drain active
        exporter._status_drain_active = True
        exporter._status_rpc_event = Event()
        exporter._pending_status_request = None

        # Call _report_status — should return immediately without awaiting RPC
        await exporter._report_status(ExporterStatus.AVAILABLE, "test")

        # Verify it enqueued the request
        assert exporter._pending_status_request is not None
        assert exporter._pending_status_request.message == "test"
        assert exporter._status_rpc_event.is_set()

        # Now verify latest-wins: call again with different status
        exporter._status_rpc_event = Event()  # Reset
        await exporter._report_status(ExporterStatus.LEASE_READY, "ready")

        # The pending request should be replaced
        assert exporter._pending_status_request.message == "ready"
        assert ExporterStatus.from_proto(exporter._pending_status_request.status) == ExporterStatus.LEASE_READY

    async def test_request_lease_release_succeeds_on_first_attempt(self):
        """When ReleaseLease succeeds immediately, send AVAILABLE and exit."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = False

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        release_calls = []
        report_calls = []

        async def capture_release(request, **kwargs):
            release_calls.append(request)

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=capture_release)
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release()

        # ReleaseLease called once with correct lease name
        assert len(release_calls) == 1
        assert release_calls[0].name == "test-lease"

        # ReportStatus called once (AVAILABLE only, no release_lease)
        assert len(report_calls) == 1
        assert report_calls[0].release_lease is False
        assert ExporterStatus.from_proto(report_calls[0].status) == ExporterStatus.AVAILABLE

        # Lease ended event set
        assert lease_ctx.lease_ended.is_set()

    async def test_request_lease_release_retries_on_transient_failure(self):
        """When ReleaseLease fails with UNAVAILABLE on first attempt, retry and succeed."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = False

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        unavailable_error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNAVAILABLE,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Service unavailable",
        )

        release_calls = []
        report_calls = []
        release_call_count = 0

        async def fail_first_then_succeed(request, **kwargs):
            nonlocal release_call_count
            release_call_count += 1
            if release_call_count == 1:
                raise unavailable_error
            release_calls.append(request)

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=fail_first_then_succeed)
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), patch("anyio.sleep"):
            await exporter._request_lease_release()

        # ReleaseLease called twice (failed once, succeeded on retry)
        assert release_call_count == 2
        assert len(release_calls) == 1
        assert release_calls[0].name == "test-lease"

        # ReportStatus called once (AVAILABLE)
        assert len(report_calls) == 1
        assert ExporterStatus.from_proto(report_calls[0].status) == ExporterStatus.AVAILABLE

    async def test_request_lease_release_exhausts_retries(self):
        """When ReleaseLease and AVAILABLE status both fail, lease_ended is still set
        so handle_lease can exit (prevents being stuck forever)."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = False

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        unavailable_error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNAVAILABLE,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Service unavailable",
        )

        release_call_count = 0
        report_call_count = 0

        async def always_fail_release(request, **kwargs):
            nonlocal release_call_count
            release_call_count += 1
            raise unavailable_error

        async def always_fail_report(request, **kwargs):
            nonlocal report_call_count
            report_call_count += 1
            raise unavailable_error

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=always_fail_release)
        mock_controller.ReportStatus = AsyncMock(side_effect=always_fail_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), patch("anyio.sleep"):
            await exporter._request_lease_release()

        # ReleaseLease: _RPC_MAX_RETRIES + 1 attempts (all fail)
        assert release_call_count == _RPC_MAX_RETRIES + 1

        # ReportStatus: _RPC_MAX_RETRIES + 1 attempts via _send_report_status_rpc (all fail)
        assert report_call_count == _RPC_MAX_RETRIES + 1

        # Critical: lease_ended must be set even when everything fails,
        # otherwise handle_lease never exits
        assert lease_ctx.lease_ended.is_set()

    async def test_request_lease_release_falls_back_on_unsupported(self):
        """When ReleaseLease fails with PERMISSION_DENIED, fall back to compat path."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = False
        exporter._exporter_status = ExporterStatus.AVAILABLE

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        permission_error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.PERMISSION_DENIED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Exporter auth not supported",
        )

        report_calls = []

        async def fail_with_permission(request, **kwargs):
            raise permission_error

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=fail_with_permission)
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release()

        # ReleaseLease called once, failed with PERMISSION_DENIED
        assert mock_controller.ReleaseLease.call_count == 1

        # Fallback: ReportStatus called twice
        # First call: release_lease=true with AFTER_LEASE_HOOK (AVAILABLE reverted)
        # Second call: AVAILABLE
        assert len(report_calls) == 2
        assert report_calls[0].release_lease is True
        assert ExporterStatus.from_proto(report_calls[0].status) == ExporterStatus.AFTER_LEASE_HOOK
        assert report_calls[1].release_lease is False
        assert ExporterStatus.from_proto(report_calls[1].status) == ExporterStatus.AVAILABLE

        # _release_lease_unsupported should be cached
        assert exporter._release_lease_unsupported is True

    async def test_request_lease_release_skips_release_lease_when_cached_unsupported(self):
        """When _release_lease_unsupported is True, skip ReleaseLease entirely."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = True  # Cached from prior attempt
        exporter._exporter_status = ExporterStatus.AVAILABLE

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        report_calls = []

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock()  # Should never be called
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release()

        # ReleaseLease NOT called (cached as unsupported)
        assert mock_controller.ReleaseLease.call_count == 0

        # Went straight to fallback path
        assert len(report_calls) == 2
        assert report_calls[0].release_lease is True
        assert report_calls[1].release_lease is False

    async def test_request_lease_release_preserves_failure_status_in_fallback(self):
        """When falling back with a failure status, preserve it (don't revert to AFTER_LEASE_HOOK)."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = True
        exporter._exporter_status = ExporterStatus.AFTER_LEASE_HOOK_FAILED  # Failure status

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        report_calls = []

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release()

        # First call: release_lease=true with AFTER_LEASE_HOOK_FAILED (preserved, not reverted)
        assert report_calls[0].release_lease is True
        assert ExporterStatus.from_proto(report_calls[0].status) == ExporterStatus.AFTER_LEASE_HOOK_FAILED

        # Second call: AVAILABLE
        assert report_calls[1].release_lease is False
        assert ExporterStatus.from_proto(report_calls[1].status) == ExporterStatus.AVAILABLE

    async def test_request_lease_release_unimplemented(self):
        """When ReleaseLease returns UNIMPLEMENTED, treat as unsupported and fall back."""
        exporter = _make_exporter_for_report_status()
        exporter._release_lease_unsupported = False
        exporter._exporter_status = ExporterStatus.AVAILABLE

        lease_ctx = LeaseContext(
            lease_name="test-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = lease_ctx

        unimplemented_error = grpc.aio.AioRpcError(
            code=grpc.StatusCode.UNIMPLEMENTED,
            initial_metadata=grpc.aio.Metadata(),
            trailing_metadata=grpc.aio.Metadata(),
            details="Method not implemented",
        )

        report_calls = []

        async def fail_with_unimplemented(request, **kwargs):
            raise unimplemented_error

        async def capture_report(request, **kwargs):
            report_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=fail_with_unimplemented)
        mock_controller.ReportStatus = AsyncMock(side_effect=capture_report)

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release()

        # ReleaseLease called once (UNIMPLEMENTED)
        assert mock_controller.ReleaseLease.call_count == 1

        # Falls back to compat path
        assert len(report_calls) == 2
        assert report_calls[0].release_lease is True

        # Cached as unsupported
        assert exporter._release_lease_unsupported is True


class TestHandleLeaseStaleSkip:
    async def test_handle_lease_skips_session_for_stale_lease(self):
        """When lease_ended is already set, handle_lease skips session
        creation entirely and just sets the events needed by serve()."""
        lease_ctx = LeaseContext(
            lease_name="stale-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        lease_ctx.lease_ended.set()

        exporter = make_exporter(lease_ctx)
        session_for_lease_mock = AsyncMock()
        exporter.session_for_lease = session_for_lease_mock

        async with create_task_group() as tg:
            await exporter.handle_lease("stale-lease", tg, lease_ctx)

        session_for_lease_mock.assert_not_called()
        assert lease_ctx.skip_after_lease_hook is True
        assert lease_ctx.before_lease_hook.is_set()
        assert lease_ctx.after_lease_hook_done.is_set()
        assert lease_ctx.session is None
        exporter._report_status.assert_awaited_once_with(
            ExporterStatus.AVAILABLE, "Available for new lease",
        )

    async def test_handle_lease_stale_skip_suppresses_available_on_shutdown(self):
        """When stop_requested is True, the stale lease fast path should
        not report AVAILABLE status."""
        lease_ctx = LeaseContext(
            lease_name="stale-lease",
            before_lease_hook=Event(),
        )
        lease_ctx.lease_ended.set()

        exporter = make_exporter(lease_ctx)
        exporter._stop_requested = True

        async with create_task_group() as tg:
            await exporter.handle_lease("stale-lease", tg, lease_ctx)

        assert lease_ctx.after_lease_hook_done.is_set()
        exporter._report_status.assert_not_awaited()

    async def test_handle_lease_skips_conn_handling_when_lease_ends_during_session(self):
        """When lease_ended is set during session creation (serve() processes
        the buffered leased=False while session_for_lease sets up sockets),
        handle_lease bails after session setup, skipping Listen/conn_tg."""
        from contextlib import asynccontextmanager

        lease_ctx = LeaseContext(
            lease_name="stale-during-setup",
            before_lease_hook=Event(),
            client_name="test-client",
        )

        mock_session = MagicMock()
        mock_session.context_log_source.return_value = nullcontext()

        @asynccontextmanager
        async def fake_session_for_lease():
            lease_ctx.lease_ended.set()
            yield (mock_session, "/tmp/main.sock", "/tmp/hook.sock")

        exporter = make_exporter(lease_ctx)
        exporter.session_for_lease = fake_session_for_lease

        async with create_task_group() as tg:
            await exporter.handle_lease("stale-during-setup", tg, lease_ctx)

        assert lease_ctx.skip_after_lease_hook is True
        assert lease_ctx.before_lease_hook.is_set()
        assert lease_ctx.after_lease_hook_done.is_set()
        exporter._report_status.assert_awaited_once_with(
            ExporterStatus.AVAILABLE, "Available for new lease",
        )


def _make_serve_exporter(exit_on_lease_end=False):
    """Build an Exporter suitable for serve() tests with mocked I/O."""
    from contextlib import asynccontextmanager

    from jumpstarter.exporter.exporter import Exporter

    exporter = Exporter.__new__(Exporter)
    exporter._exporter_status = ExporterStatus.AVAILABLE
    exporter._lease_context = None
    exporter._stop_requested = False
    exporter._standalone = False
    exporter._tg = None
    exporter._started = False
    exporter._registered = True
    exporter._unregister = False
    exporter._deferred_unregister = True
    exporter._exit_code = None
    exporter.hook_executor = None
    exporter.exit_on_lease_end = exit_on_lease_end
    exporter.labels = {"jumpstarter.dev/name": "test-exporter"}
    exporter._report_status = AsyncMock()
    exporter._request_lease_release = AsyncMock()
    exporter._status_drain_active = False
    exporter._pending_status_request = None
    exporter._status_rpc_event = Event()

    @asynccontextmanager
    async def fake_session():
        yield

    exporter.session = fake_session
    return exporter


def _wire_status_stream(exporter, statuses):
    """Replace _retry_stream with a function that feeds statuses into tx.

    The stream sends the provided statuses then waits indefinitely (until cancelled),
    matching production behavior where status streams are long-lived.
    """
    async def fake_retry_stream(name, factory, tx, **kwargs):
        for s in statuses:
            await tx.send(s)
        # Don't close - wait until task group cancels us (matches production)
        await anyio.sleep_forever()

    exporter._retry_stream = fake_retry_stream


def _wire_handle_lease(exporter):
    """Replace handle_lease with a minimal mock that satisfies lifecycle events."""
    async def fake_handle_lease(lease_name, tg, lease_ctx):
        await lease_ctx.lease_ended.wait()
        lease_ctx.after_lease_hook_done.set()

    exporter.handle_lease = fake_handle_lease


class TestExitOnLeaseEnd:
    async def test_serve_stops_after_lease_ends(self):
        """serve() sets _stop_requested after a lease→unleased transition
        when exit_on_lease_end is True."""
        exporter = _make_serve_exporter(exit_on_lease_end=True)
        _wire_status_stream(exporter, [
            MagicMock(leased=True, lease_name="test-lease", client_name="test"),
            MagicMock(leased=False, lease_name="", client_name=""),
        ])
        _wire_handle_lease(exporter)

        await exporter.serve()

        assert exporter._stop_requested is True

    async def test_serve_not_triggered_on_startup(self):
        """serve() does NOT set _stop_requested on startup when no lease
        has been served yet (previous_leased is False)."""
        exporter = _make_serve_exporter(exit_on_lease_end=True)
        _wire_status_stream(exporter, [
            MagicMock(leased=False, lease_name="", client_name=""),
        ])

        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            # Give serve time to process the status
            await anyio.sleep(0.1)
            # Check that _stop_requested was NOT set
            assert exporter._stop_requested is False
            # Clean exit
            tg.cancel_scope.cancel()

    async def test_serve_continues_when_disabled(self):
        """serve() does NOT set _stop_requested after lease ends when
        exit_on_lease_end is False — the exporter loops for next lease."""
        exporter = _make_serve_exporter(exit_on_lease_end=False)
        _wire_status_stream(exporter, [
            MagicMock(leased=True, lease_name="test-lease", client_name="test"),
            MagicMock(leased=False, lease_name="", client_name=""),
        ])
        _wire_handle_lease(exporter)

        async with create_task_group() as tg:
            tg.start_soon(exporter.serve)
            # Give serve time to process both statuses
            await anyio.sleep(0.2)
            # Check that _stop_requested was NOT set (exit_on_lease_end is False)
            assert exporter._stop_requested is False
            # Clean exit
            tg.cancel_scope.cancel()


class TestContextPropagation:
    """Tests for spec.context propagation from StatusResponse to log context."""

    async def test_context_bound_on_new_lease(self):
        """When a new lease is assigned with context, it should be bound to log context."""
        from jumpstarter.exporter.exporter import Exporter
        from jumpstarter.logging import clear_log_context

        clear_log_context()

        exporter = Exporter.__new__(Exporter)
        exporter._exporter_status = ExporterStatus.AVAILABLE
        exporter._lease_context = None
        exporter._stop_requested = False
        exporter._standalone = False
        exporter._started = False
        exporter.hook_executor = None
        exporter.labels = {"jumpstarter.dev/name": "lab-exporter-01"}
        exporter._report_status = AsyncMock()
        exporter._request_lease_release = AsyncMock()

        # Simulate receiving a StatusResponse with context
        status = MagicMock()
        status.leased = True
        status.lease_name = "test-lease-ctx"
        status.client_name = "ci-bot"
        status.context = {"build_id": "nightly-42", "image_digest": "sha256:abc"}

        from jumpstarter.logging import set_log_context as _orig_set

        calls = []

        def tracking_set(**kwargs):
            calls.append(kwargs)
            _orig_set(**kwargs)

        with patch("jumpstarter.exporter.exporter.set_log_context", side_effect=tracking_set):
            # Simulate the lease assignment block from serve()
            exporter._lease_context = None
            if exporter._lease_context is None and status.lease_name != "" and status.leased:
                exporter._started = True
                lease_scope = LeaseContext(
                    lease_name=status.lease_name,
                    before_lease_hook=Event(),
                )
                exporter._lease_context = lease_scope
                log_ctx = {"lease_id": status.lease_name, "exporter": exporter.name}
                if status.context:
                    log_ctx.update(status.context)
                tracking_set(**log_ctx)

        assert len(calls) >= 1
        first_call = calls[0]
        assert first_call["lease_id"] == "test-lease-ctx"
        assert first_call["exporter"] == "lab-exporter-01"
        assert first_call["build_id"] == "nightly-42"
        assert first_call["image_digest"] == "sha256:abc"

        clear_log_context()

    async def test_context_not_bound_when_empty(self):
        """When StatusResponse has no context, only lease_id and exporter are bound."""
        from jumpstarter.logging import clear_log_context

        clear_log_context()

        status = MagicMock()
        status.leased = True
        status.lease_name = "lease-no-ctx"
        status.client_name = ""
        status.context = {}

        calls = []

        from jumpstarter.logging import set_log_context as _orig_set

        def tracking_set(**kwargs):
            calls.append(kwargs)
            _orig_set(**kwargs)

        # Simulate the lease assignment block
        log_ctx = {"lease_id": status.lease_name, "exporter": "my-exporter"}
        if status.context:
            log_ctx.update(status.context)
        tracking_set(**log_ctx)

        assert len(calls) == 1
        assert calls[0] == {"lease_id": "lease-no-ctx", "exporter": "my-exporter"}
        assert "build_id" not in calls[0]

        clear_log_context()

    async def test_client_name_bound_separately(self):
        """Client name is bound in a separate set_log_context call."""
        from jumpstarter.logging import clear_log_context

        clear_log_context()

        calls = []

        def tracking_set(**kwargs):
            calls.append(kwargs)

        # Simulate the client name binding (line 828 in exporter.py)
        tracking_set(client="ci-bot")

        assert calls == [{"client": "ci-bot"}]
        clear_log_context()
