from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import grpc
import pytest
from anyio import create_memory_object_stream, create_task_group

from jumpstarter.exporter.exporter import (
    Exporter,
    _Backoff,
    _GraceWindow,
    _is_retryable,
    _StreamClosedImmediately,
)


def _make_exporter() -> Exporter:
    exporter = Exporter(
        channel_factory=AsyncMock(),
        device_factory=AsyncMock(),
        labels={},
    )

    @asynccontextmanager
    async def _fake_controller_stub():
        yield object()

    exporter._controller_stub = _fake_controller_stub
    return exporter


class TestIsRetryable:
    def test_unavailable_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.UNAVAILABLE, None, None, details="service unavailable"
        )
        assert _is_retryable(e) is True

    def test_deadline_exceeded_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.DEADLINE_EXCEEDED, None, None, details="timed out"
        )
        assert _is_retryable(e) is True

    def test_permission_denied_is_terminal(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.PERMISSION_DENIED, None, None, details="denied"
        )
        assert _is_retryable(e) is False

    def test_not_found_is_terminal(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.NOT_FOUND, None, None, details="not found"
        )
        assert _is_retryable(e) is False

    def test_connection_error_is_retryable(self):
        assert _is_retryable(ConnectionError("reset")) is True

    def test_os_error_is_retryable(self):
        assert _is_retryable(OSError("broken pipe")) is True

    def test_stream_closed_immediately_is_retryable(self):
        assert _is_retryable(_StreamClosedImmediately("closed")) is True

    def test_programming_error_is_terminal(self):
        assert _is_retryable(AttributeError("no such attr")) is False
        assert _is_retryable(TypeError("bad type")) is False


class TestGraceWindowRetry:
    @pytest.mark.anyio
    async def test_recovers_within_grace_period(self):
        """Stream that fails then recovers should continue."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise ConnectionError("connection lost")
            yield "recovered"

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        async with create_task_group() as tg:
            exporter._tg = tg

            async def run_stream():
                await exporter._retry_stream(
                    stream_name="test",
                    stream_factory=stream_factory,
                    send_tx=send_tx,
                    grace_period=10.0,
                    max_backoff=0.01,
                )

            async def check_recovery():
                item = await send_rx.receive()
                assert item == "recovered"
                tg.cancel_scope.cancel()

            tg.start_soon(run_stream)
            tg.start_soon(check_recovery)

        assert call_count >= 4

    @pytest.mark.anyio
    async def test_cancels_scope_after_grace_period(self):
        """Continuous failures past grace period should cancel tg, not raise."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise ConnectionError("connection lost")
            yield  # noqa: E501

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                grace_period=0.1,
                max_backoff=0.01,
            )

        assert exporter._fatal_stream_error is not None
        assert exporter._fatal_stream_error[0] == "test"
        assert call_count >= 2

    @pytest.mark.anyio
    async def test_terminal_error_cancels_immediately(self):
        """Non-retryable error should cancel without waiting for grace period."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.PERMISSION_DENIED, None, None, details="denied"
            )
            yield  # noqa: E501

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                grace_period=10.0,
                max_backoff=0.01,
            )

        assert call_count == 1
        assert exporter._fatal_stream_error is not None
        assert exporter._fatal_stream_error[0] == "test"


class TestEmptyCleanCompletion:
    @pytest.mark.anyio
    async def test_empty_clean_completions_exhaust_grace_period(self):
        """Streams returning zero items should exhaust grace period and fire on_terminal."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            return
            yield  # noqa: E501

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        async with create_task_group() as tg:
            exporter._tg = tg
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                grace_period=0.1,
                max_backoff=0.01,
            )

        assert exporter._fatal_stream_error is not None
        assert exporter._fatal_stream_error[0] == "test"
        assert "closed immediately" in str(exporter._fatal_stream_error[1])
        assert call_count >= 2

    @pytest.mark.anyio
    async def test_empty_then_data_recovers(self):
        """Empty completions followed by data should reset degradation."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return
            yield "recovered"

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        async with create_task_group() as tg:
            exporter._tg = tg

            async def run_stream():
                await exporter._retry_stream(
                    stream_name="test",
                    stream_factory=stream_factory,
                    send_tx=send_tx,
                    grace_period=10.0,
                    max_backoff=0.01,
                )

            async def check_recovery():
                item = await send_rx.receive()
                assert item == "recovered"
                tg.cancel_scope.cancel()

            tg.start_soon(run_stream)
            tg.start_soon(check_recovery)

        assert call_count >= 4
        assert exporter._fatal_stream_error is None


class TestGraceWindowResetOnData:
    @pytest.mark.anyio
    async def test_grace_window_resets_after_receiving_data(self):
        """Receiving data should reset the degradation window."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                yield f"item-{call_count}"
            raise ConnectionError("connection lost")

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)
        items = []

        async with create_task_group() as tg:
            exporter._tg = tg

            async def run_stream():
                await exporter._retry_stream(
                    stream_name="test",
                    stream_factory=stream_factory,
                    send_tx=send_tx,
                    grace_period=0.2,
                    max_backoff=0.01,
                )

            async def collect():
                async for item in send_rx:
                    items.append(item)
                    if len(items) >= 3:
                        tg.cancel_scope.cancel()
                        return

            tg.start_soon(run_stream)
            tg.start_soon(collect)

        assert len(items) >= 3


class TestGraceWindow:
    def test_fresh_mark_failure_returns_zero(self):
        w = _GraceWindow(period=10.0)
        assert w.since is None
        elapsed = w.mark_failure()
        assert elapsed == 0.0
        assert w.since is not None

    def test_expired_after_period(self):
        w = _GraceWindow(period=0.0)
        w.mark_failure()
        assert w.expired() is True

    def test_not_expired_within_period(self):
        w = _GraceWindow(period=100.0)
        w.mark_failure()
        assert w.expired() is False

    def test_reset_clears_since(self):
        w = _GraceWindow(period=10.0)
        w.mark_failure()
        assert w.since is not None
        w.reset()
        assert w.since is None
        assert w.expired() is False

    def test_elapsed_zero_when_no_failure(self):
        w = _GraceWindow(period=10.0)
        assert w.elapsed() == 0.0


class TestBackoff:
    def test_initial_delay_capped_by_max(self):
        b = _Backoff(max_delay=0.1)
        assert b.delay == 0.1

    def test_initial_delay_default(self):
        b = _Backoff(max_delay=10.0)
        assert b.delay == 0.5

    def test_reset_restores_initial(self):
        b = _Backoff(max_delay=10.0)
        b.delay = 5.0
        b.reset()
        assert b.delay == 0.5

    @pytest.mark.anyio
    async def test_wait_increases_delay(self):
        b = _Backoff(max_delay=10.0)
        initial = b.delay
        await b.wait()
        assert b.delay > initial

    @pytest.mark.anyio
    async def test_delay_capped_at_max(self):
        b = _Backoff(max_delay=1.0)
        for _ in range(20):
            await b.wait()
        assert b.delay <= 1.0
