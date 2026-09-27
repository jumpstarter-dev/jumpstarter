from functools import partial
from unittest.mock import AsyncMock, Mock

import grpc
import pytest
from anyio import Event, create_memory_object_stream, create_task_group, fail_after, sleep_forever
from grpc.aio import AioRpcError

from jumpstarter.common.exceptions import CertificateDiscoveryError, ConnectionError, ExporterOfflineError
from jumpstarter.exporter.exporter import Exporter


def _rpc_error(code, details=""):
    return AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details)


def _make_exporter(channel_factory=None):
    return Exporter(
        channel_factory=channel_factory or AsyncMock(return_value=Mock(close=AsyncMock())),
        device_factory=AsyncMock(),
        labels={},
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        _rpc_error(grpc.StatusCode.UNAVAILABLE),
        _rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED),
        _rpc_error(grpc.StatusCode.UNKNOWN, "oidc: authenticator not initialized"),
        _rpc_error(grpc.StatusCode.UNKNOWN, "Stream removed"),
        _rpc_error(grpc.StatusCode.INTERNAL, "Received RST_STREAM with error code 2"),
        _rpc_error(grpc.StatusCode.CANCELLED, "stream cancelled by ingress"),
    ],
)
async def test_stream_recovers_after_repeated_failures_and_remains_cancellable(error):
    attempts = 0
    closed = False

    async def stream_factory(controller):
        nonlocal attempts, closed
        attempts += 1
        if attempts <= 12:
            raise error
        try:
            yield "recovered"
            await sleep_forever()
        finally:
            closed = True

    exporter = _make_exporter()
    tx, rx = create_memory_object_stream(1)
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(partial(exporter._retry_stream, "Listen", stream_factory, tx, backoff=0))
            assert await rx.receive() == "recovered"
            tg.cancel_scope.cancel()
    assert attempts == 13
    assert closed


@pytest.mark.anyio
async def test_retries_certificate_discovery_connection_errors():
    channel = Mock(close=AsyncMock())
    failure = CertificateDiscoveryError("Failed connecting to controller:443 - all IPs exhausted")
    channel_factory = AsyncMock(side_effect=[failure] * 12 + [channel])
    exporter = _make_exporter(channel_factory)

    async def stream_factory(controller):
        yield "recovered"
        await sleep_forever()

    tx, rx = create_memory_object_stream(1)
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(partial(exporter._retry_stream, "Status", stream_factory, tx, backoff=0))
            assert await rx.receive() == "recovered"
            tg.cancel_scope.cancel()
    assert channel_factory.await_count == 13
    channel.close.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        _rpc_error(grpc.StatusCode.PERMISSION_DENIED),
        _rpc_error(grpc.StatusCode.UNAUTHENTICATED),
        _rpc_error(grpc.StatusCode.UNKNOWN, "unexpected server failure"),
        _rpc_error(grpc.StatusCode.INTERNAL, "unexpected server failure"),
        ConnectionError("grpc error: permission denied"),
        ConnectionError("Failed connecting to controller:443 - all IPs exhausted"),
        ExporterOfflineError("exporter offline"),
        ValueError("bad configuration"),
    ],
)
async def test_permanent_errors_propagate_without_retry(error):
    calls = 0

    async def stream_factory(controller):
        nonlocal calls
        calls += 1
        raise error
        yield

    exporter = _make_exporter()
    tx, _ = create_memory_object_stream(1)
    with pytest.raises(type(error)) as caught:
        await exporter._retry_stream("Listen", stream_factory, tx, backoff=0)
    assert caught.value is error
    assert calls == 1


@pytest.mark.anyio
async def test_empty_stream_backs_off_and_can_be_cancelled(monkeypatch):
    from anyio.lowlevel import checkpoint

    delays = []

    async def record_sleep(delay):
        delays.append(delay)
        await checkpoint()

    monkeypatch.setattr("jumpstarter.exporter.exporter.sleep", record_sleep)

    async def stream_factory(controller):
        if len(delays) >= 10:
            raise ValueError("stop")
        return
        yield

    exporter = _make_exporter()
    tx, _ = create_memory_object_stream(1)
    with pytest.raises(ValueError, match="stop"):
        await exporter._retry_stream("Listen", stream_factory, tx)
    assert len(delays) == 10
    assert all(0 < delay <= 5 for delay in delays)
    assert delays[-1] == 5


@pytest.mark.anyio
async def test_cancel_during_outage_stops_reconnection(monkeypatch):
    retrying = Event()

    async def backoff(delay):
        retrying.set()
        await sleep_forever()

    monkeypatch.setattr("jumpstarter.exporter.exporter.sleep", backoff)
    failure = CertificateDiscoveryError("Failed connecting to controller:443 - all IPs exhausted")
    channel_factory = AsyncMock(side_effect=failure)
    exporter = _make_exporter(channel_factory)
    tx, _ = create_memory_object_stream(1)
    with fail_after(2):
        async with create_task_group() as tg:
            tg.start_soon(exporter._retry_stream, "Status", AsyncMock(), tx)
            await retrying.wait()
            tg.cancel_scope.cancel()
    channel_factory.assert_awaited_once()
