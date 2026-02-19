"""Tests for StatusMonitor background polling and status tracking."""

from unittest.mock import MagicMock

import anyio
import pytest
from grpc import StatusCode
from grpc.aio import AioRpcError

from jumpstarter.client.status_monitor import StatusMonitor
from jumpstarter.common import ExporterStatus

pytestmark = pytest.mark.anyio


class MockAioRpcError(AioRpcError):
    """Mock gRPC error for testing that properly inherits from AioRpcError."""

    def __init__(self, status_code: StatusCode, message: str = ""):
        self._status_code = status_code
        self._message = message

    def code(self) -> StatusCode:
        return self._status_code

    def details(self) -> str:
        return self._message


def create_mock_rpc_error(code: StatusCode, details: str = "") -> MockAioRpcError:
    """Create a mock AioRpcError with the specified status code."""
    return MockAioRpcError(code, details)


def create_status_response(
    status: ExporterStatus,
    version: int = 1,
    message: str = "",
    previous_status: ExporterStatus | None = None,
):
    """Create a mock GetStatusResponse."""
    response = MagicMock()
    response.status = status.to_proto()
    response.status_version = version
    response.message = message
    if previous_status is not None:
        response.HasField.return_value = True
        response.previous_status = previous_status.to_proto()
    else:
        response.HasField.return_value = False
    return response


class MockExporterStub:
    """Mock stub that returns predefined status responses."""

    def __init__(self, responses: list | None = None, repeat_last: bool = True):
        self._responses = list(responses or [])
        self._index = 0
        self._call_count = 0
        self._repeat_last = repeat_last

    async def GetStatus(self, request, timeout=None):
        self._call_count += 1
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            if isinstance(response, Exception):
                raise response
            return response
        elif self._repeat_last and self._responses:
            # Repeat the last response
            last = self._responses[-1]
            if isinstance(last, Exception):
                raise last
            return last
        else:
            # Default to AVAILABLE if no responses
            return create_status_response(ExporterStatus.AVAILABLE, version=self._call_count)


class TestStatusMonitorProperties:
    async def test_current_status_property(self) -> None:
        """Test that current_status property returns cached status."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)

        # Initially None
        assert monitor.current_status is None

        # After manual update (simulating poll)
        monitor._current_status = ExporterStatus.AVAILABLE
        assert monitor.current_status == ExporterStatus.AVAILABLE

    async def test_status_version_property(self) -> None:
        """Test that status_version property returns cached version."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)

        assert monitor.status_version == 0

        monitor._status_version = 5
        assert monitor.status_version == 5

    async def test_connection_lost_property(self) -> None:
        """Test that connection_lost property returns connection state."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)

        assert not monitor.connection_lost

        monitor._connection_lost = True
        assert monitor.connection_lost

    async def test_missed_transitions_property(self) -> None:
        """Test that missed_transitions property tracks missed updates."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)

        assert monitor.missed_transitions == 0

        monitor._missed_transitions = 3
        assert monitor.missed_transitions == 3


class TestStatusMonitorPolling:
    async def test_poll_loop_updates_status(self) -> None:
        """Test that poll loop updates current status from GetStatus."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.BEFORE_LEASE_HOOK, version=2),
            create_status_response(ExporterStatus.LEASE_READY, version=3),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            # Wait for a few poll cycles
            await anyio.sleep(0.2)
            await monitor.stop()

        assert monitor.current_status is not None
        assert stub._call_count >= 3

    async def test_poll_loop_tracks_version(self) -> None:
        """Test that poll loop tracks status version."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=5),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.1)
            await monitor.stop()

        assert monitor.status_version == 5

    async def test_poll_loop_detects_missed_transitions(self) -> None:
        """Test that poll loop detects and counts missed transitions."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.LEASE_READY, version=5),  # Skipped 2,3,4
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.15)
            await monitor.stop()

        assert monitor.missed_transitions == 3  # Missed versions 2, 3, 4

    async def test_poll_loop_slow_polling_when_idle(self) -> None:
        """Test that poll loop uses slow interval when idle in LEASE_READY."""
        responses = [
            create_status_response(ExporterStatus.LEASE_READY, version=1),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)
        monitor._slow_poll_interval = 0.5  # Set long slow poll

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.2)  # Less than slow poll interval
            call_count = stub._call_count
            await monitor.stop()

        # Should only have polled 1-2 times due to slow polling
        assert call_count <= 2

    async def test_poll_loop_fast_polling_with_waiters(self) -> None:
        """Test that poll loop uses critical interval when waiters are active."""
        responses = [
            create_status_response(ExporterStatus.LEASE_READY, version=1),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)
        monitor._critical_poll_interval = 0.05  # Set critical interval for test
        monitor._slow_poll_interval = 1.0

        # Simulate active waiter - should use critical polling
        monitor._active_waiters = 1

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.2)
            call_count = stub._call_count
            await monitor.stop()

        # Should have polled multiple times due to critical polling
        assert call_count >= 3

    async def test_poll_loop_critical_polling_with_waiters(self) -> None:
        """Test that poll loop uses critical (fastest) interval when waiters are active.

        Critical polling uses 0.1s interval to catch brief status changes
        before connections close.
        """
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ]
        stub = MockExporterStub(responses)
        # Set normal poll interval much higher to distinguish from critical
        monitor = StatusMonitor(stub, poll_interval=0.5)
        monitor._critical_poll_interval = 0.05  # Very fast for testing
        monitor._slow_poll_interval = 1.0

        # Simulate active waiter - should use critical polling
        monitor._active_waiters = 1

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.25)
            call_count = stub._call_count
            await monitor.stop()

        # With 0.05s critical interval and 0.25s sleep, should poll ~5 times
        # With 0.5s normal interval, would only poll ~1 time
        assert call_count >= 4

    async def test_poll_loop_handles_unimplemented(self) -> None:
        """Test that poll loop exits gracefully on UNIMPLEMENTED."""
        error = create_mock_rpc_error(StatusCode.UNIMPLEMENTED)
        responses = [error]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.1)
            # Poll loop should have exited
            await monitor.stop()

        assert stub._call_count == 1  # Only tried once before giving up

    async def test_poll_loop_handles_unavailable(self) -> None:
        """Test that poll loop sets connection_lost on UNAVAILABLE."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.UNAVAILABLE),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.15)
            await monitor.stop()

        assert monitor.connection_lost

    async def test_poll_loop_handles_deadline_exceeded(self) -> None:
        """Test that poll loop treats DEADLINE_EXCEEDED as transient.

        DEADLINE_EXCEEDED means the RPC timed out, not that the connection is
        dead. The monitor should continue polling rather than setting
        connection_lost. Only UNAVAILABLE indicates a true connection loss.
        """
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED),
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED),
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED),
            create_status_response(ExporterStatus.LEASE_READY, version=2),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.5)
            await monitor.stop()

        # DEADLINE_EXCEEDED should NOT cause connection_lost
        assert not monitor.connection_lost
        # Monitor should have recovered and reached LEASE_READY
        assert monitor.current_status == ExporterStatus.LEASE_READY

    async def test_poll_loop_deadline_exceeded_threshold(self) -> None:
        """Test that poll loop marks connection lost after threshold DEADLINE_EXCEEDED.

        If GetStatus times out 20+ consecutive times (~100s at 5s/timeout),
        the monitor should treat this as a permanently stuck connection and
        set connection_lost.
        """
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ] + [
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED)
            for _ in range(25)
        ]
        stub = MockExporterStub(responses, repeat_last=False)
        monitor = StatusMonitor(stub, poll_interval=0.01)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            # Wait for all DEADLINE_EXCEEDED errors to be processed
            await anyio.sleep(3.0)
            await monitor.stop()

        # After 20+ consecutive DEADLINE_EXCEEDED, connection should be marked lost
        assert monitor.connection_lost

    async def test_poll_loop_deadline_exceeded_below_threshold(self) -> None:
        """Test that DEADLINE_EXCEEDED below threshold does not mark connection lost.

        10 consecutive timeouts is well below the threshold of 20, so the
        monitor should recover when a successful response arrives.
        """
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ] + [
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED)
            for _ in range(10)
        ] + [
            create_status_response(ExporterStatus.LEASE_READY, version=2),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.01)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(1.0)
            await monitor.stop()

        # 10 consecutive DEADLINE_EXCEEDED (below 20 threshold) should recover
        assert not monitor.connection_lost
        assert monitor.current_status == ExporterStatus.LEASE_READY


class TestStatusMonitorWaitForStatus:
    async def test_wait_for_status_already_at_target(self) -> None:
        """Test wait_for_status returns immediately when already at target."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)
        monitor._current_status = ExporterStatus.LEASE_READY
        monitor._running = True

        result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=1.0)

        assert result is True

    async def test_wait_for_status_transitions_to_target(self) -> None:
        """Test wait_for_status waits until target status is reached."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.BEFORE_LEASE_HOOK, version=2),
            create_status_response(ExporterStatus.LEASE_READY, version=3),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=2.0)

            await monitor.stop()

        assert result is True
        assert monitor.current_status == ExporterStatus.LEASE_READY

    async def test_wait_for_status_timeout(self) -> None:
        """Test wait_for_status returns False on timeout."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=0.15)

            await monitor.stop()

        assert result is False

    async def test_wait_for_status_connection_lost(self) -> None:
        """Test wait_for_status returns False when connection is lost."""
        # Return UNAVAILABLE to simulate connection loss
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.UNAVAILABLE),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=0.5)

            await monitor.stop()

        assert result is False
        assert monitor.connection_lost

    async def test_wait_for_status_unimplemented_returns_promptly(self) -> None:
        """Test wait_for_status returns promptly when UNIMPLEMENTED is received.

        When GetStatus returns UNIMPLEMENTED (old exporter without status support),
        the monitor should signal waiters so they don't hang indefinitely.
        """
        # First return AVAILABLE, then UNIMPLEMENTED to simulate an old exporter
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.UNIMPLEMENTED),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            # Wait for a status that will never be reached - should return promptly
            # when UNIMPLEMENTED is received, not hang until timeout
            result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=2.0)

            await monitor.stop()

        # Should return False promptly (well before the 2s timeout)
        assert result is False
        # Monitor should have stopped running
        assert not monitor._running

    async def test_wait_for_status_deadline_exceeded_keeps_polling(self) -> None:
        """Test wait_for_status keeps waiting through DEADLINE_EXCEEDED.

        DEADLINE_EXCEEDED is transient - the monitor should keep polling and
        the wait should succeed once the status eventually arrives.
        """
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED),
            create_mock_rpc_error(StatusCode.DEADLINE_EXCEEDED),
            create_status_response(ExporterStatus.LEASE_READY, version=2),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            # Wait should succeed after DEADLINE_EXCEEDED errors clear
            result = await monitor.wait_for_status(ExporterStatus.LEASE_READY, timeout=2.0)

            await monitor.stop()

        assert result is True
        assert not monitor.connection_lost
        assert monitor.current_status == ExporterStatus.LEASE_READY


class TestStatusMonitorWaitForAnyOf:
    async def test_wait_for_any_of_already_at_target(self) -> None:
        """Test wait_for_any_of returns immediately when already at one of targets."""
        stub = MockExporterStub()
        monitor = StatusMonitor(stub, poll_interval=0.1)
        monitor._current_status = ExporterStatus.LEASE_READY
        monitor._running = True

        targets = [ExporterStatus.LEASE_READY, ExporterStatus.AVAILABLE]
        result = await monitor.wait_for_any_of(targets, timeout=1.0)

        assert result == ExporterStatus.LEASE_READY

    async def test_wait_for_any_of_first_match_wins(self) -> None:
        """Test wait_for_any_of returns first matching status."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.BEFORE_LEASE_HOOK, version=2),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            targets = [ExporterStatus.BEFORE_LEASE_HOOK, ExporterStatus.LEASE_READY]
            result = await monitor.wait_for_any_of(targets, timeout=2.0)

            await monitor.stop()

        assert result == ExporterStatus.BEFORE_LEASE_HOOK

    async def test_wait_for_any_of_timeout_returns_none(self) -> None:
        """Test wait_for_any_of returns None on timeout."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            targets = [ExporterStatus.LEASE_READY, ExporterStatus.AFTER_LEASE_HOOK]
            result = await monitor.wait_for_any_of(targets, timeout=0.15)

            await monitor.stop()

        assert result is None

    async def test_wait_for_any_of_connection_lost(self) -> None:
        """Test wait_for_any_of returns None when connection is lost."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.UNAVAILABLE),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            targets = [ExporterStatus.LEASE_READY]
            result = await monitor.wait_for_any_of(targets, timeout=0.5)

            await monitor.stop()

        assert result is None

    async def test_wait_for_any_of_unimplemented_returns_promptly(self) -> None:
        """Test wait_for_any_of returns promptly when UNIMPLEMENTED is received.

        When GetStatus returns UNIMPLEMENTED (old exporter without status support),
        the monitor assumes LEASE_READY for backward compatibility. If the caller
        is waiting for LEASE_READY, it should be returned promptly without hanging.
        """
        # First return AVAILABLE, then UNIMPLEMENTED to simulate an old exporter
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_mock_rpc_error(StatusCode.UNIMPLEMENTED),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)

            # _signal_unsupported sets status to LEASE_READY for backward compat,
            # so wait_for_any_of should return LEASE_READY promptly
            targets = [ExporterStatus.LEASE_READY, ExporterStatus.AFTER_LEASE_HOOK]
            result = await monitor.wait_for_any_of(targets, timeout=2.0)

            await monitor.stop()

        # Should return LEASE_READY promptly (backward compat fallback)
        assert result == ExporterStatus.LEASE_READY
        # Monitor should have stopped running
        assert not monitor._running


class TestStatusMonitorLifecycle:
    async def test_start_sets_running_flag(self) -> None:
        """Test that start() sets the running flag."""
        stub = MockExporterStub([
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ])
        monitor = StatusMonitor(stub, poll_interval=0.1)

        assert not monitor._running

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            assert monitor._running
            await monitor.stop()

    async def test_stop_clears_running_flag(self) -> None:
        """Test that stop() clears the running flag."""
        stub = MockExporterStub([
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ])
        monitor = StatusMonitor(stub, poll_interval=0.1)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await monitor.stop()

        assert not monitor._running

    async def test_context_manager_stops_on_exit(self) -> None:
        """Test that context manager stops monitor on exit."""
        stub = MockExporterStub([
            create_status_response(ExporterStatus.AVAILABLE, version=1),
        ])
        monitor = StatusMonitor(stub, poll_interval=0.1)
        monitor._running = True

        async with monitor:
            pass

        assert not monitor._running


class TestStatusMonitorCallbacks:
    async def test_on_status_change_callback(self) -> None:
        """Test that status change callbacks are invoked."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.LEASE_READY, version=2),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        callback_invocations = []

        async def callback(new_status, old_status):
            callback_invocations.append((new_status, old_status))

        monitor.on_status_change(callback)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.15)
            await monitor.stop()

        # Should have at least one callback for the transition
        assert len(callback_invocations) >= 1

    async def test_callback_exception_does_not_stop_monitor(self) -> None:
        """Test that callback exceptions don't stop the monitor."""
        responses = [
            create_status_response(ExporterStatus.AVAILABLE, version=1),
            create_status_response(ExporterStatus.LEASE_READY, version=2),
            create_status_response(ExporterStatus.AFTER_LEASE_HOOK, version=3),
        ]
        stub = MockExporterStub(responses)
        monitor = StatusMonitor(stub, poll_interval=0.05)

        async def failing_callback(new_status, old_status):
            raise ValueError("Callback error")

        monitor.on_status_change(failing_callback)

        async with anyio.create_task_group() as tg:
            await monitor.start(tg)
            await anyio.sleep(0.2)
            await monitor.stop()

        # Monitor should still have polled multiple times despite callback errors
        assert stub._call_count >= 2


class TestStatusMonitorStatusMessageUpdate:
    async def test_wait_for_status_updates_status_message(self) -> None:
        """Test that wait_for_status verification poll updates _status_message.

        When connection_lost is True and the verification poll recovers,
        _status_message must be updated from the response so callers don't
        read a stale message from a previous status.
        """
        stub = MockExporterStub([
            create_status_response(
                ExporterStatus.AFTER_LEASE_HOOK_FAILED,
                version=5,
                message="hook script exited with code 1",
            ),
        ])
        monitor = StatusMonitor(stub, poll_interval=0.1)
        monitor._running = True
        monitor._connection_lost = True
        monitor._status_message = "Ready for commands"  # stale from LEASE_READY

        result = await monitor.wait_for_status(
            ExporterStatus.AFTER_LEASE_HOOK_FAILED, timeout=2.0
        )

        assert result is True
        assert monitor.status_message == "hook script exited with code 1"

    async def test_wait_for_any_of_updates_status_message(self) -> None:
        """Test that wait_for_any_of verification poll updates _status_message.

        When connection_lost is True and the verification poll recovers,
        _status_message must be updated from the response so callers don't
        read a stale message from a previous status.
        """
        stub = MockExporterStub([
            create_status_response(
                ExporterStatus.AFTER_LEASE_HOOK_FAILED,
                version=5,
                message="hook script exited with code 1",
            ),
        ])
        monitor = StatusMonitor(stub, poll_interval=0.1)
        monitor._running = True
        monitor._connection_lost = True
        monitor._status_message = "Ready for commands"  # stale from LEASE_READY

        result = await monitor.wait_for_any_of(
            [ExporterStatus.AVAILABLE, ExporterStatus.AFTER_LEASE_HOOK_FAILED],
            timeout=2.0,
        )

        assert result == ExporterStatus.AFTER_LEASE_HOOK_FAILED
        assert monitor.status_message == "hook script exited with code 1"
