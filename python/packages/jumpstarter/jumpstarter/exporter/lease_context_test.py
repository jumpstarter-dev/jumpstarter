"""Tests for LeaseContext dataclass."""

from unittest.mock import MagicMock

import anyio
import pytest

from jumpstarter.common import ExporterStatus
from jumpstarter.exporter.lease_context import LeaseContext
from jumpstarter.exporter.lease_lifecycle import LeaseLifecycle, LeasePhase

pytestmark = pytest.mark.anyio


class TestLeaseContextInitialization:
    def test_init_with_required_fields(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")

        assert ctx.lease_name == "test-lease"
        assert isinstance(ctx.lifecycle, LeaseLifecycle)
        assert ctx.lifecycle.phase == LeasePhase.CREATED
        assert ctx.session is None
        assert ctx.socket_path == ""
        assert ctx.hook_socket_path == ""
        assert ctx.client_name == ""
        assert ctx.current_status == ExporterStatus.AVAILABLE
        assert ctx.status_message == ""

    def test_init_validates_lease_name_non_empty(self) -> None:
        with pytest.raises(AssertionError, match="non-empty lease_name"):
            LeaseContext(lease_name="")

    def test_end_session_requested_event_created(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        assert ctx.end_session_requested is not None
        assert not ctx.end_session_requested.is_set()


class TestLeaseContextStateQueries:
    def test_is_ready_false_without_session(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.socket_path = "/tmp/socket"
        assert not ctx.is_ready()

    def test_is_ready_false_without_socket_path(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.session = MagicMock()
        assert not ctx.is_ready()

    def test_is_ready_true_with_both(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.session = MagicMock()
        ctx.socket_path = "/tmp/socket"
        assert ctx.is_ready()

    def test_is_active_true_with_lease_name(self) -> None:
        ctx = LeaseContext(lease_name="active-lease")
        assert ctx.is_active()

    def test_has_client_true_with_client_name(self) -> None:
        ctx = LeaseContext(lease_name="test-lease", client_name="my-client")
        assert ctx.has_client()

    def test_has_client_false_without_client_name(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        assert not ctx.has_client()


class TestLeaseContextClientManagement:
    def test_update_client_sets_name(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.update_client("new-client")
        assert ctx.client_name == "new-client"
        assert ctx.has_client()

    def test_clear_client_removes_name(self) -> None:
        ctx = LeaseContext(lease_name="test-lease", client_name="my-client")
        ctx.clear_client()
        assert ctx.client_name == ""
        assert not ctx.has_client()


class TestLeaseContextStatusUpdates:
    def test_update_status_stores_status(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.update_status(ExporterStatus.LEASE_READY, "ready to go")
        assert ctx.current_status == ExporterStatus.LEASE_READY
        assert ctx.status_message == "ready to go"

    def test_update_status_propagates_to_session(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        mock_session = MagicMock()
        ctx.session = mock_session
        ctx.update_status(ExporterStatus.BEFORE_LEASE_HOOK, "running hook")
        mock_session.update_status.assert_called_once_with(
            ExporterStatus.BEFORE_LEASE_HOOK, "running hook"
        )

    def test_update_status_without_session_no_error(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.update_status(ExporterStatus.AVAILABLE, "available")
        assert ctx.current_status == ExporterStatus.AVAILABLE


class TestLeaseContextDriversReady:
    def test_drivers_ready_false_before_ready_phase(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        assert not ctx.drivers_ready()

    def test_drivers_ready_true_after_ready_phase(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        ctx.lifecycle.transition(LeasePhase.STARTING)
        ctx.lifecycle.transition(LeasePhase.READY)
        assert ctx.drivers_ready()

    async def test_wait_for_drivers_blocks_until_ready(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")

        async def transition_after_delay():
            await anyio.sleep(0.05)
            ctx.lifecycle.transition(LeasePhase.STARTING)
            ctx.lifecycle.transition(LeasePhase.READY)

        async with anyio.create_task_group() as tg:
            tg.start_soon(transition_after_delay)
            await ctx.wait_for_drivers()

        assert ctx.drivers_ready()


class TestLeaseContextSkipAfterLeaseHook:
    def test_skip_after_lease_hook_delegates_to_lifecycle(self) -> None:
        ctx = LeaseContext(lease_name="test-lease")
        assert ctx.skip_after_lease_hook is False
        ctx.skip_after_lease_hook = True
        assert ctx.skip_after_lease_hook is True
        assert ctx.lifecycle.skip_after_lease is True
