"""Tests for the exporter MetricsStream client (JEP-0013 Phase 3 PR C).

Copyright 2026. The Jumpstarter Authors.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

import urllib.request
from unittest.mock import MagicMock, patch

import grpc
import pytest
from anyio import EndOfStream, create_memory_object_stream, create_task_group, fail_after, sleep
from jumpstarter_protocol import telemetry_pb2

from jumpstarter.exporter.metrics_stream import MetricsStreamClient
from jumpstarter.metrics.registry import MetricsRegistry
from jumpstarter.metrics.server import start_metrics_server

pytestmark = pytest.mark.anyio

IDENTITY = "board-42"
TOKEN = "exporter-jwt"


class FakeMetricsCall:
    """In-memory stand-in for a grpc.aio stream-stream MetricsStream call."""

    def __init__(self):
        self.writes: list[telemetry_pb2.MetricsStreamRequest] = []
        self._send, self._recv = create_memory_object_stream[telemetry_pb2.MetricsStreamResponse](32)

    async def write(self, msg: telemetry_pb2.MetricsStreamRequest) -> None:
        self.writes.append(msg)

    def __aiter__(self):
        return self

    async def __anext__(self) -> telemetry_pb2.MetricsStreamResponse:
        try:
            return await self._recv.receive()
        except EndOfStream as exc:
            raise StopAsyncIteration from exc

    async def push_scrape(self) -> None:
        await self._send.send(
            telemetry_pb2.MetricsStreamResponse(
                scrape_request=telemetry_pb2.MetricsScrapeRequest(),
            )
        )

    async def close_from_server(self) -> None:
        await self._send.aclose()


class RecordingStub:
    def __init__(self, call: FakeMetricsCall | None = None, *, error: Exception | None = None):
        self.call = call
        self.error = error
        self.calls: list[dict] = []

    def MetricsStream(self, *args, **kwargs):
        assert not args, "MetricsStream client must use write/read style, not a request iterator"
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.call is not None
        return self.call


async def _wait_until(pred, *, timeout: float = 2.0) -> None:
    with fail_after(timeout):
        while not pred():
            await sleep(0.01)


def _record_op(reg: MetricsRegistry, *, exporter: str = IDENTITY) -> None:
    reg.record_operation(
        exporter=exporter,
        operation="power",
        result="success",
        driver_type="power",
        duration_seconds=0.05,
    )


def test_module_documents_local_http_metrics_is_not_replaced():
    """CLI loopback :0 scrape from JEP-0013 Phase 2 stays; reverse-scrape is additive."""
    import jumpstarter.exporter.metrics_stream as mod

    assert "does not replace" in (mod.__doc__ or "").lower()


class TestMetricsStreamProtocol:
    async def test_first_message_is_register_with_exporter_identity(self):
        call = FakeMetricsCall()
        stub = RecordingStub(call)
        client = MetricsStreamClient(stub, identity=IDENTITY, token=TOKEN)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            tg.cancel_scope.cancel()

        first = call.writes[0]
        assert first.WhichOneof("msg") == "register"
        assert first.register.identity == IDENTITY

    async def test_sends_bearer_token_metadata(self):
        call = FakeMetricsCall()
        stub = RecordingStub(call)
        client = MetricsStreamClient(stub, identity=IDENTITY, token=TOKEN)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: stub.calls)
            tg.cancel_scope.cancel()

        assert stub.calls[0]["metadata"] == [("authorization", f"Bearer {TOKEN}")]

    async def test_scrape_replies_with_generate_latest_bytes(self):
        reg = MetricsRegistry()
        _record_op(reg)
        expected = reg.generate_latest()

        call = FakeMetricsCall()
        client = MetricsStreamClient(RecordingStub(call), identity=IDENTITY, token=TOKEN, registry=reg)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        scrape = call.writes[1]
        assert scrape.WhichOneof("msg") == "scrape_response"
        assert scrape.scrape_response.metrics_text == expected
        assert scrape.scrape_response.HasField("timestamp")
        assert scrape.scrape_response.timestamp.seconds > 0

    async def test_empty_registry_still_sends_scrape_response(self):
        reg = MetricsRegistry()
        empty = reg.generate_latest()
        assert empty  # OpenMetrics text is still produced with no samples

        call = FakeMetricsCall()
        client = MetricsStreamClient(RecordingStub(call), identity=IDENTITY, token="", registry=reg)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        scrape = call.writes[1]
        assert scrape.WhichOneof("msg") == "scrape_response"
        assert scrape.scrape_response.metrics_text == empty

    async def test_scrape_bytes_match_local_http_metrics(self):
        """Reverse-scrape payload must be the same bytes as GET /metrics (lab loopback)."""
        reg = MetricsRegistry()
        _record_op(reg)
        listen, shutdown = start_metrics_server("127.0.0.1:0", registry=reg)
        try:
            http_body = urllib.request.urlopen(f"http://{listen}/metrics").read()
        finally:
            assert shutdown is not None
            shutdown()

        call = FakeMetricsCall()
        client = MetricsStreamClient(RecordingStub(call), identity=IDENTITY, token=TOKEN, registry=reg)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        assert call.writes[1].scrape_response.metrics_text == http_body

    async def test_cancel_stops_run(self):
        call = FakeMetricsCall()
        client = MetricsStreamClient(RecordingStub(call), identity=IDENTITY, token=TOKEN)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            tg.cancel_scope.cancel()

        # Leaving the task group without timeout is the assertion: cancel unwinds.

    async def test_reconnect_keeps_local_counters(self):
        """Telemetry unavailable: counters stay local; next scrape is full current state."""
        reg = MetricsRegistry()
        _record_op(reg)
        expected = reg.generate_latest()

        failing = RecordingStub(
            error=grpc.aio.AioRpcError(
                code=grpc.StatusCode.UNAVAILABLE,
                initial_metadata=None,
                trailing_metadata=None,
            )
        )
        call = FakeMetricsCall()
        succeeding = RecordingStub(call)
        stubs = iter([failing, succeeding])

        def metrics_stream(*args, **kwargs):
            return next(stubs).MetricsStream(*args, **kwargs)

        stub = MagicMock()
        stub.MetricsStream = metrics_stream

        client = MetricsStreamClient(
            stub, identity=IDENTITY, token=TOKEN, registry=reg, backoff_base=0.01, backoff_cap=0.01
        )

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        assert call.writes[0].WhichOneof("msg") == "register"
        assert call.writes[1].scrape_response.metrics_text == expected
        assert b"jumpstarter_operations_total" in expected

    async def test_reconnect_uses_backoff(self):
        delays: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            delays.append(seconds)
            await sleep(0)

        call = FakeMetricsCall()
        attempts = {"n": 0}

        def metrics_stream(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise grpc.aio.AioRpcError(
                    code=grpc.StatusCode.UNAVAILABLE,
                    initial_metadata=None,
                    trailing_metadata=None,
                )
            return call

        stub = MagicMock()
        stub.MetricsStream = metrics_stream
        client = MetricsStreamClient(
            stub, identity=IDENTITY, token=TOKEN, backoff_base=0.5, backoff_cap=2.0
        )

        with patch("jumpstarter.exporter.metrics_stream.sleep", fake_sleep):
            async with create_task_group() as tg:
                tg.start_soon(client.run)
                await _wait_until(lambda: len(call.writes) >= 1)
                tg.cancel_scope.cancel()

        assert delays[0] == 0.5
        assert attempts["n"] >= 2
