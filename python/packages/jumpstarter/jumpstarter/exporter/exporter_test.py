"""Tests for exporter state machine transitions.

These tests verify the exporter correctly handles lease lifecycle edge cases
including premature lease-end during hooks, unused lease timeouts,
consecutive leases, and idempotent lifecycle completion.
"""

from contextlib import nullcontext
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from anyio import create_task_group

from jumpstarter.common import ExporterStatus
from jumpstarter.exporter.lease_context import LeaseContext
from jumpstarter.exporter.lease_lifecycle import LeasePhase

pytestmark = pytest.mark.anyio


def make_lease_context(lease_name="test-lease", client_name="test-client"):
    ctx = LeaseContext(
        lease_name=lease_name,
        client_name=client_name,
    )
    mock_session = MagicMock()
    mock_session.context_log_source.return_value = nullcontext()
    ctx.session = mock_session
    ctx.socket_path = "/tmp/test_socket"
    ctx.hook_socket_path = "/tmp/test_hook_socket"
    ctx.lifecycle.transition(LeasePhase.STARTING)
    ctx.lifecycle.transition(LeasePhase.READY)
    return ctx


def make_lease_context_before_ready(lease_name="test-lease", client_name="test-client"):
    """Create a lease context in STARTING phase (before READY)."""
    ctx = LeaseContext(
        lease_name=lease_name,
        client_name=client_name,
    )
    mock_session = MagicMock()
    mock_session.context_log_source.return_value = nullcontext()
    ctx.session = mock_session
    ctx.socket_path = "/tmp/test_socket"
    ctx.hook_socket_path = "/tmp/test_hook_socket"
    ctx.lifecycle.transition(LeasePhase.STARTING)
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
    async def test_cleanup_waits_for_ready_before_running_ending_phase(self):
        """_cleanup_after_lease must wait for lifecycle to reach READY
        before starting the ending phase."""
        lease_ctx = make_lease_context_before_ready()
        lc = lease_ctx.lifecycle

        ending_started_before_ready = False

        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        original_run_after = hook_executor.run_after_lease_hook

        async def tracking_run_after(*args, **kwargs):
            nonlocal ending_started_before_ready
            if lc.phase == LeasePhase.STARTING:
                ending_started_before_ready = True
            return await original_run_after(*args, **kwargs)

        hook_executor.run_after_lease_hook = tracking_run_after
        exporter = make_exporter(lease_ctx, hook_executor)

        async with create_task_group() as tg:
            async def delayed_ready():
                await anyio.sleep(0.2)
                lc.transition(LeasePhase.READY)

            tg.start_soon(delayed_ready)
            await exporter._cleanup_after_lease(lease_ctx)

        assert not ending_started_before_ready
        assert lc.is_complete()

    async def test_exporter_returns_to_available_after_premature_lease_end(self):
        """After a lease ends during beforeLease hook execution, exporter
        must transition to AVAILABLE once hooks complete."""
        lease_ctx = make_lease_context()

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.lifecycle.is_complete()

    async def test_new_lease_accepted_after_recovery_from_premature_end(self):
        """After recovering from a premature lease-end, a new LeaseContext
        can be created and the exporter processes it normally."""
        lease_ctx_1 = make_lease_context(lease_name="lease-1")

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_1.lifecycle.is_complete()

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        exporter._lease_context = lease_ctx_2

        statuses.clear()
        await exporter._cleanup_after_lease(lease_ctx_2)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_2.lifecycle.is_complete()


class TestUnusedLeaseTimeout:
    async def test_unused_lease_timeout_transitions_to_available(self):
        """When a lease ends with no client session (unused lease timeout),
        the exporter must transition to AVAILABLE."""
        lease_ctx = make_lease_context(client_name="")

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx)

        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx.lifecycle.is_complete()

    async def test_unused_lease_with_hooks_runs_after_lease_when_client_present(self):
        """When a lease ends with a client (normal end or timeout after
        client connected), the afterLease hook runs."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        lease_ctx = make_lease_context(client_name="some-client")

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
        assert lease_ctx.lifecycle.is_complete()

    async def test_new_lease_after_unused_timeout_recovery(self):
        """After recovering from unused lease timeout, a new lease
        can be accepted and processed."""
        lease_ctx_1 = make_lease_context(lease_name="unused-lease", client_name="")

        statuses = []

        async def track_status(status, message=""):
            statuses.append(status)

        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock(side_effect=track_status)

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_1.lifecycle.is_complete()

        lease_ctx_2 = make_lease_context(lease_name="new-lease", client_name="real-client")
        exporter._lease_context = lease_ctx_2

        statuses.clear()
        await exporter._cleanup_after_lease(lease_ctx_2)
        assert ExporterStatus.AVAILABLE in statuses
        assert lease_ctx_2.lifecycle.is_complete()


class TestConsecutiveLeaseOrdering:
    async def test_lifecycle_completes_before_new_lease_context_created(self):
        """The serve() loop must not create a new LeaseContext until the
        previous lease's lifecycle is complete."""
        lease_ctx_1 = make_lease_context(lease_name="lease-1")
        exporter = make_exporter(lease_ctx_1)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx_1)
        assert lease_ctx_1.lifecycle.is_complete()

        exporter._lease_context = None

        lease_ctx_2 = make_lease_context(lease_name="lease-2")
        exporter._lease_context = lease_ctx_2

        await exporter._cleanup_after_lease(lease_ctx_2)
        assert lease_ctx_2.lifecycle.is_complete()

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
        assert after1_end < before2_start


class TestLifecycleSafetyTimeout:
    async def test_cleanup_handles_stuck_before_hook_via_timeout(self):
        """When lifecycle never reaches READY (race condition),
        _cleanup_after_lease must not deadlock. The safety timeout
        forces FAILED and cleanup proceeds."""
        from unittest.mock import patch

        lease_ctx = make_lease_context_before_ready()
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

        assert lease_ctx.lifecycle.phase == LeasePhase.FAILED

    async def test_safety_timeout_uses_hook_config_when_available(self):
        """When a hook executor with before_lease config is present,
        the safety timeout should use the configured hook timeout + 30s."""
        from unittest.mock import patch

        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=60),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        exporter = make_exporter(lease_ctx, hook_executor)

        captured_timeouts = []
        original_move_on_after = anyio.move_on_after

        def tracking_move_on_after(delay, *args, **kwargs):
            captured_timeouts.append(delay)
            return original_move_on_after(delay, *args, **kwargs)

        with patch("jumpstarter.exporter.exporter.move_on_after", side_effect=tracking_move_on_after):
            await exporter._cleanup_after_lease(lease_ctx)

        assert 90 in captured_timeouts


class TestIdempotentCleanup:
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
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._cleanup_after_lease(lease_ctx)
        await exporter._cleanup_after_lease(lease_ctx)

        assert after_hook_call_count == 1
        assert lease_ctx.lifecycle.is_complete()


class TestBeforeHookLifecycleWrapper:
    async def test_before_hook_transitions_to_ready_on_success(self):
        """_run_before_hook_lifecycle transitions BEFORE_LEASE → READY."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context_before_ready()
        lease_ctx.lifecycle.transition(LeasePhase.READY)
        lease_ctx_new = make_lease_context_before_ready()

        exporter = make_exporter(lease_ctx_new, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._run_before_hook_lifecycle(lease_ctx_new)
        assert lease_ctx_new.lifecycle.phase == LeasePhase.READY

    async def test_before_hook_transitions_to_ending_when_end_requested(self):
        """When end was requested during BEFORE_LEASE, transitions to ENDING."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            before_lease=HookInstanceConfigV1Alpha1(script="echo setup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context_before_ready()
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        original_run = hook_executor.run_before_lease_hook

        async def run_and_request_end(*args, **kwargs):
            await original_run(*args, **kwargs)
            lease_ctx.lifecycle.request_end()

        hook_executor.run_before_lease_hook = run_and_request_end

        await exporter._run_before_hook_lifecycle(lease_ctx)
        assert lease_ctx.lifecycle.phase == LeasePhase.ENDING


class TestRunEndingPhase:
    async def test_ending_phase_with_after_hook(self):
        """_run_ending_phase runs afterLease hook and transitions to DONE."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._run_ending_phase(lease_ctx)
        assert lease_ctx.lifecycle.phase == LeasePhase.DONE

    async def test_ending_phase_without_hook(self):
        """_run_ending_phase transitions to DONE via RELEASING when no hook."""
        lease_ctx = make_lease_context()
        exporter = make_exporter(lease_ctx)
        exporter._report_status = AsyncMock()

        await exporter._run_ending_phase(lease_ctx)
        assert lease_ctx.lifecycle.phase == LeasePhase.DONE

    async def test_ending_phase_skips_when_skip_flag_set(self):
        """_run_ending_phase skips afterLease when skip_after_lease is True."""
        from jumpstarter.config.exporter import HookConfigV1Alpha1, HookInstanceConfigV1Alpha1
        from jumpstarter.exporter.hooks import HookExecutor

        hook_config = HookConfigV1Alpha1(
            after_lease=HookInstanceConfigV1Alpha1(script="echo cleanup", timeout=10),
        )
        hook_executor = HookExecutor(config=hook_config)

        lease_ctx = make_lease_context()
        lease_ctx.lifecycle.skip_after_lease = True
        exporter = make_exporter(lease_ctx, hook_executor)
        exporter._report_status = AsyncMock()

        await exporter._run_ending_phase(lease_ctx)
        assert lease_ctx.lifecycle.phase == LeasePhase.DONE
        assert lease_ctx.lifecycle.phase != LeasePhase.AFTER_LEASE
