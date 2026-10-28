from __future__ import annotations

import threading
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
import pytest
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    create_memory_object_stream,
    create_task_group,
)
from anyio.lowlevel import checkpoint
from anyio.streams.stapled import StapledObjectStream
from anyio.to_thread import run_sync as run_sync_in_worker

from .fanout import (
    BufferOverflowError,
    ClientBuffer,
    ExclusiveSessionActive,
    ExclusiveStream,
    FanOutStreamMixin,
    ReadOnlyStreamError,
    StreamFanOut,
    WriteTokenRevokedError,
    _Reader,
)


@pytest.fixture
async def running():
    async with AsyncExitStack() as stack:

        async def enter(fanout: StreamFanOut) -> StreamFanOut:
            return await stack.enter_async_context(fanout.running())

        yield enter


class TestClientBuffer:
    def test_push_pull_basic(self):
        buf = ClientBuffer(max_bytes=1024)
        buf.push(b"hello")
        assert buf._buf[0] == b"hello"

    @pytest.mark.anyio
    async def test_pull_blocks_until_data(self):
        buf = ClientBuffer(max_bytes=1024)

        async with create_task_group() as tg:
            result = []

            async def puller():
                result.append(await buf.pull())

            tg.start_soon(puller)
            await anyio.sleep(0.01)
            assert result == []
            buf.push(b"data")
            await anyio.sleep(0.01)
            assert result == [b"data"]

    def test_drop_oldest_on_overflow(self):
        buf = ClientBuffer(max_bytes=10, on_overflow="drop")
        buf.push(b"aaaaa")  # 5 bytes
        buf.push(b"bbbbb")  # 5 bytes, at limit
        buf.push(b"ccccc")  # 5 bytes, drops oldest
        assert buf._dropped_bytes > 0

    def test_error_on_overflow(self):
        buf = ClientBuffer(max_bytes=10, on_overflow="error")
        buf.push(b"aaaaa")  # 5 bytes
        buf.push(b"bbbbb")  # 5 bytes, at limit
        buf.push(b"ccccc")  # 5 bytes, should close
        assert buf.closed

    @pytest.mark.anyio
    async def test_pull_after_close(self):
        buf = ClientBuffer(max_bytes=1024)
        buf.close()
        with pytest.raises(ClosedResourceError):
            await buf.pull()

    @pytest.mark.anyio
    async def test_pull_drains_before_close_error(self):
        buf = ClientBuffer(max_bytes=1024)
        buf.push(b"data")
        buf.close()
        assert await buf.pull() == b"data"
        with pytest.raises(ClosedResourceError):
            await buf.pull()

    def test_prefill(self):
        buf = ClientBuffer(max_bytes=1024)
        buf.prefill([b"chunk1", b"chunk2"])
        assert buf._current_bytes == 12

    @pytest.mark.anyio
    async def test_drop_marker_on_resume(self):
        buf = ClientBuffer(max_bytes=10, on_overflow="drop")
        buf.push(b"aaaaa")
        buf.push(b"bbbbb")
        buf.push(b"cc")  # drops "aaaaa"
        data = await buf.pull()
        assert b"bytes dropped" in data

    def test_byte_bounded_not_item_bounded(self):
        buf = ClientBuffer(max_bytes=100)
        buf.push(b"x" * 60)
        buf.push(b"y" * 60)  # should trigger drop of first
        assert buf._dropped_bytes == 60



def _make_memory_source():
    """Create a pair of memory streams usable as a source factory."""
    tx, rx = create_memory_object_stream[bytes](32)
    return tx, rx


@asynccontextmanager
async def _memory_source_factory(tx_send, rx_recv):
    """Source factory that yields a stapled stream from pre-created channels."""
    stream = StapledObjectStream(tx_send, rx_recv)
    yield stream



class TestStreamFanOut:
    @pytest.mark.anyio
    async def test_exclusive_basic(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            await a_tx.send(b"hello")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"hello" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_second_exclusive_raises(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive():
            with pytest.raises(ExclusiveSessionActive):
                async with fanout.attach_exclusive():
                    pass

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_receives_same_data(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as primary, fanout.attach_observer() as observer:
            await a_tx.send(b"shared data")
            await anyio.sleep(0.05)
            primary_data = await primary.receive()
            observer_data = await observer.receive()
            assert b"shared data" in primary_data
            assert b"shared data" in observer_data

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_send_raises(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive(), fanout.attach_observer() as observer:
            with pytest.raises(ReadOnlyStreamError):
                await observer.send(b"should fail")

        await fanout.close()

    @pytest.mark.anyio
    async def test_primary_close_frees_token(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=True))

        async with fanout.attach_exclusive():
            pass
        # token released, should be claimable again
        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_before_exclusive(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=True))

        async with fanout.attach_observer() as observer:
            await a_tx.send(b"before exclusive")
            await anyio.sleep(0.05)
            data = await observer.receive()
            assert b"before exclusive" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_release_write_token(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            await fanout.release_write_token()
            with pytest.raises(WriteTokenRevokedError):
                await stream.send(b"should fail")

        await fanout.close()

    @pytest.mark.anyio
    async def test_scrollback_replay(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, scrollback_size=4096, always_on=True))

        async with fanout.attach_exclusive() as primary:
            await a_tx.send(b"history line 1\n")
            await a_tx.send(b"history line 2\n")
            await anyio.sleep(0.05)
            # drain primary
            await primary.receive()
            await primary.receive()

            # now attach observer — should get scrollback
            async with fanout.attach_observer() as observer:
                data = await observer.receive()
                assert b"history" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_last_detach_clears_scrollback_before_next_observer(self, running):
        sources = []

        @asynccontextmanager
        async def factory():
            tx, rx = create_memory_object_stream[bytes](32)
            sink, _ = create_memory_object_stream[bytes](32)
            sources.append(tx)
            yield StapledObjectStream(sink, rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))
        async with fanout.attach_observer() as first:
            await sources[-1].send(b"old output")
            with anyio.fail_after(1):
                assert await first.receive() == b"old output"

        assert fanout.status()["scrollback_bytes"] == 0
        assert not fanout.status()["reader_running"]

        async with fanout.attach_observer() as second:
            await sources[-1].send(b"new output")
            with anyio.fail_after(1):
                assert await second.receive() == b"new output"

        await fanout.close()

    @pytest.mark.anyio
    async def test_exclusive_session_active_message(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive():
            with pytest.raises(ExclusiveSessionActive) as exc_info:
                async with fanout.attach_exclusive():
                    pass

        assert "Console in use" in str(exc_info.value)

        await fanout.close()

    @pytest.mark.anyio
    async def test_status(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive(), fanout.attach_observer():
            status = fanout.status()
            assert status["write_token_held"]
            assert "write_token_holder" not in status
            assert status["observer_count"] == 1
            assert status["total_clients"] == 2

        await fanout.close()

    @pytest.mark.anyio
    async def test_overflow_error_policy(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive(on_overflow="error", buffer_bytes=10) as stream:
            for _ in range(20):
                await a_tx.send(b"x" * 10)
            await anyio.sleep(0.1)
            with pytest.raises((BufferOverflowError, ClosedResourceError)):
                while True:
                    await stream.receive()

        await fanout.close()

    @pytest.mark.anyio
    async def test_close_with_error(self):
        buf = ClientBuffer(max_bytes=1024)
        err = RuntimeError("test error")
        buf.close(error=err)
        assert buf.closed
        assert buf._close_error is err

    @pytest.mark.anyio
    async def test_exclusive_send_when_source_none(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            fanout._source = None
            with pytest.raises(BrokenResourceError):
                await stream.send(b"no source")

        await fanout.close()

    @pytest.mark.anyio
    async def test_send_eof_is_noop(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            await stream.send_eof()

        await fanout.close()

    @pytest.mark.anyio
    async def test_extra_attributes_empty(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            assert stream.extra_attributes == {}
            assert stream.extra("missing", default="fallback") == "fallback"

        await fanout.close()

    @pytest.mark.anyio
    async def test_extra_raises_without_default(self, running):
        from anyio import TypedAttributeLookupError

        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            with pytest.raises(TypedAttributeLookupError):
                stream.extra("missing")

        await fanout.close()

    @pytest.mark.anyio
    async def test_async_iteration(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            assert stream.__aiter__() is stream
            await a_tx.send(b"iter-data")
            await anyio.sleep(0.05)
            data = await stream.__anext__()
            assert b"iter-data" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_anext_raises_stop_on_close(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_observer() as stream:
            fanout._clients[stream._id].close()
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()

        await fanout.close()

    @pytest.mark.anyio
    async def test_context_manager_protocol(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            s = await stream.__aenter__()
            assert s is stream
            await stream.__aexit__(None, None, None)

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_aclose_idempotent(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive(), fanout.attach_observer() as observer:
            await observer.aclose()
            await observer.aclose()

        await fanout.close()

    @pytest.mark.anyio
    async def test_scrollback_overflow_trims(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, scrollback_size=20, always_on=False))

        async with fanout.attach_exclusive() as stream:
            await a_tx.send(b"a" * 15)
            await a_tx.send(b"b" * 15)
            await anyio.sleep(0.05)
            await stream.receive()
            await stream.receive()
            assert fanout._scrollback_bytes <= 20

        await fanout.close()

    def test_extra_returns_attribute_value(self):
        buf = ClientBuffer(max_bytes=1024)
        stream = ExclusiveStream(fanout=None, client_id=1, buffer=buf)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
        original = type(stream).extra_attributes
        try:
            type(stream).extra_attributes = property(  # ty: ignore[invalid-assignment]
                lambda self: {"peername": lambda: ("127.0.0.1", 8080)}
            )
            assert stream.extra("peername") == ("127.0.0.1", 8080)
        finally:
            type(stream).extra_attributes = original

    @pytest.mark.anyio
    async def test_exclusive_send_to_source(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async with fanout.attach_exclusive() as stream:
            await stream.send(b"outbound")
            data = await b_rx.receive()
            assert data == b"outbound"

        await fanout.close()

    @pytest.mark.anyio
    async def test_reconnect_broadcasts_message(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)
        call_count = 0

        @asynccontextmanager
        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                err_tx, err_rx = create_memory_object_stream[bytes](1)
                stream = StapledObjectStream(err_tx, err_rx)
                await err_rx.aclose()
                yield stream
            else:
                yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))
        collected = bytearray()

        async with fanout.attach_observer() as obs:
            with anyio.fail_after(2.0):
                while b"reconnected" not in collected:
                    collected += await obs.receive()

        assert b"reconnected" in collected
        await fanout.close()

    @pytest.mark.anyio
    async def test_stop_reader_handles_a_failed_reader(self, running, monkeypatch):
        @asynccontextmanager
        async def factory():
            yield None

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))

        async def failing_reader():
            raise RuntimeError("boom")

        monkeypatch.setattr(fanout, "_reader_loop", failing_reader)
        async with fanout._lock:
            await fanout._ensure_started()
        await fanout._stop_reader()
        assert fanout._reader is None
        assert not fanout._started

    @pytest.mark.anyio
    async def test_unexpected_reader_failure_resets_start_state(self, running, monkeypatch):
        @asynccontextmanager
        async def factory():
            yield None

        fanout = await running(StreamFanOut(source_factory=factory))

        async def failing_reader():
            raise RuntimeError("unexpected source failure")

        monkeypatch.setattr(fanout, "_reader_loop", failing_reader)
        fanout._started = True
        fanout._source_ready.set()

        await fanout._run_reader(_Reader())

        assert not fanout._started
        assert not fanout._source_ready.is_set()

    @pytest.mark.anyio
    async def test_unexpected_reader_failure_disconnects_clients(self, running, monkeypatch):
        @asynccontextmanager
        async def factory():
            yield None

        fanout = await running(StreamFanOut(source_factory=factory))
        buf = ClientBuffer()
        fanout._clients[1] = buf
        fanout._write_token_holder = 1
        fanout._started = True

        async def failing_reader():
            raise RuntimeError("unexpected source failure")

        monkeypatch.setattr(fanout, "_reader_loop", failing_reader)
        await fanout._run_reader(_Reader())

        assert buf.closed
        with pytest.raises(ClosedResourceError):
            await buf.pull()
        assert not fanout._clients
        assert not fanout.status()["write_token_held"]
        assert not fanout._started

    @pytest.mark.anyio
    async def test_kick_keeps_reader_alive_but_shutdown_stops_it(self, running):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _ = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory))
        async with fanout.attach_exclusive() as first:
            reader = fanout._reader
            assert reader is not None

            fanout.kick_sync()
            assert not fanout._shutdown
            assert fanout._reader is reader
            assert not reader.done.is_set()
            with pytest.raises(ClosedResourceError):
                await first.receive()

            async with fanout.attach_exclusive() as replacement:
                await a_tx.send(b"after kick")
                with anyio.fail_after(1):
                    assert await replacement.receive() == b"after kick"

                fanout.shutdown_sync()
                assert fanout._shutdown
                assert fanout._reader is None
                with pytest.raises(ClosedResourceError):
                    await replacement.receive()

        with anyio.fail_after(1):
            while not reader.done.is_set():
                await checkpoint()
        assert reader.done.is_set()


    @pytest.mark.anyio
    async def test_kick_stops_lazy_reader_once_kicked_client_unwinds(self, running):
        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _ = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = await running(StreamFanOut(source_factory=factory, always_on=False))
        async with fanout.attach_exclusive():
            reader = fanout._reader
            assert reader is not None
            fanout.kick_sync()
            # The kick itself leaves the reader running...
            assert fanout._reader is reader
            assert not reader.done.is_set()

        # ...until the kicked client's attach context detaches the last client.
        assert fanout._reader is None
        assert reader.done.is_set()
        assert not fanout.status()["reader_running"]


@asynccontextmanager
async def _idle_source():
    _tx, rx = create_memory_object_stream[bytes](32)
    sink, _ = create_memory_object_stream[bytes](32)
    yield StapledObjectStream(sink, rx)


class TestRunning:
    @pytest.mark.anyio
    async def test_attaching_outside_running_raises(self):
        fanout = StreamFanOut(source_factory=_idle_source)

        with pytest.raises(RuntimeError, match="not running"):
            async with fanout.attach_observer():
                pass

    @pytest.mark.anyio
    async def test_running_twice_raises(self):
        fanout = StreamFanOut(source_factory=_idle_source)

        async with fanout.running():
            with pytest.raises(RuntimeError, match="already running"):
                async with fanout.running():
                    pass

    @pytest.mark.anyio
    async def test_leaving_running_closes_the_source_and_wakes_clients(self):
        closed = anyio.Event()

        @asynccontextmanager
        async def source_with_async_close():
            async with _idle_source() as stream:
                try:
                    yield stream
                finally:
                    await checkpoint()
                    closed.set()

        fanout = StreamFanOut(source_factory=source_with_async_close)
        async with fanout.running():
            attachment = fanout.attach_observer()
            stream = await attachment.__aenter__()

        assert closed.is_set()
        with pytest.raises(ClosedResourceError):
            await stream.receive()
        await attachment.__aexit__(None, None, None)

    @pytest.mark.anyio
    async def test_a_detach_in_a_cancelled_scope_still_releases_the_client(self, running):
        fanout = await running(StreamFanOut(source_factory=_idle_source))

        with anyio.CancelScope() as scope:
            async with fanout.attach_exclusive():
                scope.cancel()
                await checkpoint()

        status = fanout.status()
        assert status["total_clients"] == 0
        assert not status["write_token_held"]

    @pytest.mark.anyio
    async def test_a_failing_reader_does_not_deadlock_stopping_it(self, running, monkeypatch):
        fanout = await running(StreamFanOut(source_factory=_idle_source, always_on=False))

        async def failing_reader():
            raise RuntimeError("source failed")

        monkeypatch.setattr(fanout, "_reader_loop", failing_reader)
        with anyio.fail_after(1):
            async with fanout._lock:
                await fanout._ensure_started()
                await anyio.sleep(0.05)
                await fanout._stop_reader()

        assert fanout._reader is None


class TestFanOutStreamMixin:
    @pytest.mark.anyio
    async def test_exported_close_kicks_on_loop_thread(self, running, monkeypatch):
        from dataclasses import dataclass

        _tx, rx = create_memory_object_stream[bytes](32)
        sink, _ = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(sink, rx)

        driver = TestDriver()
        fanout = await running(driver._get_fanout())
        loop_thread = threading.get_ident()
        original_kick = fanout.kick_sync

        def checked_kick():
            assert threading.get_ident() == loop_thread
            original_kick()

        monkeypatch.setattr(fanout, "kick_sync", checked_kick)
        async with fanout.attach_exclusive() as stream:
            await run_sync_in_worker(driver.close)
            with pytest.raises(ClosedResourceError):
                await stream.receive()

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_connect_and_observe(self, running):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = await running(driver._get_fanout())

        async with fanout.attach_exclusive() as stream:
            await a_tx.send(b"mixin-data")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"mixin-data" in data

            async with fanout.attach_observer() as obs:
                # drain scrollback from "mixin-data"
                scrollback = await obs.receive()
                assert b"mixin-data" in scrollback
                await a_tx.send(b"obs-data")
                await anyio.sleep(0.05)
                assert b"obs-data" in await obs.receive()

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_release_console(self, running):
        from dataclasses import dataclass

        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = await running(driver._get_fanout())

        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None
            await fanout.release_write_token()
            assert fanout._write_token_holder is None

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_console_status(self):
        from dataclasses import dataclass

        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        status = driver._get_fanout().status()
        assert "write_token_held" in status
        assert "observer_count" in status

    def test_mixin_get_fanout_assert(self):
        from dataclasses import dataclass

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield  # pragma: no cover

        driver = TestDriver()
        driver._fanout = None
        with pytest.raises(AssertionError, match="__post_init__"):
            driver._get_fanout()

    def test_mixin_close_calls_super(self):
        from dataclasses import dataclass

        close_called = False

        class Base:
            def close(self):
                nonlocal close_called
                close_called = True

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin, Base):
            @asynccontextmanager
            async def _open_source(self):
                yield  # pragma: no cover

        driver = TestDriver()
        driver.close()
        assert close_called

    def test_mixin_shutdown_stops_fanout_and_calls_super(self):
        from dataclasses import dataclass

        shutdown_called = False

        class Base:
            def shutdown(self):
                nonlocal shutdown_called
                shutdown_called = True

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin, Base):
            @asynccontextmanager
            async def _open_source(self):
                yield  # pragma: no cover

        driver = TestDriver()
        fanout = driver._get_fanout()
        driver.shutdown()

        assert fanout._shutdown
        assert shutdown_called

    @pytest.mark.anyio
    async def test_mixin_open_source_not_implemented(self):
        from dataclasses import dataclass

        @dataclass(kw_only=True)
        class BareDriver(FanOutStreamMixin):
            pass

        driver = BareDriver()
        with pytest.raises(NotImplementedError, match="subclass must implement"):
            async with driver._open_source():
                pass

    @pytest.mark.anyio
    async def test_mixin_connect_via_method(self, running):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        await running(driver._get_fanout())

        async with driver.connect() as stream:
            await a_tx.send(b"via-connect")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"via-connect" in data

        await driver._get_fanout().close()

    @pytest.mark.anyio
    async def test_mixin_observe_via_method(self, running):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        await running(driver._get_fanout())

        async with driver.observe() as stream:
            await a_tx.send(b"via-observe")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"via-observe" in data

        await driver._get_fanout().close()

    @pytest.mark.anyio
    async def test_mixin_release_console_via_method(self, running):
        from dataclasses import dataclass

        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = await running(driver._get_fanout())

        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None
            await driver.release_console()
            assert fanout._write_token_holder is None

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_console_status_via_method(self):
        from dataclasses import dataclass

        _a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, _b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        status = await driver.console_status()
        assert "write_token_held" in status
        assert "observer_count" in status
