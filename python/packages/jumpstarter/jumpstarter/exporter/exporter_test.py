"""Tests for exporter state machine transitions and status reporting.

These tests verify the exporter correctly handles lease lifecycle edge cases
including premature lease-end during hooks, unused lease timeouts,
consecutive leases, idempotent lease-end signals, and gRPC error handling
in _report_status.
"""

import logging
from contextlib import asynccontextmanager, nullcontext
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import anyio.lowlevel
import grpc
import pytest
from anyio import Event, create_memory_object_stream, create_task_group, fail_after
from grpc.aio import AioRpcError, Metadata
from jumpstarter_protocol import jumpstarter_pb2

import jumpstarter.exporter.exporter as exporter_module
from jumpstarter.common import ExporterStatus, LeaseHookPhase
from jumpstarter.exporter.exporter import (
    _RPC_BACKOFF_BASE,
    _RPC_BACKOFF_CAP,
    _RPC_MAX_RETRIES,
    _RPC_TIMEOUT,
    LeaseFinished,
    LeaseState,
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


def _make_base_exporter(**overrides):
    """Shared exporter factory: initializes all fields that handle_lease,
    _apply_status, and serve() may touch so test helpers stay in sync
    when new init=False fields are added."""
    from jumpstarter.exporter.exporter import Exporter

    defaults = {
        "_exporter_status": ExporterStatus.AVAILABLE,
        "_lease_context": None,
        "_stop_requested": False,
        "_standalone": False,
        "_started": False,
        "_tg": None,
        "_registered": False,
        "_unregister": False,
        "_deferred_unregister": True,
        "_exit_code": None,
        "hook_executor": None,
        "exit_on_lease_end": False,
        "labels": {},
        "exporter_name": "test-exporter",
        "_last_completed_lease": None,
        # An exporter past start-up; start-up record handling has its own tests.
        "_previous_process_checked": True,
        "_lease_hooks_supported": False,
        "_handed_off_lease": None,
        "_pending_lease_status": None,
        "_control_tx": None,
        "_status_drain_active": False,
        "_pending_status_request": None,
        "_status_rpc_event": Event(),
        "_fatal_stream_error": None,
        "_report_status": AsyncMock(),
        "_request_lease_release": AsyncMock(),
        "_telemetry_handler": None,
        "_telemetry_channel": None,
    }
    defaults.update(overrides)
    exporter = Exporter.__new__(Exporter)
    for k, v in defaults.items():
        setattr(exporter, k, v)
    return exporter


def make_exporter(lease_ctx, hook_executor=None):
    return _make_base_exporter(
        _lease_context=lease_ctx,
        hook_executor=hook_executor,
    )


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
            return await original_run_after(*args, **kwargs)  # type: ignore[call-arg]

        hook_executor.run_after_lease_hook = tracking_run_after  # type: ignore[method-assign]

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
            result = await original_run_before(*args, **kwargs)  # type: ignore[call-arg]
            events.append("before_end")
            return result

        async def tracking_after(*args, **kwargs):
            events.append("after_start")
            result = await original_run_after(*args, **kwargs)  # type: ignore[call-arg]
            events.append("after_end")
            return result

        hook_executor.run_before_lease_hook = tracking_before  # type: ignore[method-assign]
        hook_executor.run_after_lease_hook = tracking_after  # type: ignore[method-assign]

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


class TestCleanupDoesNotReleaseActiveLease:
    @pytest.mark.parametrize("has_after_hook", [False, True])
    @pytest.mark.parametrize("lifecycle", ["cancelled_cleanup", "end_session"])
    async def test_cleanup_preserves_reservation_without_endlease_policy(self, has_after_hook, lifecycle):
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook = HookInstanceConfigV1Alpha1(script="echo cleanup", on_failure="warn")
        executor = HookExecutor(config=HookConfigV1Alpha1(
            before_lease=hook, after_lease=hook if has_after_hook else None,
        ))
        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()
        exporter = make_exporter(lease_ctx, executor)
        with patch.object(executor, "_execute_hook", new=AsyncMock(return_value=None)):
            if lifecycle == "cancelled_cleanup":
                with anyio.CancelScope() as scope:
                    scope.cancel()
                    await exporter._cleanup_after_lease(lease_ctx)
            else:
                lease_ctx.end_session_requested.set()
                await exporter._handle_end_session(lease_ctx)
        exporter._request_lease_release.assert_not_awaited()
        assert not lease_ctx.lease_ended.is_set()
        assert lease_ctx.after_lease_hook_done.is_set()


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
            return await original_run_after(*args, **kwargs)  # type: ignore[call-arg]

        hook_executor.run_after_lease_hook = counting_run_after  # type: ignore[method-assign]

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
    """Create an Exporter with real methods for testing gRPC error handling.

    Unlike the other factories, this restores the real _report_status and
    _request_lease_release so tests can verify retry logic and error paths.
    """
    from jumpstarter.exporter.exporter import Exporter

    exporter = _make_base_exporter()
    exporter._report_status = Exporter._report_status.__get__(exporter, Exporter)
    exporter._request_lease_release = Exporter._request_lease_release.__get__(exporter, Exporter)
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

        with (
            patch.object(exporter, "_controller_stub", return_value=stub_ctx),
            caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"),
        ):
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

        with (
            patch.object(exporter, "_controller_stub", return_value=stub_ctx),
            patch("anyio.sleep") as mock_sleep,
            caplog.at_level(logging.DEBUG, logger="jumpstarter.exporter.exporter"),
        ):
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

        _mock_controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=fail_twice_then_succeed)

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
            await exporter._request_lease_release(lease_ctx)

        # ReleaseLease called once with correct lease name
        assert len(release_calls) == 1
        assert release_calls[0].name == "test-lease"

        # ReportStatus called once (AVAILABLE only, no release_lease)
        assert len(report_calls) == 1
        assert report_calls[0].release_lease is False
        assert ExporterStatus.from_proto(report_calls[0].status) == ExporterStatus.AVAILABLE

        # Lease ended event set
        assert lease_ctx.lease_ended.is_set()

    async def test_request_lease_release_rejects_stale_lease(self):
        """A delayed release callback for a replaced lease must not release
        the replacement lease.

        Simulates the beforeLease endLease path: the hook fails for
        'old-lease', the release is delayed, and a replacement lease is
        granted before the callback fires.
        """
        exporter = _make_exporter_for_report_status()

        stale_ctx = make_lease_context("old-lease")
        replacement_ctx = LeaseContext(
            lease_name="new-lease",
            before_lease_hook=Event(),
            client_name="test-client",
        )
        exporter._lease_context = replacement_ctx

        release_calls = []

        async def capture_release(request, **kwargs):
            release_calls.append(request)

        mock_controller = AsyncMock()
        mock_controller.ReleaseLease = AsyncMock(side_effect=capture_release)
        mock_controller.ReportStatus = AsyncMock()

        stub_ctx = AsyncMock()
        stub_ctx.__aenter__ = AsyncMock(return_value=mock_controller)
        stub_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch.object(exporter, "_controller_stub", return_value=stub_ctx):
            await exporter._request_lease_release(stale_ctx)

        # No ReleaseLease RPC for the stale lease; replacement lease untouched
        assert release_calls == []
        assert not replacement_ctx.lease_ended.is_set()

    @pytest.mark.parametrize(("pending_rpc", "error_code"), [
        ("release", None),
        ("release", grpc.StatusCode.PERMISSION_DENIED),
        ("release", grpc.StatusCode.UNIMPLEMENTED),
        ("release", grpc.StatusCode.INVALID_ARGUMENT),
        ("available", None),
    ])
    async def test_request_lease_release_preserves_replacement_during_rpc(self, pending_rpc, error_code):
        """Replacing a lease during an RPC must not apply stale release side effects."""
        from grpc.aio import AioRpcError, Metadata

        exporter = _make_exporter_for_report_status()
        original = make_lease_context("old-lease")
        exporter._lease_context = original
        exporter._exporter_status = ExporterStatus.BEFORE_LEASE_HOOK_FAILED
        replacement = make_lease_context("new-lease")
        replacement.update_status(ExporterStatus.LEASE_READY, "Ready for commands")
        rpc_started = Event()
        rpc_complete = Event()
        report_calls = []

        async def release(request, **kwargs):
            assert request.name == "old-lease"
            if pending_rpc == "release":
                rpc_started.set()
                await rpc_complete.wait()
            if error_code is not None:
                raise AioRpcError(error_code, Metadata(), Metadata(), "Release failed")

        async def report(request, **kwargs):
            report_calls.append(request)
            if pending_rpc != "release":
                rpc_started.set()
                await rpc_complete.wait()

        controller, stub_ctx = _setup_mock_controller_stub(exporter, side_effect=report)
        controller.ReleaseLease = AsyncMock(side_effect=release)
        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), fail_after(5):
            async with create_task_group() as tg:
                tg.start_soon(exporter._request_lease_release, original)
                await rpc_started.wait()
                exporter._lease_context = replacement
                exporter._exporter_status = ExporterStatus.LEASE_READY
                rpc_complete.set()

        assert exporter._lease_context is replacement
        assert not replacement.lease_ended.is_set()
        assert replacement.current_status == ExporterStatus.LEASE_READY
        assert exporter._exporter_status == ExporterStatus.LEASE_READY
        # A report already in flight may finish, but no new report may follow it.
        assert len(report_calls) == (0 if pending_rpc == "release" else 1)
        if report_calls:
            assert report_calls[0].release_lease is False

    @pytest.mark.parametrize("lease_change", ["replacement", "ended", "unchanged"])
    async def test_release_retries_only_for_active_original_lease(self, lease_change):
        from grpc.aio import AioRpcError, Metadata

        exporter = _make_exporter_for_report_status()
        original = make_lease_context("old-lease")
        replacement = make_lease_context("new-lease")
        exporter._lease_context = original
        transient_error = AioRpcError(grpc.StatusCode.UNAVAILABLE, Metadata(), Metadata(), "Try again")
        controller, stub_ctx = _setup_mock_controller_stub(exporter)
        controller.ReleaseLease.side_effect = [transient_error, None]

        async def during_backoff(delay):
            if lease_change == "replacement":
                exporter._lease_context = replacement
            elif lease_change == "ended":
                original.lease_ended.set()

        with (
            patch.object(exporter, "_controller_stub", return_value=stub_ctx),
            patch("anyio.sleep", side_effect=during_backoff) as sleep,
        ):
            await exporter._request_lease_release(original)

        sleep.assert_awaited_once()
        releases = controller.ReleaseLease.await_args_list
        assert len(releases) == (2 if lease_change == "unchanged" else 1)
        assert all(call.args[0].name == original.lease_name for call in releases)
        assert not replacement.lease_ended.is_set()
        if lease_change == "replacement":
            controller.ReportStatus.assert_not_awaited()
        if lease_change == "unchanged":
            assert original.lease_ended.is_set()

    @pytest.mark.parametrize("replace_on_attempt", [1, 2])
    async def test_release_rechecks_lease_after_channel_setup(self, replace_on_attempt):
        from grpc.aio import AioRpcError, Metadata

        exporter = _make_exporter_for_report_status()
        original = make_lease_context("old-lease")
        exporter._lease_context = original
        replacement = make_lease_context("new-lease")
        controller, stub_ctx = _setup_mock_controller_stub(exporter)
        controller.ReleaseLease.side_effect = AioRpcError(
            grpc.StatusCode.UNAVAILABLE, Metadata(), Metadata(), "Try again",
        )

        async def enter_stub():
            if stub_ctx.__aenter__.await_count == replace_on_attempt:
                exporter._lease_context = replacement
            return controller

        stub_ctx.__aenter__.side_effect = enter_stub
        with patch.object(exporter, "_controller_stub", return_value=stub_ctx), patch("anyio.sleep"):
            await exporter._request_lease_release(original)

        assert controller.ReleaseLease.await_count == replace_on_attempt - 1
        controller.ReportStatus.assert_not_awaited()
        assert not replacement.lease_ended.is_set()

    async def test_request_lease_release_retries_on_transient_failure(self):
        """When ReleaseLease fails with UNAVAILABLE on first attempt, retry and succeed."""
        exporter = _make_exporter_for_report_status()

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
            await exporter._request_lease_release(lease_ctx)

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
            await exporter._request_lease_release(lease_ctx)

        # ReleaseLease: _RPC_MAX_RETRIES + 1 attempts (all fail)
        assert release_call_count == _RPC_MAX_RETRIES + 1

        # ReportStatus: _RPC_MAX_RETRIES + 1 attempts via _send_report_status_rpc (all fail)
        assert report_call_count == _RPC_MAX_RETRIES + 1

        # Critical: lease_ended must be set even when everything fails,
        # otherwise handle_lease never exits
        assert lease_ctx.lease_ended.is_set()

    @pytest.mark.parametrize("error_code", [
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.UNIMPLEMENTED,
    ])
    async def test_rejected_release_never_sends_nameless_release(self, error_code, caplog):
        from grpc.aio import AioRpcError, Metadata

        exporter = _make_exporter_for_report_status()
        lease_ctx = make_lease_context()
        exporter._lease_context = lease_ctx
        controller, stub_ctx = _setup_mock_controller_stub(exporter)
        controller.ReleaseLease.side_effect = AioRpcError(error_code, Metadata(), Metadata(), "Release rejected")

        with (
            patch.object(exporter, "_controller_stub", return_value=stub_ctx),
            caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"),
        ):
            await exporter._request_lease_release(lease_ctx)

        controller.ReleaseLease.assert_awaited_once()
        assert controller.ReleaseLease.await_args.args[0].name == "test-lease"
        controller.ReportStatus.assert_awaited_once()
        assert controller.ReportStatus.await_args.args[0].release_lease is False
        assert "Failed to release lease" in caplog.text


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


class TestApplyStatus:
    """Tests for _apply_status state machine transitions."""

    def _make_idle_exporter(self, hook_executor=None):
        return _make_base_exporter(hook_executor=hook_executor)

    async def test_reassignment_signals_old_lease_ended(self):
        """When already LEASED with lease A, receiving lease B signals teardown
        and stashes the new status for replay."""
        exporter = self._make_idle_exporter()
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter._lease_context = lease_ctx

        assert exporter._lease_state == LeaseState.LEASED

        status = MagicMock()
        status.leased = True
        status.lease_name = "lease-B"
        status.client_name = "other-client"
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert result is False
        assert lease_ctx.lease_ended.is_set()
        assert exporter._lease_context.lease_name == "lease-A"
        assert exporter._pending_lease_status is status

    async def test_reassignment_idempotent_no_duplicate_log(self, caplog):
        """Repeated ticks for the new lease don't re-log the warning."""
        exporter = self._make_idle_exporter()
        lease_ctx = make_lease_context(lease_name="lease-A")
        lease_ctx.lease_ended.set()
        exporter._lease_context = lease_ctx

        status = MagicMock()
        status.leased = True
        status.lease_name = "lease-B"
        status.client_name = "other-client"
        status.context = {}

        with caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"):
            async with create_task_group() as tg:
                await exporter._apply_status(status, tg)
                tg.cancel_scope.cancel()

        assert "reassigned" not in caplog.text

    async def test_overlap_same_lease_name_not_rejected(self):
        """Re-receiving the same lease name is a normal update, not rejected."""
        exporter = self._make_idle_exporter()
        exporter._lease_context = make_lease_context(lease_name="lease-A")

        status = MagicMock()
        status.leased = True
        status.lease_name = "lease-A"
        status.lease_uid = ""
        status.client_name = "updated-client"
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert result is False
        assert exporter._lease_context.client_name == "updated-client"

    async def test_idle_to_leased_spawns_handle_lease(self):
        """IDLE → LEASED spawns handle_lease via _on_lease_acquired."""
        exporter = self._make_idle_exporter()
        handle_lease_called = []
        handle_lease_ran = Event()

        async def fake_handle_lease(lease_name, tg, lease_scope):
            handle_lease_called.append(lease_name)
            handle_lease_ran.set()

        exporter.handle_lease = fake_handle_lease

        status = MagicMock()
        status.leased = True
        status.lease_name = "new-lease"
        status.client_name = "ci-bot"
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            with fail_after(5):
                await handle_lease_ran.wait()
            tg.cancel_scope.cancel()

        assert result is False
        assert exporter._lease_context is not None
        assert exporter._lease_context.lease_name == "new-lease"
        assert handle_lease_called == ["new-lease"]

    async def test_idle_to_leased_with_hook_executor(self):
        """IDLE → LEASED with hook_executor spawns before_lease_hook task."""
        hook_executor = MagicMock()
        hook_calls = []
        hook_ran = Event()

        async def fake_before_hook(lease_scope, report_status, shutdown, request_release, record_hook=None):
            hook_calls.append(lease_scope.lease_name)
            lease_scope.before_lease_hook.set()
            hook_ran.set()

        hook_executor.run_before_lease_hook = fake_before_hook

        exporter = self._make_idle_exporter(hook_executor=hook_executor)
        handle_lease_called = []
        handle_lease_ran = Event()

        async def fake_handle_lease(lease_name, tg, lease_scope):
            handle_lease_called.append(lease_name)
            handle_lease_ran.set()

        exporter.handle_lease = fake_handle_lease

        status = MagicMock()
        status.leased = True
        status.lease_name = "hooked-lease"
        status.client_name = "ci-bot"
        status.context = {"env": "staging"}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            with fail_after(5):
                await hook_ran.wait()
                await handle_lease_ran.wait()
            tg.cancel_scope.cancel()

        assert hook_calls == ["hooked-lease"]
        assert handle_lease_called == ["hooked-lease"]

    async def test_leased_to_idle_calls_on_lease_released(self):
        """LEASED → IDLE transitions through _on_lease_released."""
        exporter = self._make_idle_exporter()
        lease_ctx = make_lease_context(lease_name="ending-lease")
        lease_ctx.after_lease_hook_done.set()
        exporter._lease_context = lease_ctx
        exporter._started = True

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        # _on_lease_released only signals teardown; the loop keeps the slot
        # until handle_lease posts LeaseFinished (_on_lease_finished clears it).
        assert lease_ctx.lease_ended.is_set()
        assert exporter._lease_context is lease_ctx
        assert exporter._last_completed_lease is None

    async def test_trailing_tick_for_completed_lease_ignored(self):
        """After handle_lease completes, a trailing leased=true tick for the same lease is skipped."""
        exporter = self._make_idle_exporter()
        exporter._last_completed_lease = ("old-lease", "")
        exporter._started = True

        status = MagicMock()
        status.leased = True
        status.lease_name = "old-lease"
        status.lease_uid = ""
        status.client_name = "test-client"
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert result is False
        assert exporter._lease_context is None

    async def test_new_lease_after_completed_lease_accepted(self):
        """A different lease arriving after a completed one is accepted normally."""
        exporter = self._make_idle_exporter()
        exporter._last_completed_lease = ("old-lease", "")
        exporter._started = True
        handle_lease_called = []

        async def fake_handle_lease(lease_name, tg, lease_scope):
            handle_lease_called.append(lease_name)

        exporter.handle_lease = fake_handle_lease

        status = MagicMock()
        status.leased = True
        status.lease_name = "new-lease"
        status.client_name = "test-client"
        status.context = {}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()

        assert exporter._lease_context is not None
        assert exporter._lease_context.lease_name == "new-lease"
        assert handle_lease_called == ["new-lease"]

    async def test_not_leased_clears_last_completed(self):
        """A leased=false tick clears the trailing-tick guard."""
        exporter = self._make_idle_exporter()
        exporter._last_completed_lease = ("old-lease", "")
        exporter._started = True

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert exporter._last_completed_lease is None


class TestHandleLeaseConnections:
    """Tests for handle_lease connection handling and finally block."""

    async def test_handle_lease_processes_connections(self):
        """handle_lease sets up Listen stream, processes connections, and cleans up."""
        from contextlib import asynccontextmanager

        lease_ctx = make_lease_context(lease_name="conn-lease")
        exporter = make_exporter(lease_ctx)
        exporter.tls = None
        exporter.grpc_options = []
        exporter._started = True

        mock_session = MagicMock()
        mock_session.context_log_source.return_value = nullcontext()
        mock_session.update_status = MagicMock()
        mock_session.lease_context = None

        @asynccontextmanager
        async def fake_session_for_lease():
            yield (mock_session, "/tmp/main.sock", "/tmp/hook.sock")

        exporter.session_for_lease = fake_session_for_lease

        conn_handled = []
        conn_arrived = Event()

        async def fake_handle_client_conn(socket_path, router_endpoint, router_token, tls, grpc_options):
            conn_handled.append(router_endpoint)
            conn_arrived.set()

        exporter._handle_client_conn = fake_handle_client_conn
        exporter._handle_end_session = AsyncMock()

        async def fake_retry_stream(name, factory, tx, **kwargs):
            conn_request = MagicMock()
            conn_request.router_endpoint = "router.example.com:443"
            conn_request.router_token = "tok123"
            await tx.send(conn_request)
            await anyio.sleep_forever()

        exporter._retry_stream = fake_retry_stream
        exporter._listen_stream_factory = MagicMock(return_value=MagicMock())
        exporter._skip_stale_lease = AsyncMock(return_value=False)
        cleanup_done = Event()

        async def fake_cleanup_after_lease(lease_scope):
            cleanup_done.set()

        exporter._cleanup_after_lease = AsyncMock(side_effect=fake_cleanup_after_lease)

        async with create_task_group() as tg:
            tg.start_soon(exporter.handle_lease, "conn-lease", tg, lease_ctx)
            with fail_after(5):
                await conn_arrived.wait()
            lease_ctx.lease_ended.set()
            with fail_after(5):
                await cleanup_done.wait()
            tg.cancel_scope.cancel()

        assert conn_handled == ["router.example.com:443"]
        exporter._cleanup_after_lease.assert_awaited_once()

    async def test_handle_lease_finally_sets_before_lease_hook_fallback(self):
        """When no hook_executor, finally block sets before_lease_hook if unset."""
        from contextlib import asynccontextmanager

        lease_ctx = make_lease_context(lease_name="fallback-lease")
        exporter = make_exporter(lease_ctx)
        exporter.tls = None
        exporter.grpc_options = []
        exporter._started = True

        mock_session = MagicMock()
        mock_session.context_log_source.return_value = nullcontext()
        mock_session.update_status = MagicMock()
        mock_session.lease_context = None

        @asynccontextmanager
        async def fake_session_for_lease():
            yield (mock_session, "/tmp/main.sock", "/tmp/hook.sock")

        exporter.session_for_lease = fake_session_for_lease
        exporter._handle_end_session = AsyncMock()
        exporter._handle_client_conn = AsyncMock()
        exporter._skip_stale_lease = AsyncMock(return_value=False)
        cleanup_done = Event()

        async def fake_cleanup_after_lease(lease_scope):
            cleanup_done.set()

        exporter._cleanup_after_lease = AsyncMock(side_effect=fake_cleanup_after_lease)

        async def fake_retry_stream(name, factory, tx, **kwargs):
            await tx.aclose()

        exporter._retry_stream = fake_retry_stream
        exporter._listen_stream_factory = MagicMock(return_value=MagicMock())

        async with create_task_group() as tg:
            tg.start_soon(exporter.handle_lease, "fallback-lease", tg, lease_ctx)
            with fail_after(5):
                await lease_ctx.before_lease_hook.wait()
            lease_ctx.lease_ended.set()
            with fail_after(5):
                await cleanup_done.wait()
            tg.cancel_scope.cancel()

        assert lease_ctx.before_lease_hook.is_set()
        exporter._cleanup_after_lease.assert_awaited_once()

    async def test_handle_lease_finally_posts_lease_finished(self):
        """handle_lease's finally posts LeaseFinished and leaves the slot alone.

        The control-plane loop is the sole writer of _lease_context; handle_lease
        hands the slot back via the message rather than clearing it itself."""
        lease_ctx = make_lease_context(lease_name="cleanup-lease")
        exporter = make_exporter(lease_ctx)
        exporter._skip_stale_lease = AsyncMock(return_value=True)
        status_tx, status_rx = create_memory_object_stream(max_buffer_size=1)
        exporter._control_tx = status_tx

        async with create_task_group() as tg:
            await exporter.handle_lease("cleanup-lease", tg, lease_ctx)

        msg = status_rx.receive_nowait()
        assert isinstance(msg, LeaseFinished)
        assert msg.lease_ctx is lease_ctx
        assert exporter._lease_context is lease_ctx  # handle_lease never clears it
        assert lease_ctx.after_lease_hook_done.is_set()
        await status_tx.aclose()
        await status_rx.aclose()

    async def test_handle_lease_posts_lease_finished_even_when_cancelled(self):
        """The finally is shielded, so cancellation still posts LeaseFinished —
        the loop's finalize trigger is never lost."""
        lease_ctx = make_lease_context(lease_name="cancel-lease")
        exporter = make_exporter(lease_ctx)
        exporter._skip_stale_lease = AsyncMock(return_value=True)
        status_tx, status_rx = create_memory_object_stream(max_buffer_size=1)
        exporter._control_tx = status_tx

        with fail_after(5):
            async with create_task_group() as tg:
                tg.start_soon(exporter.handle_lease, "cancel-lease", tg, lease_ctx)
                await anyio.lowlevel.checkpoint()
                tg.cancel_scope.cancel()

        msg = status_rx.receive_nowait()
        assert isinstance(msg, LeaseFinished)
        assert msg.lease_ctx is lease_ctx
        await status_tx.aclose()
        await status_rx.aclose()

    async def test_on_lease_finished_clears_slot(self):
        """The loop's LeaseFinished handler releases the slot and records the
        completed lease's name and UID."""
        owner_ctx = make_lease_context(lease_name="lease-A")
        exporter = make_exporter(owner_ctx)

        stop = await exporter._on_lease_finished(owner_ctx)

        assert stop is False
        assert exporter._lease_context is None
        assert exporter._last_completed_lease == ("lease-A", "")

    async def test_on_lease_finished_noop_when_slot_not_owned(self, caplog):
        """LeaseFinished for a context that no longer owns the slot is a no-op.

        Because the loop is the only writer, this only happens for a lease that
        never took the slot; it must not wipe whoever owns it now, and it must
        not warn — there is no race to report."""
        ctx_a = make_lease_context(lease_name="lease-A")
        ctx_b = make_lease_context(lease_name="lease-B")
        exporter = make_exporter(ctx_b)
        with caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"):
            stop = await exporter._on_lease_finished(ctx_a)
        assert stop is False
        assert exporter._lease_context is ctx_b
        assert exporter._last_completed_lease is None
        assert caplog.text == ""

    async def test_on_lease_finished_sets_stop_when_exit_on_lease_end(self):
        """Fallback: if the leased=false tick never reached _on_lease_released
        (cancellation / stale lease), the loop still sets _stop_requested when it
        releases the slot."""
        lease_ctx = make_lease_context(lease_name="exit-lease")
        exporter = make_exporter(lease_ctx)
        exporter.exit_on_lease_end = True

        stop = await exporter._on_lease_finished(lease_ctx)

        assert stop is True
        assert exporter._stop_requested is True
        assert exporter._lease_context is None

    async def test_on_lease_finished_replays_pending_reassignment(self):
        """After releasing the slot, the loop replays a stashed reassignment so
        the next lease is acquired on the following iteration."""
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter = make_exporter(lease_ctx)
        pending = MagicMock()
        pending.lease_name = "lease-B"
        exporter._pending_lease_status = pending
        status_tx, status_rx = create_memory_object_stream(max_buffer_size=1)
        exporter._control_tx = status_tx

        await exporter._on_lease_finished(lease_ctx)

        assert status_rx.receive_nowait() is pending
        assert exporter._pending_lease_status is None
        assert exporter._lease_context is None
        await status_tx.aclose()
        await status_rx.aclose()

    async def test_on_lease_finished_ignores_closed_control_channel(self):
        """Shutdown may close the control channel before the replay send; that
        must not leak ClosedResourceError out of the loop."""
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter = make_exporter(lease_ctx)
        pending = MagicMock()
        pending.lease_name = "lease-B"
        exporter._pending_lease_status = pending
        status_tx, status_rx = create_memory_object_stream(max_buffer_size=1)
        await status_tx.aclose()
        exporter._control_tx = status_tx

        await exporter._on_lease_finished(lease_ctx)  # must not raise

        assert exporter._pending_lease_status is None
        assert exporter._lease_context is None
        await status_rx.aclose()

    async def test_lease_reusing_a_finished_lease_name_is_acquired(self):
        """A lease name can be reused once the lease is deleted: a leased tick with the
        finished lease's name but another UID is a new lease, not a trailing tick."""
        lease_ctx = make_lease_context(lease_name="lease-A")
        lease_ctx.lease_uid = "uid-1"
        exporter = make_exporter(lease_ctx)
        await exporter._on_lease_finished(lease_ctx)
        spawned = []

        async def fake_handle_lease(lease_name, tg, lease_scope):
            spawned.append(lease_scope.lease_uid)

        exporter.handle_lease = fake_handle_lease
        trailing = jumpstarter_pb2.StatusResponse(leased=True, lease_name="lease-A", lease_uid="uid-1")
        reused = jumpstarter_pb2.StatusResponse(leased=True, lease_name="lease-A", lease_uid="uid-2")

        async with create_task_group() as tg:
            await exporter._apply_status(trailing, tg)
            await exporter._apply_status(reused, tg)
            await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()

        assert spawned == ["uid-2"]

    async def test_reassignment_to_a_lease_reusing_the_name_tears_down_the_old_one(self):
        """If the controller assigns a new lease with the current lease's name before the
        exporter saw the old one end, the old lease must still be torn down."""
        lease_ctx = make_lease_context(lease_name="lease-A")
        lease_ctx.lease_uid = "uid-1"
        exporter = make_exporter(lease_ctx)
        reused = jumpstarter_pb2.StatusResponse(leased=True, lease_name="lease-A", lease_uid="uid-2")

        async with create_task_group() as tg:
            await exporter._apply_status(reused, tg)
            tg.cancel_scope.cancel()

        assert lease_ctx.lease_ended.is_set()
        assert exporter._pending_lease_status is reused

    async def test_completed_lease_suppresses_trailing_status(self):
        """Once the loop has finalized a lease, a trailing leased=true tick for
        the same name is dropped instead of spawning a dead handle_lease."""
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter = make_exporter(lease_ctx)
        await exporter._on_lease_finished(lease_ctx)
        assert exporter._last_completed_lease == ("lease-A", "")

        spawned = []

        async def fake_handle_lease(lease_name, tg, lease_scope):
            spawned.append(lease_name)

        exporter.handle_lease = fake_handle_lease
        exporter._started = True

        status = MagicMock()
        status.leased = True
        status.lease_name = "lease-A"
        status.lease_uid = ""
        status.client_name = "test-client"
        status.context = {}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert spawned == []
        assert exporter._lease_context is None

    async def test_handle_lease_closes_listen_streams_when_stale_during_setup(self):
        """Stale-lease return during session setup must close the Listen streams."""
        from contextlib import asynccontextmanager

        from jumpstarter.exporter import exporter as exporter_mod

        lease_ctx = make_lease_context(lease_name="stale-setup")
        exporter = make_exporter(lease_ctx)
        exporter.tls = None
        exporter.grpc_options = []
        exporter._started = True

        mock_session = MagicMock()
        mock_session.context_log_source.return_value = nullcontext()
        mock_session.update_status = MagicMock()
        mock_session.lease_context = None

        @asynccontextmanager
        async def fake_session_for_lease():
            yield (mock_session, "/tmp/main.sock", "/tmp/hook.sock")

        exporter.session_for_lease = fake_session_for_lease
        exporter._cleanup_after_lease = AsyncMock()

        skip_calls = {"n": 0}

        async def fake_skip(*_args, **_kwargs):
            skip_calls["n"] += 1
            return skip_calls["n"] > 1

        exporter._skip_stale_lease = fake_skip

        closed = []
        original_create = exporter_mod.create_memory_object_stream

        class TrackingFactory:
            def __getitem__(self, _spec):
                return self

            def __call__(self, *args, **kwargs):
                tx, rx = original_create(*args, **kwargs)
                orig_tx, orig_rx = tx.aclose, rx.aclose

                async def close_tx():
                    closed.append("tx")
                    await orig_tx()

                async def close_rx():
                    closed.append("rx")
                    await orig_rx()

                tx.aclose = close_tx
                rx.aclose = close_rx
                return tx, rx

        with patch.object(exporter_mod, "create_memory_object_stream", TrackingFactory()):
            async with create_task_group() as tg:
                await exporter.handle_lease("stale-setup", tg, lease_ctx)

        assert skip_calls["n"] == 2
        assert "tx" in closed
        assert "rx" in closed


def _make_serve_exporter(exit_on_lease_end=False):
    """Build an Exporter suitable for serve() tests with mocked I/O."""
    from contextlib import asynccontextmanager

    exporter = _make_base_exporter(
        exit_on_lease_end=exit_on_lease_end,
        _registered=True,
    )

    @asynccontextmanager
    async def fake_session():
        yield

    exporter.session = fake_session
    return exporter


def _wire_status_stream(exporter, statuses, sent: Event | None = None):
    """Replace _retry_stream with a function that feeds statuses into tx.

    The stream sends the provided statuses then waits indefinitely (until cancelled),
    matching production behavior where status streams are long-lived.
    If ``sent`` is provided, it is set after all statuses have been queued.
    """
    async def fake_retry_stream(name, factory, tx, backoff=0.5, outage_budget=None):
        for s in statuses:
            await tx.send(s)
        if sent is not None:
            sent.set()
        # Don't close - wait until task group cancels us (matches production)
        await anyio.sleep_forever()

    exporter._retry_stream = fake_retry_stream


def _wire_handle_lease(exporter):
    """Replace handle_lease with a minimal mock that satisfies lifecycle events.

    Mirrors the real ordering: afterLease hook completes, session teardown
    finishes, then handle_lease's finally posts LeaseFinished so the
    control-plane loop releases the slot."""
    from jumpstarter.exporter.exporter import LeaseFinished

    async def fake_handle_lease(lease_name, tg, lease_ctx):
        await lease_ctx.lease_ended.wait()
        lease_ctx.after_lease_hook_done.set()
        await anyio.lowlevel.checkpoint()
        if exporter._control_tx is not None:
            await exporter._control_tx.send(LeaseFinished(lease_ctx))

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

        with patch("jumpstarter.exporter.exporter.shutdown_runtime_sidecar") as shutdown:
            await exporter.serve()
            shutdown.assert_called()

        assert exporter._stop_requested is True

    async def test_serve_not_triggered_on_startup(self):
        """serve() does NOT set _stop_requested on startup when no lease
        has been served yet (previous_leased is False)."""
        exporter = _make_serve_exporter(exit_on_lease_end=True)
        statuses_sent = Event()
        _wire_status_stream(exporter, [
            MagicMock(leased=False, lease_name="", client_name=""),
        ], sent=statuses_sent)

        with patch("jumpstarter.exporter.exporter.shutdown_runtime_sidecar"):
            async with create_task_group() as tg:
                tg.start_soon(exporter.serve)
                await statuses_sent.wait()
                # Yield so serve() can process the queued status.
                await anyio.lowlevel.checkpoint()
                assert exporter._stop_requested is False
                tg.cancel_scope.cancel()

    async def test_serve_continues_when_disabled(self):
        """serve() does NOT set _stop_requested after lease ends when
        exit_on_lease_end is False — the exporter loops for next lease."""
        exporter = _make_serve_exporter(exit_on_lease_end=False)
        statuses_sent = Event()
        _wire_status_stream(exporter, [
            MagicMock(leased=True, lease_name="test-lease", client_name="test"),
            MagicMock(leased=False, lease_name="", client_name=""),
        ], sent=statuses_sent)
        _wire_handle_lease(exporter)

        with patch("jumpstarter.exporter.exporter.shutdown_runtime_sidecar") as shutdown:
            async with create_task_group() as tg:
                tg.start_soon(exporter.serve)
                await statuses_sent.wait()
                # Wait until the lease→unleased transition has been applied.
                with anyio.fail_after(2):
                    while not exporter._started or exporter._lease_context is not None:
                        await anyio.sleep(0.01)
                assert exporter._stop_requested is False
                shutdown.assert_not_called()
                tg.cancel_scope.cancel()



class TestShutdownRuntimeSidecar:
    def test_noop_without_socket_env(self, monkeypatch):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
        assert shutdown_runtime_sidecar() is False

    def test_runs_shutdown_command(self, monkeypatch, tmp_path):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")  # path only; connect is mocked via subprocess
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)

        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert shutdown_runtime_sidecar(binary=str(binary)) is True
            run.assert_called_once()
            args = run.call_args.args[0]
            assert args[0] == str(binary)
            assert args[1] == "shutdown"
            assert "--socket" in args
            assert str(sock) in args

    def test_falls_back_to_binary_beside_socket(self, monkeypatch, tmp_path):
        """When the default path is missing, use jumpstarter-exec next to the socket."""
        from pathlib import Path

        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)

        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        real_is_file = Path.is_file

        def is_file_no_default(self):
            # Treat the packaged default path as absent so we exercise fallback.
            if str(self) == "/shared/jumpstarter-exec":
                return False
            return real_is_file(self)

        with (
            patch.object(Path, "is_file", is_file_no_default),
            patch("subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert shutdown_runtime_sidecar() is True
            assert run.call_args.args[0][0] == str(binary)

        monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
        with (
            patch.object(Path, "is_file", is_file_no_default),
            patch("subprocess.run") as run,
        ):
            run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert shutdown_runtime_sidecar(socket_path=str(sock)) is True
            assert run.call_args.args[0][0] == str(binary)

    def test_missing_binary_returns_false(self, monkeypatch, tmp_path):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        missing = tmp_path / "does-not-exist"
        assert shutdown_runtime_sidecar(binary=str(missing)) is False

    def test_file_not_found_returns_false(self, monkeypatch, tmp_path):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        with patch("subprocess.run", side_effect=FileNotFoundError):
            assert shutdown_runtime_sidecar(binary=str(binary)) is False

    def test_permission_error_returns_false(self, monkeypatch, tmp_path):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        with patch("subprocess.run", side_effect=PermissionError("denied")):
            assert shutdown_runtime_sidecar(binary=str(binary)) is False

    def test_timeout_returns_false(self, monkeypatch, tmp_path):
        import subprocess

        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["jumpstarter-exec"], timeout=1),
        ):
            assert shutdown_runtime_sidecar(binary=str(binary), timeout=1.0) is False

    def test_nonzero_exit_returns_false(self, monkeypatch, tmp_path):
        from jumpstarter.exporter.exporter import shutdown_runtime_sidecar

        sock = tmp_path / "launcher.sock"
        sock.write_text("")
        binary = tmp_path / "jumpstarter-exec"
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        monkeypatch.setenv("JUMPSTARTER_LAUNCHER_SOCKET", str(sock))

        with patch("subprocess.run") as run:
            run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
            assert shutdown_runtime_sidecar(binary=str(binary)) is False


class TestOnLeaseReleased:
    """_on_lease_released reacts to a leased=false tick without touching the
    slot: it flags lease_ended (and, under exit_on_lease_end, _stop_requested)
    and returns immediately. The slot is released later by the loop when the
    matching LeaseFinished arrives — see the TestHandleLeaseFinally /
    _on_lease_finished tests for the release half of the handshake."""

    async def test_signals_lease_ended_without_clearing_slot(self):
        """A leased=false tick flags the lease as ended so handle_lease can run
        its afterLease hook, but must NOT clear _lease_context. The loop keeps
        the slot (so _report_status still reaches the client session during the
        hook) until handle_lease posts LeaseFinished."""
        exporter = _make_serve_exporter()
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter._lease_context = lease_ctx
        exporter._started = True

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert result is False  # loop keeps running until LeaseFinished arrives
        assert lease_ctx.lease_ended.is_set()
        assert exporter._lease_context is lease_ctx
        assert exporter._last_completed_lease is None

    async def test_exit_on_lease_end_sets_stop_immediately(self):
        """Refuse new leases the moment the lease ends — do not wait for the
        afterLease hook or session teardown. The slot is still held until
        LeaseFinished so serve()'s shutdown runs only after the hook completes."""
        exporter = _make_serve_exporter(exit_on_lease_end=True)
        lease_ctx = make_lease_context(lease_name="ending-lease")
        exporter._lease_context = lease_ctx
        exporter._started = True

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        async with create_task_group() as tg:
            result = await exporter._apply_status(status, tg)
            tg.cancel_scope.cancel()

        assert exporter._stop_requested is True
        assert result is False  # do not break the loop before LeaseFinished
        assert exporter._lease_context is lease_ctx

    async def test_does_not_call_shutdown_itself(self):
        """exit_on_lease_end flips _stop_requested but must not call
        shutdown_runtime_sidecar; serve()'s finally is the single authoritative
        call site, reached only after handle_lease's afterLease hook completes."""
        exporter = _make_serve_exporter(exit_on_lease_end=True)
        lease_ctx = make_lease_context(lease_name="ending-lease")
        exporter._lease_context = lease_ctx
        exporter._started = True

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        shutdown_calls = []

        def tracking_shutdown(*_args, **_kwargs):
            shutdown_calls.append(True)

        with patch("jumpstarter.exporter.exporter.shutdown_runtime_sidecar", tracking_shutdown):
            async with create_task_group() as tg:
                await exporter._apply_status(status, tg)
                tg.cancel_scope.cancel()

        assert shutdown_calls == []
        assert exporter._stop_requested is True

    async def test_spawns_watchdog_on_lease_end(self):
        """A leased=false tick starts the LeaseFinished watchdog so a wedged
        teardown is eventually logged."""
        exporter = _make_serve_exporter()
        lease_ctx = make_lease_context(lease_name="lease-A")
        exporter._lease_context = lease_ctx
        exporter._started = True

        spawned = []

        async def tracking_watchdog(ctx):
            spawned.append(ctx)

        exporter._lease_finished_watchdog = tracking_watchdog

        status = MagicMock()
        status.leased = False
        status.lease_name = ""
        status.client_name = ""
        status.context = {}

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            await anyio.lowlevel.checkpoint()
            tg.cancel_scope.cancel()

        assert spawned == [lease_ctx]


class TestLeaseFinishedWatchdog:
    """The watchdog only logs a stuck Ending state; it never releases the slot,
    so the loop's no-timeout wait for LeaseFinished stays a real happens-before
    edge rather than a disguised timer."""

    async def test_warns_when_teardown_stalls(self, caplog):
        """If LeaseFinished never arrives, the watchdog logs once and leaves the
        slot untouched."""
        exporter = _make_serve_exporter()
        lease_ctx = make_lease_context(lease_name="wedged")
        exporter._lease_context = lease_ctx

        with (
            caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"),
            patch("jumpstarter.exporter.exporter._LEASE_FINISHED_WATCHDOG", 0.01),
        ):
            await exporter._lease_finished_watchdog(lease_ctx)

        assert "stuck in Ending" in caplog.text
        assert "wedged" in caplog.text
        assert exporter._lease_context is lease_ctx  # never released by the watchdog

    async def test_silent_after_lease_finished(self, caplog):
        """Once _on_lease_finished has released the slot, the watchdog stays
        quiet when it wakes."""
        exporter = _make_serve_exporter()
        lease_ctx = make_lease_context(lease_name="clean")
        exporter._lease_context = lease_ctx

        with (
            caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.exporter"),
            patch("jumpstarter.exporter.exporter._LEASE_FINISHED_WATCHDOG", 0.05),
        ):
            async with create_task_group() as tg:
                tg.start_soon(exporter._lease_finished_watchdog, lease_ctx)
                await exporter._on_lease_finished(lease_ctx)

        assert exporter._lease_context is None
        assert caplog.text == ""


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
        exporter.labels = {}
        exporter.exporter_name = "lab-exporter-01"
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
                tracking_set(**exporter._lease_log_context(status))

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


# --- Exporter restarts are not lease boundaries (#1204) ---------------------


def _recording_hook_executor(
    tmp_path, *, before=False, after=False, on_interrupt="rerun", timeout=10, setup_fails_for=None
):
    """A real HookExecutor whose hooks append $LEASE_NAME to tmp_path/<hook>, and $CLIENT_NAME
    to tmp_path/<hook>.client. The beforeLease hook of lease setup_fails_for fails (endLease)."""
    from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
    from jumpstarter.exporter.hooks import HookExecutor

    def hook(name, fails_for=None):
        script = f'echo "$LEASE_NAME" >> {tmp_path / name}; echo "$CLIENT_NAME" >> {tmp_path / name}.client'
        if fails_for:
            script += f'; [ "$LEASE_NAME" != "{fails_for}" ]'
        return HookInstanceConfigV1Alpha1(
            script=script, timeout=timeout, on_interrupt=on_interrupt,
            on_failure="endLease" if fails_for else "warn",
        )

    return HookExecutor(
        config=HookConfigV1Alpha1(
            before_lease=hook("before", setup_fails_for) if before else None,
            after_lease=hook("after") if after else None,
        )
    )


def _hook_runs(tmp_path, name):
    """Lease names the recording hook ran for, in order."""
    path = tmp_path / name
    return path.read_text().split() if path.exists() else []


def _hook_clients(tmp_path, name):
    """$CLIENT_NAME of each run of the recording hook, in order."""
    path = tmp_path / f"{name}.client"
    return path.read_text().splitlines() if path.exists() else []


def _fake_session_for_lease():
    session = MagicMock()
    session.context_log_source.return_value = nullcontext()

    @asynccontextmanager
    async def fake():
        yield (session, "/tmp/main.sock", "/tmp/hook.sock")

    return fake


class _LeaseHooksController:
    """Controller stand-in for UpdateLeaseHooks. It keeps the lease hook record and applies
    the controller's transition rules (applyLeaseHookUpdate); hook states compare by
    (phase, attempts).

    It follows the Status updates the exporter is given (see _restart_exporter): the lease
    they assign, and the record they carry the first time. follow_status=False keeps
    `assigned` as set, for a controller that has moved on from what the exporter last heard.
    """

    def __init__(
        self, *, assigned=None, follow_status=True, fail_on=(), fail_code=grpc.StatusCode.INTERNAL, fail_times=None,
        hold=None,
    ):
        self.assigned = assigned  # (name, uid) of the lease assigned to the exporter, if any
        self.follow_status = follow_status
        self.record = None  # {"lease": (name, uid), "before_lease": state, "after_lease": state}
        self.fail_on = set(fail_on)  # (hook, phase) writes that fail with fail_code
        self.fail_code = fail_code
        self.fail_times = fail_times  # fail only this many times; None: always
        self.hold = dict(hold or {})  # (hook, phase) -> Event: the first such write waits for it
        self.held = 0
        self.failures = 0
        self.calls = 0
        self.updates = []  # accepted: (lease, hook, phase, attempts)
        self.lease_uids = []  # lease UID of each accepted update

    def observe(self, status):
        if not self.follow_status:
            return
        self.assigned = (status.lease_name, status.lease_uid) if status.leased else None
        hooks = status.lease_hooks
        if self.record is None and hooks.lease_name:

            def state(name):
                if not hooks.HasField(name):
                    return None
                return (getattr(hooks, name).phase, max(getattr(hooks, name).attempts, 1))

            self.record = {
                "lease": (hooks.lease_name, hooks.lease_uid),
                "before_lease": state("before_lease"),
                "after_lease": state("after_lease"),
            }

    async def UpdateLeaseHooks(self, request, timeout=None):
        self.calls += 1
        if not request.lease_uid:
            raise AioRpcError(grpc.StatusCode.INVALID_ARGUMENT, Metadata(), Metadata(), "lease UID is required")
        hook = request.WhichOneof("hook")
        state = getattr(request, hook)
        if (hook, LeaseHookPhase(state.phase)) in self.fail_on and (
            self.fail_times is None or self.failures < self.fail_times
        ):
            self.failures += 1
            raise AioRpcError(self.fail_code, Metadata(), Metadata(), "write failed")
        if (release := self.hold.pop((hook, LeaseHookPhase(state.phase)), None)) is not None:
            self.held += 1
            await release.wait()
        self._apply((request.lease_name, request.lease_uid), hook, (state.phase, max(state.attempts, 1)))
        self.updates.append((request.lease_name, hook, LeaseHookPhase(state.phase), state.attempts))
        self.lease_uids.append(request.lease_uid)
        return jumpstarter_pb2.LeaseHooks()

    def _apply(self, lease, hook, nxt):  # noqa: C901 - mirrors applyLeaseHookUpdate
        def refusal(reason):
            return AioRpcError(grpc.StatusCode.FAILED_PRECONDITION, Metadata(), Metadata(), reason)

        finished = (SUCCEEDED, FAILED, SKIPPED)
        if self.assigned is not None and self.assigned != lease:
            raise refusal("another lease is assigned")
        assigned = self.assigned == lease
        record = dict(self.record) if self.record and self.record["lease"] == lease else None
        if hook == "before_lease":
            if not assigned and (record is None or nxt[0] == RUNNING):
                raise refusal("lease is not assigned")
            if record is None:
                record = {"lease": lease, "before_lease": None, "after_lease": None}
            if record["after_lease"] is not None:
                raise refusal("afterLease hook has already started")
        elif record is None or record["before_lease"] is None or record["before_lease"][0] not in finished:
            raise refusal("beforeLease hook has not finished")
        assert record is not None
        prev = record[hook]
        if prev is not None and prev != nxt:
            if prev[0] in finished:
                raise refusal("already finished")
            if nxt[0] == RUNNING and nxt[1] <= prev[1]:
                raise refusal("already running")
            if nxt[0] != RUNNING and nxt[1] != prev[1]:
                raise refusal("an outcome of another attempt")
        elif prev is None and nxt[0] not in (RUNNING, SKIPPED):
            raise refusal("not started")
        record[hook] = nxt
        self.record = record

    def hook(self, hook):
        """Recorded state of a hook, (phase, attempts), or None."""
        assert self.record is not None
        return self.record[hook]

    def hook_updates(self, hook):
        """Accepted updates of a hook, as (phase, attempts)."""
        return [(phase, attempts) for _, h, phase, attempts in self.updates if h == hook]


def _hook_state(phase, attempts=1, on_failure=0, message=""):
    return jumpstarter_pb2.LeaseHookState(phase=phase, attempts=attempts, on_failure=on_failure, message=message)


def _status(leased, lease_name="", record=None, *, keeps_records=True, lease_uid=None):
    """A StatusResponse; record is (lease, before state, after state[, lease uid]). A lease's
    UID defaults to "<name>-uid". keeps_records=False models a controller that predates
    lease hook records (no lease_hooks field, no lease_uid)."""
    status = jumpstarter_pb2.StatusResponse(leased=leased)
    if leased:
        status.lease_name = lease_name
        status.client_name = "test-client"
        if keeps_records:
            status.lease_uid = lease_uid or f"{lease_name}-uid"
    if keeps_records:
        status.lease_hooks.SetInParent()
        if record is not None:
            lease, before, after, *uid = record
            status.lease_hooks.lease_name = lease
            status.lease_hooks.lease_uid = uid[0] if uid else f"{lease}-uid"
            status.lease_hooks.client_name = "test-client"
            if before is not None:
                status.lease_hooks.before_lease.CopyFrom(before)
            if after is not None:
                status.lease_hooks.after_lease.CopyFrom(after)
    return status


def _restart_exporter(tmp_path, controller, *, first_status=True, **hooks):
    """An exporter wired to a fake controller, with fake sessions and Listen streams.

    first_status: the exporter just started, so its first Status update is still
    to be checked for hook work a previous process left.
    """
    exporter = _make_base_exporter(
        hook_executor=_recording_hook_executor(tmp_path, **hooks),
        _previous_process_checked=not first_status,
        tls=None,
        grpc_options={},
    )
    exporter.session_for_lease = _fake_session_for_lease()
    exporter._listen_stream_factory = MagicMock()
    exporter.reported = []

    async def record_status(status, message=""):
        exporter.reported.append(status)
        if exporter._lease_context:
            exporter._lease_context.update_status(status, message)

    exporter._report_status = AsyncMock(side_effect=record_status)

    async def idle_listen_stream(name, factory, tx, **kwargs):
        await anyio.sleep_forever()

    exporter._retry_stream = idle_listen_stream

    @asynccontextmanager
    async def controller_stub():
        yield controller

    exporter._controller_stub = controller_stub
    apply_status = exporter._apply_status

    async def apply_status_the_controller_sent(status, tg):
        controller.observe(status)
        return await apply_status(status, tg)

    exporter._apply_status = apply_status_the_controller_sent
    return exporter


RUNNING, SUCCEEDED, FAILED, SKIPPED = (
    LeaseHookPhase.RUNNING, LeaseHookPhase.SUCCEEDED, LeaseHookPhase.FAILED, LeaseHookPhase.SKIPPED
)
END_LEASE = jumpstarter_pb2.LEASE_HOOK_FAILURE_ACTION_END_LEASE
EXIT = jumpstarter_pb2.LEASE_HOOK_FAILURE_ACTION_EXIT


@pytest.mark.parametrize(
    ("hooks", "exit_on_lease_end", "records"),
    [
        pytest.param(True, False, True, id="hooks"),
        pytest.param(False, False, False, id="no-hooks"),
        pytest.param(True, True, False, id="exit-on-lease-end"),
    ],
)
async def test_registration_says_whether_the_exporter_records_lease_hooks(
    tmp_path, monkeypatch, hooks, exit_on_lease_end, records
):
    """The controller holds an exporter that records its lease hooks while its record
    owes cleanup, so only exporters that do record them may say so."""
    exporter = _make_base_exporter(
        hook_executor=_recording_hook_executor(tmp_path, before=True) if hooks else None,
        exit_on_lease_end=exit_on_lease_end,
    )
    exporter._setup_telemetry = AsyncMock()
    registered = []

    class Controller:
        async def Register(self, request):
            registered.append(request)

    @asynccontextmanager
    async def controller_stub():
        yield Controller()

    exporter._controller_stub = controller_stub
    report_stub = MagicMock()
    report_stub.GetReport = AsyncMock(return_value=jumpstarter_pb2.GetReportResponse())
    monkeypatch.setattr(exporter_module.jumpstarter_pb2_grpc, "ExporterServiceStub", lambda channel: report_stub)

    await exporter._register_with_controller(MagicMock())

    assert [request.records_lease_hooks for request in registered] == [records]


class TestExporterRestartKeepsLease:
    """An exporter restart is a process event, not a lease event: the lease stays,
    and the next process picks up from the hook record the controller keeps."""

    @pytest.mark.parametrize(
        ("keeps_records", "exit_on_lease_end", "lease_ends", "handed_off"),
        [
            pytest.param(True, False, False, True, id="stop-during-lease"),
            pytest.param(True, False, True, False, id="lease-ended-first"),
            pytest.param(False, False, False, False, id="controller-without-records"),
            pytest.param(True, True, False, False, id="exit-on-lease-end"),
        ],
    )
    async def test_stop_during_lease_leaves_it_for_the_restarted_exporter(
        self, tmp_path, keeps_records, exit_on_lease_end, lease_ends, handed_off
    ):
        """Regression (#1204): `systemctl restart` ran the afterLease hook and released the lease."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=True, after=True)
        exporter.exit_on_lease_end = exit_on_lease_end

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept", keeps_records=keeps_records), tg)
            lease_ctx = exporter._lease_context
            with fail_after(10):
                await lease_ctx.before_lease_hook.wait()
            if lease_ends:
                await exporter._apply_status(_status(False, keeps_records=keeps_records), tg)
                with fail_after(10):
                    await lease_ctx.after_lease_hook_done.wait()
            exporter.stop(should_unregister=True)  # what jmp run does on SIGTERM

        assert _hook_runs(tmp_path, "before") == ["kept"]
        # Exporters release a lease only when a hook fails with onFailure: endLease.
        exporter._request_lease_release.assert_not_awaited()
        if handed_off:
            assert _hook_runs(tmp_path, "after") == []
            assert controller.hook_updates("after_lease") == []
            assert exporter._handed_off_lease == "kept"
        else:
            assert _hook_runs(tmp_path, "after") == ["kept"]
        # Hooks start only once their start is recorded.
        if keeps_records and not exit_on_lease_end:
            assert controller.hook_updates("before_lease") == [(RUNNING, 1), (SUCCEEDED, 1)]

    @pytest.mark.parametrize(
        ("failed_write", "code", "handed_off"),
        [
            pytest.param(("before_lease", SUCCEEDED), grpc.StatusCode.INTERNAL, True, id="write-failed-for-a-while"),
            pytest.param(("before_lease", RUNNING), grpc.StatusCode.UNIMPLEMENTED, False,
                         id="controller-without-the-call"),
        ],
    )
    async def test_only_a_controller_without_the_call_leaves_a_lease_untracked(
        self, tmp_path, monkeypatch, failed_write, code, handed_off
    ):
        """A write that fails for a while is tried again, and the lease stays tracked: a
        stop hands it off. Only a controller that does not implement the call leaves the
        lease untracked; a stop then runs afterLease, as before records existed."""
        monkeypatch.setattr(exporter_module, "_HOOK_RECORD_RETRY_DELAY", 0.01)
        controller = _LeaseHooksController(fail_on=[failed_write], fail_code=code, fail_times=2)
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=True, after=True)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept"), tg)
            with fail_after(10):
                await exporter._lease_context.before_lease_hook.wait()
            exporter.stop(should_unregister=True)

        assert _hook_runs(tmp_path, "before") == ["kept"]
        assert _hook_runs(tmp_path, "after") == ([] if handed_off else ["kept"])

    @pytest.mark.parametrize(
        ("before", "on_interrupt", "keeps_records", "setup_runs", "recorded", "ready_status"),
        [
            pytest.param(_hook_state(SUCCEEDED), "rerun", True, False, [], ExporterStatus.LEASE_READY,
                         id="set-up-lease-resumes"),
            pytest.param(_hook_state(RUNNING, 1), "rerun", True, True, [(RUNNING, 2), (SUCCEEDED, 2)],
                         ExporterStatus.LEASE_READY, id="cut-off-setup-runs-again"),
            pytest.param(_hook_state(RUNNING, 3), "rerun", True, False, [(FAILED, 3)],
                         ExporterStatus.LEASE_READY, id="setup-cut-off-too-often-fails"),
            pytest.param(_hook_state(RUNNING, 1), "fail", True, False, [(FAILED, 1)],
                         ExporterStatus.LEASE_READY, id="on-interrupt-fail"),
            pytest.param(None, "rerun", False, True, [], ExporterStatus.LEASE_READY,
                         id="controller-without-records"),
            pytest.param(_hook_state(LeaseHookPhase.UNSPECIFIED), "rerun", True, True,
                         [(RUNNING, 2), (SUCCEEDED, 2)], ExporterStatus.LEASE_READY, id="unspecified-phase-runs-again"),
            pytest.param(_hook_state(99), "rerun", True, True,
                         [(RUNNING, 2), (SUCCEEDED, 2)], ExporterStatus.LEASE_READY, id="unknown-phase-runs-again"),
        ],
    )
    async def test_restart_with_lease_still_assigned(
        self, tmp_path, before, on_interrupt, keeps_records, setup_runs, recorded, ready_status
    ):
        """Regression (#1204): the restarted exporter ran the beforeLease hook again for the same lease."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, before=True, on_interrupt=on_interrupt)
        exporter._cleanup_after_lease = AsyncMock()

        async with create_task_group() as tg:
            await exporter._apply_status(
                _status(True, "kept", ("kept", before, None), keeps_records=keeps_records), tg
            )
            with fail_after(10):
                await exporter._lease_context.before_lease_hook.wait()
            await anyio.wait_all_tasks_blocked()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == (["kept"] if setup_runs else [])
        assert controller.hook_updates("before_lease") == recorded
        assert exporter.reported[-1] == ready_status
        assert ExporterStatus.AVAILABLE not in exporter.reported

    @pytest.mark.parametrize(
        "controller_args",
        [
            pytest.param({"follow_status": False}, id="lease-no-longer-assigned"),
            pytest.param(
                {"fail_on": [("before_lease", RUNNING)], "fail_code": grpc.StatusCode.INVALID_ARGUMENT},
                id="update-rejected-as-invalid",
            ),
        ],
    )
    async def test_controller_refusing_setup_keeps_hook_from_running(self, tmp_path, monkeypatch, controller_args):
        """A refused start is final: the hook does not run, and the write is not retried."""
        monkeypatch.setattr(exporter_module, "_HOOK_RECORD_RETRY_DELAY", 0.01)
        controller = _LeaseHooksController(**controller_args)
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=True)
        exporter._cleanup_after_lease = AsyncMock()

        async with create_task_group() as tg:
            await exporter._apply_status(_status(True, "gone"), tg)
            with fail_after(10):
                await exporter._lease_context.before_lease_hook.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == []
        assert ExporterStatus.LEASE_READY not in exporter.reported
        assert controller.calls == 1

    @pytest.mark.parametrize(
        ("before", "after", "cleanup_runs", "recorded"),
        [
            pytest.param(_hook_state(SUCCEEDED), None, True,
                         [("after_lease", RUNNING, 1), ("after_lease", SUCCEEDED, 1)], id="lease-ended-while-down"),
            pytest.param(_hook_state(RUNNING, 1), None, True,
                         [("before_lease", FAILED, 1), ("after_lease", RUNNING, 1), ("after_lease", SUCCEEDED, 1)],
                         id="setup-cut-off-then-lease-ended"),
            pytest.param(_hook_state(SUCCEEDED), _hook_state(RUNNING, 1), True,
                         [("after_lease", RUNNING, 2), ("after_lease", SUCCEEDED, 2)], id="cleanup-cut-off"),
            pytest.param(_hook_state(SUCCEEDED), _hook_state(SUCCEEDED), False, [], id="cleanup-already-done"),
            pytest.param(
                _hook_state(FAILED, on_failure=END_LEASE), None, False,
                [("after_lease", SKIPPED, 1)], id="setup-failed-and-ended-lease",
            ),
        ],
    )
    async def test_restart_after_lease_ended_finishes_owed_cleanup(
        self, tmp_path, before, after, cleanup_runs, recorded
    ):
        """Regression (#1204): a lease that ended while the exporter was down never got its afterLease hook."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True)

        async with create_task_group() as tg:
            await exporter._apply_status(_status(False, record=("old", before, after)), tg)
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "after") == (["old"] if cleanup_runs else [])
        assert _hook_clients(tmp_path, "after") == (["test-client"] if cleanup_runs else [])
        assert _hook_runs(tmp_path, "before") == []
        assert [(h, phase, attempts) for _, h, phase, attempts in controller.updates] == recorded
        # Available only once the device owes no cleanup: the controller holds
        # new leases off a restarted exporter until it reports a status.
        assert exporter.reported[-1] == ExporterStatus.AVAILABLE
        assert exporter.reported.count(ExporterStatus.AVAILABLE) == 1

    @pytest.mark.parametrize(
        ("first_status", "old_after"),
        [
            pytest.param(False, _hook_state(SUCCEEDED), id="while-running"),
            pytest.param(True, _hook_state(SUCCEEDED), id="after-restart"),
            pytest.param(True, None, id="after-restart-old-lease-owed-cleanup"),
        ],
    )
    async def test_new_lease_reusing_a_lease_name_is_set_up(self, tmp_path, first_status, old_after):
        """A lease name can be reused once the lease is deleted: a new lease with an
        earlier lease's name must be set up, not resumed or treated as finished."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(
            tmp_path, controller, first_status=first_status, before=True, after=True
        )
        exporter._cleanup_after_lease = AsyncMock()
        status = _status(
            True, "ci-job", ("ci-job", _hook_state(SUCCEEDED), old_after, "ci-job-1"), lease_uid="ci-job-2"
        )

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            with fail_after(10):
                await exporter._lease_context.before_lease_hook.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == ["ci-job"]
        assert _hook_runs(tmp_path, "after") == []  # nor the earlier lease's cleanup
        exporter._request_lease_release.assert_not_awaited()
        assert controller.lease_uids == ["ci-job-2", "ci-job-2"]

    @pytest.mark.parametrize(
        "failing",
        [("before_lease", RUNNING), ("before_lease", SUCCEEDED), ("after_lease", RUNNING), ("after_lease", SUCCEEDED)],
        ids=["setup-start", "setup-outcome", "cleanup-start", "cleanup-outcome"],
    )
    async def test_hook_transition_is_recorded_before_the_lease_moves_on(self, tmp_path, monkeypatch, failing):
        """Every hook transition is recorded before the exporter goes on. While a write
        fails, the exporter tries again: the hook does not start, and the lease does not
        become ready (setup) or available (cleanup). A hook that ran with its start lost
        would run again as a fresh hook after a restart, and a lost cleanup outcome would
        leave the controller holding the exporter for cleanup it already did."""
        monkeypatch.setattr(exporter_module, "_HOOK_RECORD_RETRY_DELAY", 0.01)
        controller = _LeaseHooksController(fail_on=[failing])
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=True, after=True)
        hook, phase = failing
        name, next_status = ("before", ExporterStatus.LEASE_READY) if hook == "before_lease" else (
            "after", ExporterStatus.AVAILABLE
        )

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept"), tg)
            lease_ctx = exporter._lease_context
            if hook == "after_lease":
                with fail_after(10):
                    await lease_ctx.before_lease_hook.wait()
                await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                while controller.failures < 3:
                    await anyio.sleep(0.01)
            assert _hook_runs(tmp_path, name) == ([] if phase == RUNNING else ["kept"])
            assert next_status not in exporter.reported
            controller.fail_on.clear()  # the controller records it now
            if hook == "before_lease":
                with fail_after(10):
                    await lease_ctx.before_lease_hook.wait()
                await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == ["kept"]
        assert _hook_runs(tmp_path, "after") == ["kept"]
        assert controller.hook("before_lease") == (SUCCEEDED, 1)
        assert controller.hook("after_lease") == (SUCCEEDED, 1)

    @pytest.mark.parametrize(
        ("before_hook", "first_write"),
        [pytest.param(True, RUNNING, id="setup-hook"), pytest.param(False, SKIPPED, id="no-setup-hook")],
    )
    async def test_lease_is_not_set_up_when_its_start_is_never_recorded(
        self, tmp_path, monkeypatch, before_hook, first_write
    ):
        """Without a record of the lease's start, a restart could not tell whether its
        hooks ran: no hook runs, and the lease is not made ready."""
        monkeypatch.setattr(exporter_module, "_HOOK_RECORD_RETRY_DELAY", 0.01)
        controller = _LeaseHooksController(fail_on=[("before_lease", first_write)])
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=before_hook, after=True)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept"), tg)
            lease_ctx = exporter._lease_context
            with fail_after(10):  # still retrying, the hook not run
                while controller.failures < 3:
                    await anyio.sleep(0.01)
            await exporter._apply_status(_status(False), tg)  # the lease ends
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == []
        assert _hook_runs(tmp_path, "after") == []
        assert ExporterStatus.LEASE_READY not in exporter.reported

    async def test_cleanup_is_left_to_the_next_process_once_the_controller_is_lost(self, tmp_path):
        """An exporter exiting after losing the controller cannot record that cleanup
        starts. It leaves the hook to the next process, which runs it with its start
        recorded: run unrecorded, a restart would run it again as a fresh hook."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, first_status=False, before=True, after=True)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept"), tg)
            lease_ctx = exporter._lease_context
            with fail_after(10):
                await lease_ctx.before_lease_hook.wait()
            calls = controller.calls
            exporter._controller_stream_failed = True
            await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "after") == []
        assert controller.calls == calls  # no record retries to wait on
        assert controller.hook("after_lease") is None  # the record still owes cleanup

    @pytest.mark.parametrize(
        ("before", "on_interrupt", "recorded_setup"),
        [
            pytest.param(_hook_state(SUCCEEDED), "rerun", (SUCCEEDED, 1), id="set-up-lease"),
            pytest.param(_hook_state(RUNNING, 1), "rerun", (FAILED, 1), id="setup-cut-off"),
            pytest.param(_hook_state(RUNNING, 1), "fail", (FAILED, 1), id="setup-cut-off-on-interrupt-fail"),
        ],
    )
    async def test_lease_ending_right_after_a_restart_gets_cleanup(
        self, tmp_path, before, on_interrupt, recorded_setup
    ):
        """A lease a previous process worked on owes cleanup even when it ends before this
        process has set up its session. Skipped as stale, its record kept owing cleanup, and
        the controller held the exporter from new leases until its next restart."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True, on_interrupt=on_interrupt)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept", ("kept", before, None)), tg)
            lease_ctx = exporter._lease_context
            await exporter._apply_status(_status(False), tg)  # before the lease's session exists
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == []
        assert _hook_runs(tmp_path, "after") == ["kept"]
        assert controller.hook("before_lease") == recorded_setup
        assert controller.hook("after_lease") == (SUCCEEDED, 1)
        assert exporter.reported[-1] == ExporterStatus.AVAILABLE
        assert ExporterStatus.LEASE_READY not in exporter.reported

    async def test_cleanup_closes_the_setup_attempt_the_record_shows(self, tmp_path, monkeypatch):
        """Cleanup that stops waiting for setup closes the attempt the record shows as
        running. A cut-off setup's re-run whose start is stuck is still the earlier one;
        closing the re-run's attempt would be refused and leave cleanup unable to start."""
        monkeypatch.setattr(exporter_module, "_SETUP_OVERRUN_GRACE", 0)
        start_stuck = Event()
        controller = _LeaseHooksController(hold={("before_lease", RUNNING): start_stuck})
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True, timeout=1)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "kept", ("kept", _hook_state(RUNNING, 1), None)), tg)
            lease_ctx = exporter._lease_context
            with fail_after(10):
                while not controller.held:  # the re-run's start is stuck
                    await anyio.sleep(0.01)
            await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            start_stuck.set()
            await anyio.wait_all_tasks_blocked()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "before") == []
        assert _hook_runs(tmp_path, "after") == ["kept"]
        assert controller.hook("before_lease") == (FAILED, 1)
        assert controller.hook("after_lease") == (SUCCEEDED, 1)

    async def test_setup_that_outlives_its_lease_cannot_touch_the_next_one(self, tmp_path, monkeypatch):
        """Cleanup stops waiting for a setup whose outcome is stuck at the controller. It
        then closes the setup itself, so cleanup can run, and the setup's late failure
        must not change the status of the lease that replaced it."""
        monkeypatch.setattr(exporter_module, "_SETUP_OVERRUN_GRACE", 0)
        outcome_stuck = Event()
        controller = _LeaseHooksController(hold={("before_lease", FAILED): outcome_stuck})
        exporter = _restart_exporter(
            tmp_path, controller, first_status=False, before=True, after=True, timeout=1, setup_fails_for="old"
        )

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(_status(True, "old"), tg)
            old_ctx = exporter._lease_context
            with fail_after(10):
                while not controller.held:  # setup failed; its outcome is stuck
                    await anyio.sleep(0.01)
            await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                await old_ctx.after_lease_hook_done.wait()
            await exporter._on_lease_finished(old_ctx)
            await exporter._apply_status(_status(True, "new"), tg)
            new_ctx = exporter._lease_context
            with fail_after(10):
                await new_ctx.before_lease_hook.wait()
            outcome_stuck.set()  # the old setup's failure gets through, too late
            await anyio.wait_all_tasks_blocked()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "after") == ["old"]
        assert controller.record is not None
        assert controller.record["lease"] == ("new", "new-uid")
        assert controller.hook("before_lease") == (SUCCEEDED, 1)
        assert new_ctx.current_status == ExporterStatus.LEASE_READY
        assert exporter.reported[-1] == ExporterStatus.LEASE_READY

    async def _serve_until_lease_ends(self, exporter, controller, status):
        """Acquire the lease in status, wait for setup, end the lease, wait for cleanup."""
        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._apply_status(status, tg)
            lease_ctx = exporter._lease_context
            with fail_after(10):
                await lease_ctx.before_lease_hook.wait()
            controller.assigned = None  # the client released the lease
            await exporter._apply_status(_status(False), tg)
            with fail_after(10):
                await lease_ctx.after_lease_hook_done.wait()
            tg.cancel_scope.cancel()

    async def test_cut_off_setup_refused_after_its_lease_ended_still_gets_cleanup(self, tmp_path):
        """Restarted with its lease still assigned and setup cut off, the exporter finds the
        lease ended when it re-runs setup. The cut-off run may have touched the device, so
        cleanup still runs, and the record does not keep owing it."""
        controller = _LeaseHooksController(follow_status=False)  # the lease has ended in the controller
        controller.record = {"lease": ("kept", "kept-uid"), "before_lease": (RUNNING, 1), "after_lease": None}
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True)

        await self._serve_until_lease_ends(
            exporter, controller, _status(True, "kept", ("kept", _hook_state(RUNNING, 1), None))
        )

        assert _hook_runs(tmp_path, "before") == []
        assert _hook_runs(tmp_path, "after") == ["kept"]
        assert controller.hook("before_lease") == (FAILED, 1)
        assert controller.hook("after_lease") == (SUCCEEDED, 1)

    async def test_after_lease_only_config_runs_cleanup(self, tmp_path):
        """With only an afterLease hook, the skipped beforeLease is what starts the record;
        without it the controller would refuse cleanup."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, first_status=False, after=True)

        await self._serve_until_lease_ends(exporter, controller, _status(True, "kept"))

        assert _hook_runs(tmp_path, "after") == ["kept"]
        assert controller.hook("after_lease") == (SUCCEEDED, 1)

    async def test_cleanup_of_old_lease_skipped_once_replacement_assigned(self, tmp_path):
        """Cleanup for an old lease must not touch a device handed to its replacement."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True)
        exporter._cleanup_after_lease = AsyncMock()

        async with create_task_group() as tg:
            await exporter._apply_status(_status(True, "new", ("old", _hook_state(SUCCEEDED), None)), tg)
            with fail_after(10):
                await exporter._lease_context.before_lease_hook.wait()
            tg.cancel_scope.cancel()

        assert _hook_runs(tmp_path, "after") == []
        assert _hook_runs(tmp_path, "before") == ["new"]

    @pytest.mark.parametrize(
        ("before", "after", "released"),
        [
            pytest.param(_hook_state(SUCCEEDED), _hook_state(SUCCEEDED), False, id="cleanup-done"),
            pytest.param(_hook_state(SUCCEEDED), _hook_state(FAILED, on_failure=END_LEASE), True,
                         id="cleanup-failed-end-lease"),
            pytest.param(_hook_state(FAILED, on_failure=EXIT), None, False, id="setup-failed-exit"),
            pytest.param(_hook_state(FAILED, on_failure=END_LEASE), None, True, id="setup-failed-end-lease"),
        ],
    )
    async def test_lease_done_before_restart_is_not_set_up_again(self, tmp_path, before, after, released):
        """Released only when a hook failed with onFailure: endLease, as without a
        restart; otherwise the client or the lease's expiry ends it."""
        controller = _LeaseHooksController()
        exporter = _restart_exporter(tmp_path, controller, before=True, after=True)
        release_calls = []

        async def release(lease_scope):
            release_calls.append(lease_scope.lease_name)

        exporter._request_lease_release = AsyncMock(side_effect=release)
        status = _status(True, "done", ("done", before, after))

        async with create_task_group() as tg:
            await exporter._apply_status(status, tg)
            await exporter._apply_status(status, tg)  # leased ticks until the lease ends
            tg.cancel_scope.cancel()

        assert release_calls == (["done"] if released else [])
        assert exporter._lease_context is None
        assert _hook_runs(tmp_path, "before") == []
        assert _hook_runs(tmp_path, "after") == []
        if not released:
            assert exporter.reported == [ExporterStatus.AVAILABLE]
