from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import anyio
import pytest
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    create_memory_object_stream,
    create_task_group,
)
from anyio.streams.stapled import StapledObjectStream

from .fanout import (
    BufferOverflowError,
    ClientBuffer,
    ExclusiveSessionActive,
    ExclusiveStream,
    FanOutStreamMixin,
    ReadOnlyStreamError,
    StreamFanOut,
    WriteTokenRevokedError,
)


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
    async def test_exclusive_basic(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            await a_tx.send(b"hello")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"hello" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_second_exclusive_raises(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive():
            with pytest.raises(ExclusiveSessionActive):
                async with fanout.attach_exclusive():
                    pass

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_receives_same_data(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as primary:
            async with fanout.attach_observer() as observer:
                await a_tx.send(b"shared data")
                await anyio.sleep(0.05)
                primary_data = await primary.receive()
                observer_data = await observer.receive()
                assert b"shared data" in primary_data
                assert b"shared data" in observer_data

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_send_raises(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive():
            async with fanout.attach_observer() as observer:
                with pytest.raises(ReadOnlyStreamError):
                    await observer.send(b"should fail")

        await fanout.close()

    @pytest.mark.anyio
    async def test_primary_close_frees_token(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=True)

        async with fanout.attach_exclusive():
            pass
        # token released, should be claimable again
        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_before_exclusive(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=True)

        async with fanout.attach_observer() as observer:
            await a_tx.send(b"before exclusive")
            await anyio.sleep(0.05)
            data = await observer.receive()
            assert b"before exclusive" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_release_write_token(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            await fanout.release_write_token()
            with pytest.raises(WriteTokenRevokedError):
                await stream.send(b"should fail")

        await fanout.close()

    @pytest.mark.anyio
    async def test_scrollback_replay(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, scrollback_size=4096, always_on=True)

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
    async def test_exclusive_session_active_identity(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive(identity="user-alice"):
            with pytest.raises(ExclusiveSessionActive, match="user-alice"):
                async with fanout.attach_exclusive():
                    pass

        await fanout.close()

    @pytest.mark.anyio
    async def test_status(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive(identity="tester"):
            async with fanout.attach_observer():
                status = fanout.status()
                assert status["write_token_holder"] == "tester"
                assert status["observer_count"] == 1
                assert status["total_clients"] == 2

        await fanout.close()

    @pytest.mark.anyio
    async def test_overflow_error_policy(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

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
    async def test_exclusive_send_when_source_none(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            fanout._source = None
            with pytest.raises(BrokenResourceError):
                await stream.send(b"no source")

        await fanout.close()

    @pytest.mark.anyio
    async def test_send_eof_is_noop(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            await stream.send_eof()

        await fanout.close()

    @pytest.mark.anyio
    async def test_extra_attributes_empty(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            assert stream.extra_attributes == {}
            assert stream.extra("missing", default="fallback") == "fallback"

        await fanout.close()

    @pytest.mark.anyio
    async def test_extra_raises_without_default(self):
        from anyio import TypedAttributeLookupError

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            with pytest.raises(TypedAttributeLookupError):
                stream.extra("missing")

        await fanout.close()

    @pytest.mark.anyio
    async def test_async_iteration(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            assert stream.__aiter__() is stream
            await a_tx.send(b"iter-data")
            await anyio.sleep(0.05)
            data = await stream.__anext__()
            assert b"iter-data" in data

        await fanout.close()

    @pytest.mark.anyio
    async def test_anext_raises_stop_on_close(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_observer() as stream:
            fanout._clients[stream._id].close()
            with pytest.raises(StopAsyncIteration):
                await stream.__anext__()

        await fanout.close()

    @pytest.mark.anyio
    async def test_context_manager_protocol(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            s = await stream.__aenter__()
            assert s is stream
            await stream.__aexit__(None, None, None)

        await fanout.close()

    @pytest.mark.anyio
    async def test_observer_aclose_idempotent(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive():
            async with fanout.attach_observer() as observer:
                await observer.aclose()
                await observer.aclose()

        await fanout.close()

    @pytest.mark.anyio
    async def test_scrollback_overflow_trims(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, scrollback_size=20, always_on=False)

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
        stream = ExclusiveStream(fanout=None, client_id=1, buffer=buf)
        original = type(stream).extra_attributes
        try:
            type(stream).extra_attributes = property(
                lambda self: {"peername": lambda: ("127.0.0.1", 8080)}
            )
            assert stream.extra("peername") == ("127.0.0.1", 8080)
        finally:
            type(stream).extra_attributes = original

    @pytest.mark.anyio
    async def test_exclusive_send_to_source(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @asynccontextmanager
        async def factory():
            yield StapledObjectStream(b_tx, a_rx)

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async with fanout.attach_exclusive() as stream:
            await stream.send(b"outbound")
            data = await b_rx.receive()
            assert data == b"outbound"

        await fanout.close()

    @pytest.mark.anyio
    async def test_reconnect_broadcasts_message(self):
        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)
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

        fanout = StreamFanOut(source_factory=factory, always_on=False)
        collected = bytearray()

        async with fanout.attach_observer() as obs:
            with anyio.fail_after(2.0):
                while b"reconnected" not in collected:
                    collected += await obs.receive()

        assert b"reconnected" in collected
        await fanout.close()

    @pytest.mark.anyio
    async def test_stop_reader_handles_task_exception(self):
        @asynccontextmanager
        async def factory():
            yield None

        fanout = StreamFanOut(source_factory=factory, always_on=False)

        async def _failing_reader():
            raise RuntimeError("boom")

        fanout._reader_task = asyncio.get_running_loop().create_task(_failing_reader())
        fanout._started = True
        await fanout._stop_reader()
        assert fanout._reader_task is None
        assert not fanout._started


class TestFanOutStreamMixin:
    @pytest.mark.anyio
    async def test_mixin_connect_and_observe(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = driver._get_fanout()

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
    async def test_mixin_release_console(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = driver._get_fanout()

        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None
            await fanout.release_write_token()
            assert fanout._write_token_holder is None

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_console_status(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        status = driver._get_fanout().status()
        assert "write_token_holder" in status
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
    async def test_mixin_connect_via_method(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()

        async with driver.connect() as stream:
            await a_tx.send(b"via-connect")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"via-connect" in data

        await driver._get_fanout().close()

    @pytest.mark.anyio
    async def test_mixin_observe_via_method(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()

        async with driver.observe() as stream:
            await a_tx.send(b"via-observe")
            await anyio.sleep(0.05)
            data = await stream.receive()
            assert b"via-observe" in data

        await driver._get_fanout().close()

    @pytest.mark.anyio
    async def test_mixin_release_console_via_method(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        fanout = driver._get_fanout()

        async with fanout.attach_exclusive():
            assert fanout._write_token_holder is not None
            await driver.release_console()
            assert fanout._write_token_holder is None

        await fanout.close()

    @pytest.mark.anyio
    async def test_mixin_console_status_via_method(self):
        from dataclasses import dataclass

        a_tx, a_rx = create_memory_object_stream[bytes](32)
        b_tx, b_rx = create_memory_object_stream[bytes](32)

        @dataclass(kw_only=True)
        class TestDriver(FanOutStreamMixin):
            @asynccontextmanager
            async def _open_source(self):
                yield StapledObjectStream(b_tx, a_rx)

        driver = TestDriver()
        status = await driver.console_status()
        assert "write_token_holder" in status
        assert "observer_count" in status
