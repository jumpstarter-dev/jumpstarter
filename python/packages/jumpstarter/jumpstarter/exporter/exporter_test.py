"""Tests for exporter state machine transitions.

These tests verify the exporter correctly handles lease lifecycle edge cases
including premature lease-end during hooks, unused lease timeouts,
consecutive leases, and idempotent lease-end signals.
"""

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from anyio import Event, create_task_group

from jumpstarter.common import ExporterStatus
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
        lease_ctx.lease_ended.set()

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
        lease_ctx.lease_ended.set()

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
        lease_ctx_1.lease_ended.set()

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
        lease_ctx_2.lease_ended.set()
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
        lease_ctx.lease_ended.set()

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
        lease_ctx.lease_ended.set()

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
        lease_ctx_1.lease_ended.set()

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
        lease_ctx_2.lease_ended.set()
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
        lease_ctx_1.lease_ended.set()

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert lease_ctx_1.after_lease_hook_done.is_set()

        exporter._lease_context = None

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        lease_ctx_2.lease_ended.set()
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
        lease_ctx_1.lease_ended.set()
        exporter = make_exporter(lease_ctx_1, hook_executor)
        exporter._report_status = AsyncMock()

        await hook_executor.run_before_lease_hook(
            lease_ctx_1, exporter._report_status, exporter.stop, exporter._request_lease_release
        )
        await exporter._cleanup_after_lease(lease_ctx_1)

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        lease_ctx_2.lease_ended.set()
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
        exporter = make_exporter(lease_ctx)

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter._report_status = AsyncMock(side_effect=track_status)

        original_move_on_after = anyio.move_on_after

        def fast_move_on_after(delay, *args, **kwargs):
            return original_move_on_after(0.1, *args, **kwargs)

        with patch("jumpstarter.exporter.exporter.move_on_after", side_effect=fast_move_on_after):
            await exporter._cleanup_after_lease(lease_ctx)

        assert lease_ctx.before_lease_hook.is_set(), (
            "before_lease_hook should be force-set after safety timeout"
        )
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
        lease_ctx.before_lease_hook.set()

        exporter = make_exporter(lease_ctx, hook_executor)

        captured_timeouts = []
        original_move_on_after = anyio.move_on_after

        def tracking_move_on_after(delay, *args, **kwargs):
            captured_timeouts.append(delay)
            return original_move_on_after(delay, *args, **kwargs)

        with patch("jumpstarter.exporter.exporter.move_on_after", side_effect=tracking_move_on_after):
            await exporter._cleanup_after_lease(lease_ctx)

        assert 90 in captured_timeouts, (
            f"Expected safety timeout of 90s (60 + 30), got timeouts: {captured_timeouts}"
        )


class TestHandleLeaseFinally:
    async def test_finally_sets_before_lease_hook_on_early_cancel(self):
        """When conn_tg is cancelled before before_lease_hook.set() is
        reached (no hook executor path), the finally block must ensure
        the event is set so _cleanup_after_lease can proceed."""
        lease_ctx = make_lease_context()
        assert not lease_ctx.before_lease_hook.is_set()

        exporter = make_exporter(lease_ctx)
        exporter.uuid = "test-uuid"
        exporter.labels = {}
        exporter.tls = None
        exporter.grpc_options = None

        if not lease_ctx.before_lease_hook.is_set():
            lease_ctx.before_lease_hook.set()

        assert lease_ctx.before_lease_hook.is_set(), (
            "before_lease_hook must be set after the finally-block logic"
        )


class TestClientDisconnectWithoutEndSession:
    async def test_cleanup_skips_after_lease_hook_on_disconnect_without_end_session(self):
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

        assert after_hook_call_count == 0, (
            f"afterLease hook ran {after_hook_call_count} times but should have been "
            "skipped when client disconnects without EndSession on active lease"
        )

    async def test_cleanup_does_not_transition_to_available_on_disconnect(self):
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE not in statuses, (
            f"Exporter transitioned to AVAILABLE on client disconnect without "
            f"EndSession. Statuses: {statuses}"
        )

    async def test_cleanup_does_not_call_request_lease_release_on_disconnect(self):
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()

        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()
        exporter._request_lease_release = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)

        exporter._request_lease_release.assert_not_called()

    async def test_cleanup_runs_after_lease_hook_when_lease_ended(self):
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
        lease_ctx.lease_ended.set()

        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1, (
            f"afterLease hook ran {after_hook_call_count} times, expected 1 "
            "when lease_ended is set"
        )

    async def test_cleanup_runs_after_lease_hook_when_end_session_requested(self):
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
        lease_ctx.end_session_requested.set()

        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1, (
            f"afterLease hook ran {after_hook_call_count} times, expected 1 "
            "when end_session_requested is set"
        )

    async def test_cleanup_runs_after_lease_hook_when_stop_requested(self):
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
        exporter._stop_requested = True
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1, (
            f"afterLease hook ran {after_hook_call_count} times, expected 1 "
            "when _stop_requested is True"
        )

    async def test_cleanup_without_hooks_skips_available_on_disconnect(self):
        lease_ctx = make_lease_context()
        lease_ctx.before_lease_hook.set()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx, hook_executor=None)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE not in statuses, (
            f"Exporter transitioned to AVAILABLE on client disconnect without "
            f"EndSession (no hooks). Statuses: {statuses}"
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
        lease_ctx.lease_ended.set()
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)
        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1, (
            f"afterLease hook ran {after_hook_call_count} times, expected exactly 1"
        )
        assert lease_ctx.after_lease_hook_done.is_set()
