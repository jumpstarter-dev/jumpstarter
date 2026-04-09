import logging
from unittest.mock import AsyncMock

import pytest
from anyio import create_memory_object_stream

from jumpstarter.exporter.exporter import Exporter


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


class TestRetryCounterResetsAfterReceivingData:
    @pytest.mark.anyio
    async def test_survives_more_than_retries_cycles_when_data_received(self):
        retries = 3
        data_cycles = retries * 3
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= data_cycles:
                yield f"item-{call_count}"
            raise Exception("connection lost")

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        with pytest.raises(Exception, match="connection lost"):
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                retries=retries,
                backoff=0.0,
            )

        expected_total = data_cycles + retries
        assert call_count == expected_total

    @pytest.mark.anyio
    async def test_does_not_reset_when_error_before_any_data(self):
        retries = 3
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise Exception("UNAVAILABLE")
            yield  # make it an async generator

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        with pytest.raises(Exception, match="UNAVAILABLE"):
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                retries=retries,
                backoff=0.0,
            )

        assert call_count == retries + 1


class TestExporterFailsFastOnPersistentErrors:
    @pytest.mark.anyio
    async def test_raises_after_exhausting_retries_without_data(self):
        retries = 5
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            raise Exception("permanently unreachable")
            yield

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        with pytest.raises(Exception, match="permanently unreachable"):
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                retries=retries,
                backoff=0.0,
            )

        assert call_count == retries + 1

    @pytest.mark.anyio
    async def test_retries_left_decrements_on_consecutive_failures(self):
        retries = 4
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise Exception("third failure")
            raise Exception("failure")
            yield

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        with pytest.raises(Exception, match="failure"):
            await exporter._retry_stream(
                stream_name="test",
                stream_factory=stream_factory,
                send_tx=send_tx,
                retries=retries,
                backoff=0.0,
            )

        assert call_count == retries + 1


class TestRetryCounterResetLogging:
    @pytest.mark.anyio
    async def test_logs_debug_message_when_retry_counter_resets(self, caplog):
        retries = 2
        call_count = 0

        async def stream_factory(controller):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                yield f"item-{call_count}"
            raise Exception("connection lost")

        exporter = _make_exporter()
        send_tx, send_rx = create_memory_object_stream[str](100)

        with caplog.at_level(logging.DEBUG, logger="jumpstarter.exporter.exporter"):
            with pytest.raises(Exception, match="connection lost"):
                await exporter._retry_stream(
                    stream_name="test",
                    stream_factory=stream_factory,
                    send_tx=send_tx,
                    retries=retries,
                    backoff=0.0,
                )

        reset_messages = [r for r in caplog.records if "retry counter reset" in r.message.lower()]
        assert len(reset_messages) == 1
