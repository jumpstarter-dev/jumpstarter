"""Internal local IPC used by sessions and their child processes.

Windows gRPC supports Unix-domain sockets even though CPython/AnyIO do not.
The native package owns those sockets; this adapter only supplies AnyIO's byte
stream contract. Nonblocking native operations are retried when AnyIO reports
the socket ready. On Windows' proactor event loop, AnyIO waits for readiness in
one shared selector thread, so idle sockets cause no wakeups. Unix platforms
continue using AnyIO's own implementation.
"""

import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import quote

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    ResourceGuard,
    connect_unix,
    create_task_group,
    notify_closing,
    wait_readable,
    wait_writable,
)
from anyio.abc import ByteStream
from anyio.lowlevel import checkpoint


def local_socket_target(path: os.PathLike | str) -> str:
    """Format a gRPC UDS target without treating a Windows drive as an authority."""
    path = os.fspath(path)
    if sys.platform == "win32":
        return "unix:" + quote(path.replace("\\", "/"), safe="/:")
    return f"unix://{path}"


class _WindowsUnixStream(ByteStream):
    def __init__(self, socket):
        self._socket = socket
        # Used only for readiness waits; the native object owns the handle.
        self._fileno = socket.fileno()
        self._closed = False
        self._write_closed = False
        self._receive_guard = ResourceGuard("reading from")
        self._send_guard = ResourceGuard("writing to")

    def _check_open(self):
        if self._closed:
            raise ClosedResourceError

    async def receive(self, max_bytes: int = 65536) -> bytes:
        if max_bytes < 1:
            raise ValueError("max_bytes must be at least 1")
        with self._receive_guard:
            await checkpoint()
            while True:
                self._check_open()
                try:
                    data = self._socket.try_recv(max_bytes)
                except OSError as exc:
                    raise BrokenResourceError from exc
                if data is None:
                    await wait_readable(self._fileno)
                elif data:
                    return data
                else:
                    raise EndOfStream

    async def send(self, item: bytes) -> None:
        with self._send_guard:
            await checkpoint()
            self._check_open()
            if self._write_closed:
                raise ClosedResourceError
            data = memoryview(item)
            while data:
                self._check_open()
                try:
                    # Bound the copy into PyO3 and preserve unsent bytes after
                    # partial writes instead of assuming send writes everything.
                    sent = self._socket.try_send(bytes(data[:65536]))
                except OSError as exc:
                    raise BrokenResourceError from exc
                if sent is None:
                    await wait_writable(self._fileno)
                elif sent == 0:
                    raise BrokenResourceError
                else:
                    data = data[sent:]

    async def send_eof(self) -> None:
        with self._send_guard:
            await checkpoint()
            self._check_open()
            if not self._write_closed:
                try:
                    self._socket.shutdown_write()
                except OSError as exc:
                    raise BrokenResourceError from exc
                self._write_closed = True

    async def aclose(self) -> None:
        # Close before any checkpoint: cleanup must work in cancelled scopes.
        if not self._closed:
            self._closed = True
            # Wake pending readiness waits before the handle becomes invalid.
            notify_closing(self._fileno)
            self._socket.close()


@asynccontextmanager
async def windows_unix_listener(handler, path):
    from jumpstarter_core.local import UnixListener

    listener = UnixListener.bind(os.fspath(path))
    fileno = listener.fileno()

    async def handle(socket):
        async with _WindowsUnixStream(socket) as stream:
            await handler(stream)

    async def accept(group):
        while True:
            socket = listener.try_accept()
            if socket is None:
                await wait_readable(fileno)
                continue
            try:
                group.start_soon(handle, socket)
            except BaseException:
                socket.close()
                raise
            await checkpoint()

    try:
        async with create_task_group() as group:
            group.start_soon(accept, group)
            try:
                yield path
            finally:
                group.cancel_scope.cancel()
    finally:
        # The accept task has stopped waiting on the handle by now.
        listener.close()


async def connect_local_stream(path: os.PathLike | str) -> ByteStream:
    if sys.platform != "win32":
        return await connect_unix(path)

    from jumpstarter_core.local import UnixStream

    await checkpoint()
    socket = UnixStream.connect(os.fspath(path))
    try:
        while not socket.finish_connect():
            await wait_writable(socket.fileno())
        return _WindowsUnixStream(socket)
    except BaseException:
        socket.close()
        raise
