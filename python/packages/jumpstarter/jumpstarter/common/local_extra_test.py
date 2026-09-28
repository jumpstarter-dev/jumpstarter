"""Exercise cancellation and data integrity at the native byte-stream boundary."""

import socket as stdlib_socket
import sys
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from anyio import (
    BusyResourceError,
    ClosedResourceError,
    Event,
    create_task_group,
    fail_after,
    move_on_after,
)
from anyio.from_thread import start_blocking_portal

from . import local


class PartialSocket:
    """A byte sink that occasionally cannot accept data or accepts only a prefix.

    Readiness waits use one end of a real socket pair, which is always writable.
    """

    def __init__(self):
        self.limits = deque([7, None, 100000, None, 3, 100000])
        self.written = bytearray()
        self.eof_count = 0
        self.close_count = 0
        self._ready, self._peer = stdlib_socket.socketpair()

    def fileno(self):
        return self._ready.fileno()

    def try_send(self, data):
        limit = self.limits.popleft() if self.limits else len(data)
        if limit is None:
            return None
        count = min(limit, len(data))
        self.written.extend(data[:count])
        return count

    def shutdown_write(self):
        self.eof_count += 1

    def close(self):
        self.close_count += 1
        self._ready.close()
        self._peer.close()


@pytest.mark.anyio
async def test_partial_writes_preserve_all_bytes_and_half_close_is_idempotent():
    socket = PartialSocket()
    payload = bytes(range(256)) * 513
    stream = local._WindowsUnixStream(socket)
    with fail_after(5):
        async with stream:
            await stream.send(payload)
            await stream.send_eof()
            await stream.send_eof()
            with pytest.raises(ClosedResourceError):
                await stream.send(b"after eof")
    await stream.aclose()
    assert socket.written == payload
    assert socket.eof_count == 1
    assert socket.close_count == 1


class ReceiveSocket:
    """Becomes readable through a real socket pair when the test delivers data."""

    def __init__(self):
        self.receiving = Event()
        self.pending = deque()
        self.closed = False
        self._ready, self._signal = stdlib_socket.socketpair()
        self._ready.setblocking(False)

    def fileno(self):
        return self._ready.fileno()

    def deliver(self, data):
        self.pending.append(data)
        self._signal.send(b"\0")

    def try_recv(self, size):
        self.receiving.set()
        if not self.pending:
            return None
        self._ready.recv(1)
        return self.pending.popleft()

    def close(self):
        self.closed = True
        self._ready.close()
        self._signal.close()


@pytest.mark.anyio
async def test_cancelled_receive_keeps_stream_open_and_can_be_retried():
    socket = ReceiveSocket()
    async with local._WindowsUnixStream(socket) as stream:
        with move_on_after(0.01) as scope:
            await stream.receive()
        assert scope.cancel_called
        assert not socket.closed
        socket.deliver(b"next read")
        with fail_after(1):
            assert await stream.receive() == b"next read"
    assert socket.closed


@pytest.mark.anyio
async def test_concurrent_receive_is_rejected_and_close_releases_waiting_reader():
    socket = ReceiveSocket()
    stream = local._WindowsUnixStream(socket)
    reader_closed = Event()

    async def read():
        with pytest.raises(ClosedResourceError):
            await stream.receive()
        reader_closed.set()

    with fail_after(5):
        async with create_task_group() as group:
            group.start_soon(read)
            await socket.receiving.wait()
            with pytest.raises(BusyResourceError):
                await stream.receive()
            await stream.aclose()
            await reader_closed.wait()
    assert socket.closed


@pytest.mark.anyio
async def test_cancelled_pending_connect_closes_native_socket(monkeypatch):
    # A listening socket never becomes writable, like a connection still pending.
    pending = stdlib_socket.create_server(("127.0.0.1", 0))
    socket = SimpleNamespace(finish_connect=lambda: False, fileno=pending.fileno, close_count=0)

    def close():
        socket.close_count += 1
        pending.close()

    socket.close = close
    monkeypatch.setattr(local, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setitem(
        sys.modules,
        "jumpstarter_core.local",
        SimpleNamespace(UnixStream=SimpleNamespace(connect=lambda path: socket)),
    )
    with move_on_after(0.01) as scope:
        await local.connect_local_stream("C:/private/socket")
    assert scope.cancel_called
    assert socket.close_count == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows gRPC drive paths need URI escaping")
def test_windows_grpc_session_accepts_unicode_space_and_percent_path(monkeypatch):
    power = pytest.importorskip("jumpstarter_driver_power.driver")
    from jumpstarter.client import client_from_path
    from jumpstarter.common import ExporterStatus
    from jumpstarter.exporter import Session

    with TemporaryDirectory(prefix="j-") as parent:
        base = Path(parent) / "\u00fc %"
        base.mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
        with start_blocking_portal() as portal, Session(root_device=power.MockPower()) as session:
            session.update_status(ExporterStatus.LEASE_READY)
            with portal.wrap_async_context_manager(session.serve_unix_async()) as path:
                assert path.parent.parent == base

                async def exercise():
                    with fail_after(10), ExitStack() as stack:
                        async with client_from_path(
                            path, portal, stack,
                            allow=["jumpstarter_driver_power.client.PowerClient"], unsafe=False,
                        ) as client:
                            await client.call_async("on")
                            assert [reading async for reading in client.streamingcall_async("read")]
                            await client.call_async("off")

                portal.call(exercise)
            assert not path.exists()
            assert not path.parent.exists()
