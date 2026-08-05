from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, ClassVar

import anyio
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
)
from anyio.abc import ObjectStream

from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.driver.decorators import export, exportstream

logger = logging.getLogger(__name__)

# Sentinel distinguishing "no default supplied" from an explicit default=None.
_UNSET = object()

# Max time an attach_*() call waits for the reader to open the source before
# giving up, so a hung source_factory can't wedge an attach (or the write token).
_SOURCE_READY_TIMEOUT = 30.0


class ExclusiveSessionActive(JumpstarterException):
    """Another client holds the write token."""

    def __init__(self, holder_identity: str | None = None):
        self.holder_identity = holder_identity
        if holder_identity:
            message = f"Console in use by {holder_identity}. Use --observe or release-console."
        else:
            message = "Console in use. Use --observe or release-console."
        super().__init__(message)


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
        await self.aclose()


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


class StreamFanOut:
    """Fan-out a source stream to one exclusive writer and N observers.

    The source is opened via a factory and managed by the fan-out. A background
    reader loop reads from the source and distributes bytes to all attached
    clients. The reader loop never blocks — all clients use bounded buffers
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
        self._write_token_holder_identity: str | None = None
        self._scrollback: deque[bytes] = deque()
        self._scrollback_bytes: int = 0
        self._lock = anyio.Lock()
        self._reader_running = False
        self._shutdown = False
        self._reader_task: asyncio.Task[None] | None = None
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
                async with self._source_factory() as source:
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
                self._write_token_holder_identity = None

    def _broadcast_system(self, msg: bytes) -> None:
        """Push a system message to all clients. Non-blocking."""
        for buf in self._clients.values():
            try:
                buf.push(msg)
            except Exception:
                pass

    async def _run_reader(self) -> None:
        try:
            await self._reader_loop()
        except Exception:
            if not self._shutdown:
                logger.exception("Reader loop failed unexpectedly")

    async def _ensure_started(self) -> None:
        """Start the reader loop. Must hold _lock."""
        if self._started or self._shutdown:
            return
        self._started = True
        self._source_ready = anyio.Event()
        # Deliberately asyncio, not an anyio TaskGroup: the reader must outlive
        # the attach_*() call that starts it and survive across the many later
        # _detach() calls (in other tasks) that come and go. anyio has no
        # detached-spawn primitive — a TaskGroup must enter/exit in one task,
        # which would either tie the reader's lifetime to the first attacher or
        # reintroduce a cross-task cancel-scope violation. jumpstarter runs
        # exclusively on the asyncio backend, so create_task() is safe here.
        self._reader_task = asyncio.get_running_loop().create_task(self._run_reader())

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
        identity: str | None = None,
        on_overflow: str = "drop",
        buffer_bytes: int = 65536,
    ) -> AsyncIterator[ExclusiveStream]:
        """Attach with the write token. Yields an ExclusiveStream."""
        async with self._lock:
            if self._write_token_holder is not None:
                raise ExclusiveSessionActive(self._write_token_holder_identity)

            await self._ensure_started()

            client_id = self._new_client_id()
            buf = ClientBuffer(max_bytes=buffer_bytes, on_overflow=on_overflow)
            # Atomic: snapshot scrollback + register, under lock
            buf.prefill(self._scrollback_snapshot())
            self._clients[client_id] = buf
            self._write_token_holder = client_id
            self._write_token_holder_identity = identity

        try:
            await self._wait_source_ready()
        except TimeoutError:
            await self._detach(client_id, release_token=True)
            raise
        stream = ExclusiveStream(self, client_id, buf)
        try:
            yield stream
        finally:
            await self._detach(client_id, release_token=True)

    @asynccontextmanager
    async def attach_observer(
        self,
        on_overflow: str = "drop",
        buffer_bytes: int = 65536,
    ) -> AsyncIterator[ObserverStream]:
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
        except TimeoutError:
            await self._detach(client_id, release_token=False)
            raise
        stream = ObserverStream(self, client_id, buf)
        try:
            yield stream
        finally:
            await self._detach(client_id, release_token=False)

    async def _detach(self, client_id: int, release_token: bool) -> None:
        """Remove a client. Close source if last client and not always_on."""
        async with self._lock:
            self._clients.pop(client_id, None)
            if release_token and self._write_token_holder == client_id:
                self._write_token_holder = None
                self._write_token_holder_identity = None
            if not self._clients and not self._always_on:
                await self._stop_reader()

    async def release_write_token(self) -> None:
        """Force-release the write token. Revoked holder gets error on next write."""
        async with self._lock:
            if self._write_token_holder is not None:
                holder_buf = self._clients.get(self._write_token_holder)
                self._write_token_holder = None
                self._write_token_holder_identity = None
                if holder_buf:
                    holder_buf.push(b"\r\n[write token revoked]\r\n")

    async def _stop_reader(self) -> None:
        """Stop the reader loop and wait for it to finish."""
        self._shutdown = True
        task = self._reader_task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        self._started = False
        self._shutdown = False
        self._source_ready = anyio.Event()

    def shutdown_sync(self) -> None:
        """Synchronous shutdown for use from sync close() methods."""
        self._shutdown = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            self._reader_task = None
        for buf in self._clients.values():
            buf.close()
        self._clients.clear()
        self._write_token_holder = None
        self._write_token_holder_identity = None
        self._started = False

    async def close(self) -> None:
        """Shut down everything."""
        async with self._lock:
            for buf in self._clients.values():
                buf.close()
            self._clients.clear()
            self._write_token_holder = None
            self._write_token_holder_identity = None
            await self._stop_reader()

    def status(self) -> dict:
        """Return current fan-out status for diagnostics."""
        return {
            "write_token_holder": self._write_token_holder_identity,
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

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()
        self._fanout = StreamFanOut(
            source_factory=self._open_source,
            scrollback_size=self._fanout_scrollback_size,
            always_on=self._fanout_always_on,
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
        if self._fanout is not None:
            self._fanout.shutdown_sync()
        if hasattr(super(), "close"):
            super().close()
