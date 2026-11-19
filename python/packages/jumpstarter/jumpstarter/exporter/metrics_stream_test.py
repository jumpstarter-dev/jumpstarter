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

import logging
import urllib.request
from typing import cast
from unittest.mock import MagicMock, patch

import anyio.lowlevel
import anyio.to_thread
import grpc
import pytest
from anyio import EndOfStream, create_memory_object_stream, create_task_group, fail_after, sleep
from jumpstarter_protocol import telemetry_pb2, telemetry_pb2_grpc

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
        self.cancelled = False
        self._send, self._recv = create_memory_object_stream[telemetry_pb2.MetricsStreamResponse](32)

    def cancel(self) -> None:
        self.cancelled = True

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


def _client(
    stub: RecordingStub,
    *,
    identity: str,
    token: str = "",
    registry: MetricsRegistry | None = None,
) -> MetricsStreamClient:
    """Build a client from a test double. The production parameter is the generated stub."""
    return MetricsStreamClient(
        cast(telemetry_pb2_grpc.TelemetryServiceStub, stub),
        identity=identity,
        token=token,
        registry=registry,
    )


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
        client = _client(stub, identity=IDENTITY, token=TOKEN)

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
        client = _client(stub, identity=IDENTITY, token=TOKEN)

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
        client = _client(RecordingStub(call), identity=IDENTITY, token=TOKEN, registry=reg)

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
        assert scrape.scrape_response.families

    async def test_scrape_families_carry_exemplars(self):
        reg = MetricsRegistry()
        reg.record_operation(
            exporter=IDENTITY,
            operation="power",
            result="success",
            driver_type="power",
            duration_seconds=0.05,
            exemplars={"client": "ci-bot", "lease_id": "lease-xyz"},
        )
        expected = reg.generate_latest()

        call = FakeMetricsCall()
        client = _client(RecordingStub(call), identity=IDENTITY, token=TOKEN, registry=reg)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        resp = call.writes[1].scrape_response
        assert resp.metrics_text == expected
        names = {fam.name: fam for fam in resp.families}
        counter = names["jumpstarter_operations_total"]
        ex_labels = {lp.name: lp.value for lp in counter.samples[0].exemplar.labels}
        assert ex_labels["client"] == "ci-bot"
        assert ex_labels["lease_id"] == "lease-xyz"

    async def test_empty_registry_still_sends_scrape_response(self):
        reg = MetricsRegistry()
        empty = reg.generate_latest()
        assert empty  # OpenMetrics text is still produced with no samples

        call = FakeMetricsCall()
        client = _client(RecordingStub(call), identity=IDENTITY, token="", registry=reg)

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
            http_body = await anyio.to_thread.run_sync(
                lambda: urllib.request.urlopen(f"http://{listen}/metrics").read()
            )
        finally:
            assert shutdown is not None
            shutdown()

        call = FakeMetricsCall()
        client = _client(RecordingStub(call), identity=IDENTITY, token=TOKEN, registry=reg)

        async with create_task_group() as tg:
            tg.start_soon(client.run)
            await _wait_until(lambda: len(call.writes) >= 1)
            await call.push_scrape()
            await _wait_until(lambda: len(call.writes) >= 2)
            tg.cancel_scope.cancel()

        assert call.writes[1].scrape_response.metrics_text == http_body

    async def test_cancel_stops_run(self):
        call = FakeMetricsCall()
        client = _client(RecordingStub(call), identity=IDENTITY, token=TOKEN)

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
            await anyio.lowlevel.checkpoint()

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

    async def test_scrape_build_failure_cancels_before_retry(self):
        """Building a scrape response must not leave the RPC open across the retry."""

        class _Call(FakeMetricsCall):
            def __init__(self) -> None:
                super().__init__()
                self.order: list[str] = []

            def cancel(self) -> None:
                self.order.append("cancel")
                super().cancel()

        call = _Call()
        client = MetricsStreamClient(
            cast(telemetry_pb2_grpc.TelemetryServiceStub, RecordingStub(call)),
            identity=IDENTITY,
            token=TOKEN,
            backoff_base=0.5,
            backoff_cap=2.0,
        )

        async def fake_sleep(seconds: float) -> None:
            call.order.append(f"sleep:{seconds}")
            await anyio.lowlevel.checkpoint()

        with (
            patch(
                "jumpstarter.exporter.metrics_stream.scrape_response_from_registry",
                side_effect=RuntimeError("scrape failed"),
            ),
            patch("jumpstarter.exporter.metrics_stream.sleep", fake_sleep),
        ):
            async with create_task_group() as tg:
                tg.start_soon(client.run)
                await _wait_until(lambda: len(call.writes) >= 1)
                await call.push_scrape()
                await _wait_until(lambda: any(item.startswith("sleep:") for item in call.order))
                tg.cancel_scope.cancel()

        assert call.order[0] == "cancel"
        assert call.cancelled is True
        assert call.order[1] == "sleep:0.5"


def _rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code=code, initial_metadata=None, trailing_metadata=None)


def _stream_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "jumpstarter.exporter.metrics_stream"]


class TestMetricsStreamAuthLogging:
    async def test_auth_rejection_warns_on_transition(self, caplog: pytest.LogCaptureFixture):
        """The same auth status is a warning once; a different one warns again."""
        hanging = FakeMetricsCall()
        codes = [
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.UNAUTHENTICATED,
        ]
        steps = iter(codes)

        def metrics_stream(*args, **kwargs):
            try:
                code = next(steps)
            except StopIteration:
                return hanging
            raise _rpc_error(code)

        stub = MagicMock()
        stub.MetricsStream = metrics_stream
        client = MetricsStreamClient(stub, identity=IDENTITY, token=TOKEN, backoff_base=0.01, backoff_cap=0.01)

        with caplog.at_level(logging.DEBUG):
            async with create_task_group() as tg:
                tg.start_soon(client.run)
                await _wait_until(lambda: len(hanging.writes) >= 1)
                tg.cancel_scope.cancel()

        records = _stream_records(caplog)
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        assert warnings == [
            "MetricsStream rejected (PERMISSION_DENIED); retrying in 0.01s",
            "MetricsStream rejected (UNAUTHENTICATED); retrying in 0.01s",
        ]
        assert any(
            record.levelno == logging.DEBUG and "PERMISSION_DENIED" in record.getMessage() for record in records
        )

    async def test_auth_rejection_warns_again_after_connect(self, caplog: pytest.LogCaptureFixture):
        """A completed session clears the rejection so the next one warns again."""
        recovered = FakeMetricsCall()
        hanging = FakeMetricsCall()
        steps = iter(["denied", "connect", "denied"])

        def metrics_stream(*args, **kwargs):
            try:
                step = next(steps)
            except StopIteration:
                return hanging
            if step == "connect":
                return recovered
            raise _rpc_error(grpc.StatusCode.PERMISSION_DENIED)

        stub = MagicMock()
        stub.MetricsStream = metrics_stream
        client = MetricsStreamClient(stub, identity=IDENTITY, token=TOKEN, backoff_base=0.01, backoff_cap=0.01)

        with caplog.at_level(logging.DEBUG):
            async with create_task_group() as tg:
                tg.start_soon(client.run)
                await _wait_until(lambda: len(recovered.writes) >= 1)
                await recovered.close_from_server()
                await _wait_until(
                    lambda: sum(record.levelno == logging.WARNING for record in _stream_records(caplog)) >= 2
                )
                tg.cancel_scope.cancel()

        records = _stream_records(caplog)
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        infos = [record.getMessage() for record in records if record.levelno == logging.INFO]
        assert warnings == [
            "MetricsStream rejected (PERMISSION_DENIED); retrying in 0.01s",
            "MetricsStream rejected (PERMISSION_DENIED); retrying in 0.01s",
        ]
        assert infos == ["MetricsStream connected"]

    async def test_unavailable_clears_auth_rejection_without_warning(self, caplog: pytest.LogCaptureFixture):
        """A transient outage stays at debug and lets the next auth rejection warn."""
        hanging = FakeMetricsCall()
        codes = [
            grpc.StatusCode.PERMISSION_DENIED,
            grpc.StatusCode.UNAVAILABLE,
            grpc.StatusCode.PERMISSION_DENIED,
        ]
        steps = iter(codes)

        def metrics_stream(*args, **kwargs):
            try:
                code = next(steps)
            except StopIteration:
                return hanging
            raise _rpc_error(code)

        stub = MagicMock()
        stub.MetricsStream = metrics_stream
        client = MetricsStreamClient(stub, identity=IDENTITY, token=TOKEN, backoff_base=0.01, backoff_cap=0.01)

        with caplog.at_level(logging.DEBUG):
            async with create_task_group() as tg:
                tg.start_soon(client.run)
                await _wait_until(lambda: len(hanging.writes) >= 1)
                tg.cancel_scope.cancel()

        records = _stream_records(caplog)
        warnings = [record.getMessage() for record in records if record.levelno == logging.WARNING]
        assert warnings == [
            "MetricsStream rejected (PERMISSION_DENIED); retrying in 0.01s",
            "MetricsStream rejected (PERMISSION_DENIED); retrying in 0.01s",
        ]
        assert any(record.levelno == logging.DEBUG and "UNAVAILABLE" in record.getMessage() for record in records)
        assert not any(record.levelno == logging.WARNING and "UNAVAILABLE" in record.getMessage() for record in records)
