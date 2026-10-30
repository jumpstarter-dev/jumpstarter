from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Any, ClassVar

import anyio
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
)
from anyio.abc import ObjectStream, TaskGroup
from anyio.from_thread import run_sync as run_sync_from_thread

from jumpstarter.common.exceptions import CONSOLE_IN_USE_MARKER, JumpstarterException
from jumpstarter.driver.decorators import export, exportstream

logger = logging.getLogger(__name__)

# Sentinel distinguishing "no default supplied" from an explicit default=None.
_UNSET = object()

# Max time an attach_*() call waits for the reader to open the source before
# giving up, so a hung source_factory can't wedge an attach (or the write token).
_SOURCE_READY_TIMEOUT = 30.0

# Max time to wait for the source to close when the reader stops, so a source
# whose close hangs can't wedge a detach or the end of a session.
_SOURCE_CLOSE_TIMEOUT = 5.0


class ExclusiveSessionActive(JumpstarterException):
    """Another client holds the write token.

    The exporter cannot see an authenticated client identity for a driver
    stream (the router stream carries no principal), so the holder is not
    named here. Attributing the token to a specific client requires the
    controller to plumb the dialing client's name through Dial/Listen; until
    then the message is deliberately identity-free rather than misleading.
    """

    def __init__(self):
        # Prefix a stable machine marker so the CLI can recognize this rejection
        # by token (plus the FAILED_PRECONDITION gRPC code) rather than by the
        # human wording; the CLI strips the marker before display.
        super().__init__(f"{CONSOLE_IN_USE_MARKER} Console in use by another client. Use --observe or release-console.")


class WriteTokenRevokedError(JumpstarterException):
    """Write token was revoked via release_console."""


class BufferOverflowError(Exception):
    """Client buffer overflowed with on_overflow='error' policy."""

    def __init__(self, dropped_bytes: int = 0):
        self.dropped_bytes = dropped_bytes
        super().__init__(f"Buffer overflow: {dropped_bytes} bytes would be lost")


class ReadOnlyStreamError(JumpstarterException):
    """Write attempted on an observer stream."""


class ClientBuffer:
    """Byte-bounded ring buffer with async wakeup.

    Never blocks the pusher. Supports drop-oldest and error overflow policies.
    """

    def __init__(self, max_bytes: int = 65536, on_overflow: str = "drop"):
        self._buf: deque[bytes] = deque()
        self._max_bytes = max_bytes
        self._current_bytes = 0
        self._dropped_bytes = 0
        self._pending_drop_marker = False
        self._event = anyio.Event()
        self._closed = False
        self._close_error: Exception | None = None
        self.on_overflow = on_overflow

    def push(self, data: bytes) -> None:
        """Non-blocking push. Never stalls the caller."""
        if self._closed:
            return
        if self._current_bytes + len(data) > self._max_bytes:
            if self.on_overflow == "error":
                self._closed = True
                self._close_error = BufferOverflowError(self._current_bytes + len(data))
                self._event.set()
                return
            while self._current_bytes + len(data) > self._max_bytes and self._buf:
                dropped = self._buf.popleft()
                self._current_bytes -= len(dropped)
                self._dropped_bytes += len(dropped)
                self._pending_drop_marker = True
        self._buf.append(data)
        self._current_bytes += len(data)
        self._event.set()

    async def pull(self) -> bytes:
        """Async pull. Blocks until data available."""
        while not self._buf and not self._closed:
            self._event = anyio.Event()
            await self._event.wait()
        if self._closed and not self._buf:
            if self._close_error:
                raise self._close_error
            raise ClosedResourceError
        if self._pending_drop_marker and self._dropped_bytes > 0:
            marker = f"\r\n[{self._dropped_bytes} bytes dropped]\r\n".encode()
            self._dropped_bytes = 0
            self._pending_drop_marker = False
            data = self._buf.popleft()
            self._current_bytes -= len(data)
            return marker + data
        data = self._buf.popleft()
        self._current_bytes -= len(data)
        return data

    def close(self, error: Exception | None = None) -> None:
        """Signal no more data. Wakes any waiting pull()."""
        self._closed = True
        if error:
            self._close_error = error
        self._event.set()

    @property
    def closed(self) -> bool:
        return self._closed

    def prefill(self, chunks: list[bytes]) -> None:
        """Prefill buffer with scrollback data. Call before registering with fan-out."""
        for chunk in chunks:
            self._buf.append(chunk)
            self._current_bytes += len(chunk)
        if self._buf:
            self._event.set()




class _FanOutStreamBase:
    """Base for fan-out stream wrappers. Compatible with forward_stream/copy_stream."""

    def __init__(self, fanout: StreamFanOut, client_id: int, buffer: ClientBuffer):
        self._fanout = fanout
        self._id = client_id
        self._buffer = buffer
        self._closed = False

    async def receive(self) -> bytes:
        return await self._buffer.pull()

    async def send_eof(self) -> None:
        pass

    @property
    def extra_attributes(self) -> dict:
        return {}

    def extra(self, attribute: Any, default: Any = _UNSET) -> Any:
        from anyio import TypedAttributeLookupError
        attrs = self.extra_attributes
        if attribute in attrs:
            return attrs[attribute]()
        if default is not _UNSET:
            return default
        raise TypedAttributeLookupError()

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.receive()
        except (ClosedResourceError, BrokenResourceError):
            raise StopAsyncIteration from None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()  # ty: ignore[unresolved-attribute]


class ExclusiveStream(_FanOutStreamBase):
    """Read+write stream for the write token holder."""

    async def send(self, data: bytes) -> None:
        if self._id != self._fanout._write_token_holder:
            raise WriteTokenRevokedError("write token revoked")
        source = self._fanout._source
        if source is None:
            raise BrokenResourceError("source not connected")
        await source.send(data)

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._fanout._detach(self._id, release_token=True)


class ObserverStream(_FanOutStreamBase):
    """Read-only stream for observers."""

    async def send(self, data: bytes) -> None:
        raise ReadOnlyStreamError("observer stream is read-only")

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            await self._fanout._detach(self._id, release_token=False)


@dataclass(slots=True)
class _Reader:
    scope: anyio.CancelScope = field(default_factory=anyio.CancelScope)
    done: anyio.Event = field(default_factory=anyio.Event)


class StreamFanOut:
    """Fan-out a source stream to one exclusive writer and N observers.

    The source is opened via a factory and managed by the fan-out. A background
    reader loop reads from the source and distributes bytes to all attached
    clients. The reader runs in the task group of ``running()``, so clients can
    attach only inside that context. The reader loop never blocks — all clients use bounded buffers
    with configurable overflow policy.

    With always_on=True (default), the source stays open for the lifetime of
    the fan-out, reconnecting on error. With always_on=False, the source is
    opened on first attach and closed when the last client detaches.
    """

    def __init__(
        self,
        source_factory: Callable[[], AbstractAsyncContextManager[ObjectStream[bytes]]],
        scrollback_size: int = 65536,
        always_on: bool = True,
    ):
        self._source_factory = source_factory
        self._scrollback_max = scrollback_size
        self._always_on = always_on

        self._source: ObjectStream[bytes] | None = None
        self._clients: dict[int, ClientBuffer] = {}
        self._write_token_holder: int | None = None
        self._scrollback: deque[bytes] = deque()
        self._scrollback_bytes: int = 0
        self._lock = anyio.Lock()
        self._reader_running = False
        self._shutdown = False
        self._reader: _Reader | None = None
        self._task_group: TaskGroup | None = None
        self._loop_thread: int | None = None
        self._started = False
        self._source_ready = anyio.Event()
        self._next_id = 0

    def _new_client_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _scrollback_append(self, data: bytes) -> None:
        self._scrollback.append(data)
        self._scrollback_bytes += len(data)
        while self._scrollback_bytes > self._scrollback_max and self._scrollback:
            removed = self._scrollback.popleft()
            self._scrollback_bytes -= len(removed)

    def _scrollback_snapshot(self) -> list[bytes]:
        return list(self._scrollback)

    async def _reader_loop(self) -> None:
        """Read from source, broadcast to all clients. Reconnects on error."""
        backoff = 0.1
        max_backoff = 5.0
        first_connect = True

        while not self._shutdown:
            try:
                async with self._open_source() as source:
                    self._source = source
                    backoff = 0.1
                    if not first_connect:
                        self._broadcast_system(b"[reconnected]\r\n")
                    first_connect = False
                    self._reader_running = True
                    self._source_ready.set()

                    async for data in source:
                        if self._shutdown:
                            break
                        self._scrollback_append(data)
                        self._broadcast_data(data)
                    else:
                        self._source = None
                        self._reader_running = False
                        if self._shutdown:
                            break
                        logger.warning("Source ended cleanly, reconnecting in %.1fs", backoff)
                        await anyio.sleep(backoff)
                        backoff = min(backoff * 2, max_backoff)
                        continue
            except (OSError, BrokenResourceError, ClosedResourceError) as e:
                self._source = None
                self._reader_running = False
                if self._shutdown:
                    break
                logger.warning("Source disconnected (%s: %s), reconnecting in %.1fs", type(e).__name__, e, backoff)
                self._broadcast_system(f"[disconnected: {e}, reconnecting...]\r\n".encode())
                await anyio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            except Exception:
                self._source = None
                self._reader_running = False
                if self._shutdown:
                    break
                raise

        self._source = None
        self._reader_running = False

    @asynccontextmanager
    async def _open_source(self) -> AsyncGenerator[ObjectStream[bytes], None]:
        """Open the source and close it even while the reader is being cancelled."""
        stack = AsyncExitStack()
        source = await stack.enter_async_context(self._source_factory())
        try:
            yield source
        finally:
            with anyio.CancelScope(shield=True), anyio.move_on_after(_SOURCE_CLOSE_TIMEOUT):
                await stack.aclose()

    def _broadcast_data(self, data: bytes) -> None:
        """Push data to all clients, pruning any whose buffer has closed.

        push() never raises — a client with on_overflow='error' instead marks
        its buffer closed and stores the error for its puller. Iterate over a
        snapshot so pruning can mutate self._clients in the same pass.
        """
        disconnected = []
        for client_id, buf in list(self._clients.items()):
            buf.push(data)
            if buf.closed:
                disconnected.append(client_id)
        for client_id in disconnected:
            self._clients.pop(client_id, None)
            if client_id == self._write_token_holder:
                self._write_token_holder = None

    def _broadcast_system(self, msg: bytes) -> None:
        """Push a system message to all clients. Non-blocking."""
        for buf in self._clients.values():
            with suppress(Exception):
                buf.push(msg)

    async def _run_reader(self, reader: _Reader) -> None:
        try:
            with reader.scope:
                try:
                    await self._reader_loop()
                except Exception:
                    if not self._shutdown:
                        logger.exception("Reader loop failed unexpectedly")
                # If the reader stops while we are not deliberately shutting down
                # (e.g. _reader_loop raised a non-IO exception after the source was
                # ready), reset the start state so the next attach restarts the
                # reader instead of blocking on _source_ready until the timeout.
                # This stays inside reader.scope: _stop_reader holds _lock while it
                # waits for the reader, and must be able to cancel this lock wait.
                if not self._shutdown:
                    async with self._lock:
                        for buf in self._clients.values():
                            buf.close()
                        self._clients.clear()
                        self._write_token_holder = None
                        self._reset_reader_state()
        finally:
            reader.done.set()

    def _reset_reader_state(self) -> None:
        self._reader = None
        self._started = False
        self._reader_running = False
        self._source = None
        self._scrollback.clear()
        self._scrollback_bytes = 0
        self._source_ready = anyio.Event()

    async def _ensure_started(self) -> None:
        """Start the reader loop. Must hold _lock."""
        if self._started or self._shutdown:
            return
        if self._task_group is None:
            raise RuntimeError("StreamFanOut is not running; attach inside running()")
        self._started = True
        self._source_ready = anyio.Event()
        self._reader = _Reader()
        self._task_group.start_soon(self._run_reader, self._reader)

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[StreamFanOut, None]:
        """Run the reader loop for as long as this context is open.

        Leaving it boots every client and stops the reader, waiting for the
        source to close.
        """
        if self._task_group is not None:
            raise RuntimeError("StreamFanOut is already running")
        try:
            async with anyio.create_task_group() as tg:
                self._task_group, self._loop_thread = tg, threading.get_ident()
                try:
                    yield self
                finally:
                    self._task_group = self._loop_thread = None
                    self.kick_sync()
                    tg.cancel_scope.cancel()
        finally:
            self._reset_reader_state()

    async def _wait_source_ready(self, timeout: float = _SOURCE_READY_TIMEOUT) -> None:
        """Wait for the reader loop to open the source. Call after releasing _lock.

        Bounded so a source_factory that hangs on open can't wedge an attach
        call (and, for exclusive attach, the write token) forever. Raises
        TimeoutError on expiry; the caller must detach the pending client.
        """
        with anyio.move_on_after(timeout) as scope:
            await self._source_ready.wait()
        if scope.cancelled_caught:
            raise TimeoutError(f"source not ready within {timeout:.1f}s")

    @asynccontextmanager
    async def attach_exclusive(
        self,
        on_overflow: str = "drop",
        buffer_bytes: int = 65536,
    ) -> AsyncGenerator[ExclusiveStream, None]:
        """Attach with the write token. Yields an ExclusiveStream."""
        async with self._lock:
            if self._write_token_holder is not None:
                raise ExclusiveSessionActive()

            await self._ensure_started()

            client_id = self._new_client_id()
            buf = ClientBuffer(max_bytes=buffer_bytes, on_overflow=on_overflow)
            # New exclusive writer = new session. Clear stale scrollback so
            # observers who attach later see only output from this session.
            self._scrollback.clear()
            self._scrollback_bytes = 0
            self._clients[client_id] = buf
            self._write_token_holder = client_id

        try:
            await self._wait_source_ready()
            yield ExclusiveStream(self, client_id, buf)
        finally:
            with anyio.CancelScope(shield=True):
                await self._detach(client_id, release_token=True)

    @asynccontextmanager
    async def attach_observer(
        self,
        on_overflow: str = "drop",
        buffer_bytes: int = 65536,
    ) -> AsyncGenerator[ObserverStream, None]:
        """Attach as a read-only observer. Yields an ObserverStream."""
        async with self._lock:
            await self._ensure_started()

            client_id = self._new_client_id()
            buf = ClientBuffer(max_bytes=buffer_bytes, on_overflow=on_overflow)
            # Atomic: snapshot scrollback + register, under lock
            buf.prefill(self._scrollback_snapshot())
            self._clients[client_id] = buf

        try:
            await self._wait_source_ready()
            yield ObserverStream(self, client_id, buf)
        finally:
            with anyio.CancelScope(shield=True):
                await self._detach(client_id, release_token=False)

    async def _detach(self, client_id: int, release_token: bool) -> None:
        """Remove a client. Close source if last client and not always_on."""
        async with self._lock:
            self._clients.pop(client_id, None)
            if release_token and self._write_token_holder == client_id:
                self._write_token_holder = None
            if not self._clients and not self._always_on:
                await self._stop_reader()

    async def release_write_token(self) -> None:
        """Force-release the write token. Revoked holder gets error on next write."""
        async with self._lock:
            if self._write_token_holder is not None:
                holder_buf = self._clients.get(self._write_token_holder)
                self._write_token_holder = None
                if holder_buf:
                    holder_buf.push(b"\r\n[write token revoked]\r\n")

    async def _stop_reader(self) -> None:
        """Stop the reader loop and wait for it to finish."""
        self._shutdown = True
        reader = self._reader
        if reader is not None:
            reader.scope.cancel()
            with anyio.CancelScope(shield=True), anyio.move_on_after(_SOURCE_CLOSE_TIMEOUT):
                await reader.done.wait()
        self._reset_reader_state()
        self._shutdown = False

    def kick_sync(self) -> None:
        """Boot every attached client and release the write token.

        The reader keeps running through the kick itself. When always_on is
        false it stops once the kicked clients' attach contexts unwind and
        _detach finds no clients left (unless a new client attached first);
        closing a serial port may toggle DTR and reset the attached board.
        For full teardown use ``shutdown_sync`` (session end only).

        While the reader is active, run on its event-loop thread so these
        mutations cannot interleave with the reader loop.
        """
        for buf in self._clients.values():
            buf.close()
        self._clients.clear()
        self._write_token_holder = None

    def shutdown_sync(self) -> None:
        """Synchronous full teardown for use from sync close()/shutdown() methods.

        Stops the reader loop and closes every client buffer. Intended for
        session-end teardown, not for a client-callable kick (use kick_sync).

        Must run on the event-loop thread; the caller (exporter Session
        teardown) does. Inside a session the reader has already stopped and
        closed the source when running() exited; otherwise it is cancelled here
        but cannot be awaited from a sync method.
        """
        self._shutdown = True
        if self._reader is not None:
            self._reader.scope.cancel()
            self._reader = None
        for buf in self._clients.values():
            buf.close()
        self._clients.clear()
        self._write_token_holder = None
        self._started = False
        self._reader_running = False

    async def close(self) -> None:
        """Shut down everything."""
        async with self._lock:
            for buf in self._clients.values():
                buf.close()
            self._clients.clear()
            self._write_token_holder = None
            await self._stop_reader()

    def status(self) -> dict:
        """Return current fan-out status for diagnostics."""
        return {
            # The holder is not named: the router stream carries no client
            # principal (see ExclusiveSessionActive).
            "write_token_held": self._write_token_holder is not None,
            "observer_count": sum(
                1 for cid in self._clients if cid != self._write_token_holder
            ),
            "total_clients": len(self._clients),
            "reader_running": self._reader_running,
            "scrollback_bytes": self._scrollback_bytes,
        }


@dataclass(kw_only=True)
class FanOutStreamMixin:
    """Mixin for drivers with exclusive physical streams that support observe mode.

    Provides connect() (exclusive), observe() (read-only), and release_console()
    as exported driver methods. Subclasses override _open_source() to provide
    the physical stream factory.
    """

    _fanout_always_on: ClassVar[bool] = True
    _fanout_scrollback_size: ClassVar[int] = 65536
    _fanout: StreamFanOut | None = field(default=None, init=False, repr=False)

    @asynccontextmanager
    async def _open_source(self):  # type: ignore[override]
        """Override: open the physical resource and yield a bidirectional stream."""
        raise NotImplementedError("subclass must implement _open_source()")
        yield  # pragma: no cover — makes this a generator for @asynccontextmanager

    def _get_fanout_always_on(self) -> bool:
        """Return always_on setting. Subclasses with an instance field override this."""
        return self._fanout_always_on

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()  # ty: ignore[unresolved-attribute]
        self._fanout = StreamFanOut(
            source_factory=self._open_source,
            scrollback_size=self._fanout_scrollback_size,
            always_on=self._get_fanout_always_on(),
        )

    def _get_fanout(self) -> StreamFanOut:
        assert self._fanout is not None, "FanOutStreamMixin.__post_init__ was not called"
        return self._fanout

    @exportstream
    @asynccontextmanager
    async def connect(self):
        async with self._get_fanout().attach_exclusive(on_overflow="drop") as stream:
            yield stream

    @exportstream
    @asynccontextmanager
    async def observe(self):
        async with self._get_fanout().attach_observer(on_overflow="drop") as stream:
            yield stream

    @export
    async def release_console(self) -> None:
        """Force-release the write token."""
        await self._get_fanout().release_write_token()

    @export
    async def console_status(self) -> dict:
        """Return current console session status."""
        return self._get_fanout().status()

    def close(self):
        """Client-callable: boot every attached client and release the write token.

        When always_on is false the reader stops once the kicked clients unwind.
        Closing a serial port may toggle DTR and reset the attached board.
        Full teardown happens at session end via shutdown().
        """
        if self._fanout is not None:
            loop_thread = self._fanout._loop_thread
            if loop_thread is None or loop_thread == threading.get_ident():
                self._fanout.kick_sync()
            else:
                # Needs an AnyIO worker thread (DriverCall runs exported sync
                # methods in one); a plain threading.Thread has no loop to hop to.
                run_sync_from_thread(self._fanout.kick_sync)
        if hasattr(super(), "close"):
            super().close()  # ty: ignore[unresolved-attribute]

    def shutdown(self):
        """Session-end teardown: stop the reader loop and release the source.

        Invoked by the exporter Session when the driver tree is torn down, never
        by a remote client. Kept separate from close() so a client kick cannot
        latch _shutdown and permanently wedge the fan-out.
        """
        if self._fanout is not None:
            self._fanout.shutdown_sync()
        if hasattr(super(), "shutdown"):
            super().shutdown()  # ty: ignore[unresolved-attribute]
