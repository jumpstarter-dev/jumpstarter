import sys
import time
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from anyio import (
    ClosedResourceError,
    EndOfStream,
    Event,
    create_task_group,
    fail_after,
    move_on_after,
    sleep_forever,
)
from anyio.streams.buffered import BufferedByteReceiveStream

from . import local
from .tempfile import TemporarySocket, TemporaryUnixListener


@pytest.mark.parametrize(
    ("platform", "path", "target"),
    [
        ("linux", "/tmp/jumpstarter/socket", "unix:///tmp/jumpstarter/socket"),
        ("win32", "C:\\Users\\name\\socket", "unix:C:/Users/name/socket"),
        ("win32", "C:/space %/ü/socket", "unix:C:/space%20%25/%C3%BC/socket"),
    ],
)
def test_local_socket_target(monkeypatch, platform, path, target):
    monkeypatch.setattr(local, "sys", SimpleNamespace(platform=platform))
    assert local.local_socket_target(path) == target


@pytest.mark.anyio
async def test_local_stream_concurrent_binary_roundtrips_and_half_close():
    async def echo(stream):
        async with stream:
            with suppress(EndOfStream):
                while True:
                    await stream.send(await stream.receive(4096))
            await stream.send(b"after-eof")
            await stream.send_eof()

    async def client(path, number):
        data = bytes(range(256)) * 257 + bytes([number])
        async with await local.connect_local_stream(path) as stream:
            receiver = BufferedByteReceiveStream(stream)

            async def write():
                await stream.send(data)
                await stream.send_eof()

            async with create_task_group() as group:
                group.start_soon(write)
                assert await receiver.receive_exactly(len(data)) == data
                assert await receiver.receive_exactly(9) == b"after-eof"
                with pytest.raises(EndOfStream):
                    await stream.receive()

    with fail_after(10):
        async with TemporaryUnixListener(echo) as path, create_task_group() as group:
            for number in range(8):
                group.start_soon(client, path, number)
        assert not Path(path).exists()
        assert not Path(path).parent.exists()
        with pytest.raises(OSError):
            await local.connect_local_stream(path)


@pytest.mark.anyio
async def test_idle_listener_and_connected_receive_cancel_and_cleanup():
    entered = Event()
    closed = Event()

    async def idle(stream):
        try:
            async with stream:
                entered.set()
                await sleep_forever()
        finally:
            closed.set()

    with fail_after(5):
        async with TemporaryUnixListener(idle) as path:
            stream = await local.connect_local_stream(path)
            try:
                await entered.wait()
                started = time.monotonic()
                with move_on_after(0.05) as scope:
                    await stream.receive()
                assert scope.cancel_called
                assert time.monotonic() - started < 1
            finally:
                await stream.aclose()
            with pytest.raises(ClosedResourceError):
                await stream.receive()
        assert closed.is_set()
        assert not Path(path).exists()

        with move_on_after(0.05) as scope:
            async with TemporaryUnixListener(idle) as idle_path:
                await sleep_forever()
        assert scope.cancel_called
        assert not Path(idle_path).parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native socket readiness")
@pytest.mark.anyio
async def test_idle_windows_stream_waits_for_readiness_instead_of_polling():
    async def idle(stream):
        async with stream:
            await sleep_forever()

    with fail_after(5):
        async with TemporaryUnixListener(idle) as path:
            stream = await local.connect_local_stream(path)
            native = stream._socket
            attempts = []

            class CountingSocket:
                def try_recv(self, size):
                    attempts.append(size)
                    return native.try_recv(size)

                def __getattr__(self, name):
                    return getattr(native, name)

            stream._socket = CountingSocket()
            try:
                with move_on_after(0.3):
                    await stream.receive()
            finally:
                await stream.aclose()
    # One attempt, then a readiness wait; a polling loop would retry every few ms.
    assert len(attempts) == 1


@pytest.mark.anyio
async def test_local_stream_backpressure_can_be_cancelled():
    async def no_reader(stream):
        async with stream:
            await sleep_forever()

    with fail_after(5):
        async with TemporaryUnixListener(no_reader) as path, await local.connect_local_stream(path) as stream:
            with move_on_after(0.05) as scope:
                await stream.send(b"x" * (16 * 1024 * 1024))
            assert scope.cancel_called
        assert not Path(path).parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native directory contract")
def test_windows_socket_directory_respects_runtime_dir_and_cleans_unicode_path(monkeypatch):
    # pytest's descriptive per-test directory can itself exceed sun_path.
    with TemporaryDirectory(prefix="j-") as parent:
        base = Path(parent) / "ü %"
        base.mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
        with TemporarySocket() as path:
            assert path.parent.parent == base
            assert len(str(path).encode("utf-8")) < 108
            assert path.parent.is_dir()
        assert not path.parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native pathname limit")
def test_windows_socket_directory_handles_overlong_runtime_dir_without_orphans(tmp_path, monkeypatch):
    base = tmp_path / ("x" * 120)
    base.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(base))
    with pytest.raises(OSError, match="maximum 107"):
        with TemporarySocket():
            pass
    assert not list(base.iterdir())
