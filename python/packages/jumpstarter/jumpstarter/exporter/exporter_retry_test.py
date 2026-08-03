import logging
import time
from unittest.mock import AsyncMock

import grpc
import pytest
from anyio import create_memory_object_stream

from jumpstarter.exporter.exporter import (
    _RETRYABLE_STREAM_CODES,
    _TRANSIENT_GRPC_CODES,
    Exporter,
    _Backoff,
    _GraceWindow,
    _is_retryable,
    _StreamClosedImmediately,
)


def _make_exporter() -> Exporter:
    mock_channel = AsyncMock()
    mock_channel.close = AsyncMock()

    async def channel_factory():
        return mock_channel

    return Exporter(
        channel_factory=channel_factory,
        device_factory=AsyncMock(),
        labels={},
    )


class TestIsRetryable:
    def test_stream_closed_immediately_is_retryable(self):
        assert _is_retryable(_StreamClosedImmediately("closed")) is True

    def test_unavailable_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.UNAVAILABLE, None, None,
        )
        assert _is_retryable(e) is True

    def test_deadline_exceeded_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.DEADLINE_EXCEEDED, None, None,
        )
        assert _is_retryable(e) is True

    def test_internal_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.INTERNAL, None, None,
        )
        assert _is_retryable(e) is True

    def test_unknown_is_retryable(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.UNKNOWN, None, None,
        )
        assert _is_retryable(e) is True

    def test_stream_codes_include_unary_transient_codes(self):
        assert _TRANSIENT_GRPC_CODES <= _RETRYABLE_STREAM_CODES

    def test_permission_denied_is_terminal(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.PERMISSION_DENIED, None, None,
        )
        assert _is_retryable(e) is False

    def test_not_found_is_terminal(self):
        e = grpc.aio.AioRpcError(
            grpc.StatusCode.NOT_FOUND, None, None,
        )
        assert _is_retryable(e) is False

    def test_connection_error_is_retryable(self):
        assert _is_retryable(ConnectionError("reset")) is True

    def test_os_error_not_connection_error_is_terminal(self):
        assert _is_retryable(OSError("network down")) is False

    def test_generic_exception_is_terminal(self):
        assert _is_retryable(ValueError("bad value")) is False


class TestGraceWindow:
    def test_starts_not_expired(self):
        w = _GraceWindow(period=10.0)
        assert w.expired() is False
        assert w.elapsed() == 0.0

    def test_mark_failure_starts_window(self):
        w = _GraceWindow(period=10.0)
        w.mark_failure()
        assert w.since is not None

    def test_reset_clears_window(self):
        w = _GraceWindow(period=10.0)
        w.mark_failure()
        w.reset()
        assert w.since is None
        assert w.expired() is False

    def test_expired_after_period(self):
        w = _GraceWindow(period=0.0)
        w.since = time.monotonic() - 1.0
        assert w.expired() is True


class TestBackoff:
    @pytest.mark.anyio
    async def test_initial_delay(self):
        b = _Backoff(max_delay=10.0)
        assert b.delay == 0.5

    @pytest.mark.anyio
    async def test_exponential_increase(self):
        b = _Backoff(max_delay=100.0)
        await b.wait()
        assert b.delay == 1.0
        await b.wait()
        assert b.delay == 2.0

    @pytest.mark.anyio
    async def test_capped_at_max(self):
        b = _Backoff(max_delay=1.0)
        await b.wait()
        await b.wait()
        await b.wait()
        assert b.delay <= 1.0

    @pytest.mark.anyio
    async def test_reset_restores_initial(self):
        b = _Backoff(max_delay=10.0)
        await b.wait()
        await b.wait()
        b.reset()
        assert b.delay == 0.5


class TestGraceWindowRetry:
    @pytest.mark.anyio
    async def test_retries_retryable_errors_within_grace_period(self):
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, None, None,
            )
            yield  # noqa: RUF028

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=0.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
        )

        assert call_count >= 1
        assert len(terminal_calls) == 1

    @pytest.mark.anyio
    async def test_terminal_error_calls_on_terminal_immediately(self):
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.PERMISSION_DENIED, None, None,
            )
            yield  # noqa: RUF028

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=300.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
        )

        assert call_count == 1
        assert len(terminal_calls) == 1

    @pytest.mark.anyio
    async def test_data_resets_grace_window(self):
        """Data flowing through should reset the grace window, allowing more retries."""
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                yield f"item-{call_count}"
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, None, None,
            )

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=5.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
        )

        # With data in calls 1-3 resetting the window each time, we get more
        # attempts than just the initial grace period would allow
        assert call_count > 3
        assert len(terminal_calls) == 1


class TestEmptyCleanCompletion:
    @pytest.mark.anyio
    async def test_stream_closed_immediately_is_retried(self):
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            return
            yield  # noqa: RUF028

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=0.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
        )

        assert call_count >= 1
        assert len(terminal_calls) == 1


class TestOnExhausted:
    @pytest.mark.anyio
    async def test_on_exhausted_resets_window_and_continues(self):
        """When on_exhausted is set, grace window expiry calls it, resets, and keeps retrying."""
        exhausted_calls = []
        use_terminal_error = False

        async def stream_factory(controller):
            if use_terminal_error:
                raise grpc.aio.AioRpcError(
                    grpc.StatusCode.PERMISSION_DENIED, None, None,
                )
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, None, None,
            )
            yield  # noqa: RUF028

        def on_exhausted(name, err):
            nonlocal use_terminal_error
            exhausted_calls.append((name, err))
            if len(exhausted_calls) >= 3:
                use_terminal_error = True

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=0.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
            on_exhausted=on_exhausted,
        )

        assert len(exhausted_calls) == 3
        assert len(terminal_calls) == 1

    @pytest.mark.anyio
    async def test_on_exhausted_not_set_falls_through_to_on_terminal(self):
        """Without on_exhausted, grace window expiry still calls on_terminal."""
        async def stream_factory(controller):
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, None, None,
            )
            yield  # noqa: RUF028

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=0.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
        )

        assert len(terminal_calls) == 1

    @pytest.mark.anyio
    async def test_terminal_error_still_fires_on_terminal_with_on_exhausted_set(self):
        """Terminal errors bypass on_exhausted and go straight to on_terminal."""
        async def stream_factory(controller):
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.PERMISSION_DENIED, None, None,
            )
            yield  # noqa: RUF028

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        exhausted_calls = []
        terminal_calls = []

        def on_exhausted(name, err):
            exhausted_calls.append((name, err))

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        await exporter._retry_stream(
            stream_name="test",
            stream_factory=stream_factory,
            send_tx=send_tx,
            grace_period=300.0,
            max_backoff=0.0,
            on_terminal=on_terminal,
            on_exhausted=on_exhausted,
        )

        assert len(terminal_calls) == 1
        assert len(exhausted_calls) == 0


class TestGraceWindowResetOnConnect:
    @pytest.mark.anyio
    async def test_window_resets_when_data_flows(self, caplog):
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                yield f"item-{call_count}"
            raise grpc.aio.AioRpcError(
                grpc.StatusCode.UNAVAILABLE, None, None,
            )

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        terminal_calls = []

        def on_terminal(name, err):
            terminal_calls.append((name, err))

        with caplog.at_level(logging.INFO, logger="jumpstarter.exporter.exporter"):
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                grace_period=5.0,
                max_backoff=0.0,
                on_terminal=on_terminal,
            )

        assert call_count > 2
        assert len(terminal_calls) == 1
