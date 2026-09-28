from contextlib import suppress
from types import SimpleNamespace

import pytest
from anyio import EndOfStream, connect_tcp, fail_after
from anyio.streams.buffered import BufferedByteReceiveStream

from . import tempfile


@pytest.mark.anyio
async def test_tcp_listener_roundtrip_half_close_and_cleanup():
    async def echo(stream):
        async with stream:
            with suppress(EndOfStream):
                while True:
                    await stream.send(await stream.receive())
            await stream.send(b"eof")

    with fail_after(5):
        async with tempfile.TemporaryTcpListener(echo) as address, await connect_tcp(*address) as stream:
            receiver = BufferedByteReceiveStream(stream)
            await stream.send(b"\x00\xffdata")
            assert await receiver.receive_exactly(6) == b"\x00\xffdata"
            await stream.send_eof()
            assert await receiver.receive_exactly(3) == b"eof"
            with pytest.raises(EndOfStream):
                await receiver.receive()
        with pytest.raises(OSError):
            await connect_tcp(*address)


@pytest.mark.parametrize(
    ("platform", "explicit", "expected"),
    [("win32", None, False), ("linux", None, True), ("linux", False, False), ("win32", True, True)],
)
@pytest.mark.anyio
async def test_tcp_listener_reuse_port_default_and_explicit_override(monkeypatch, platform, explicit, expected):
    create_listener = tempfile.create_tcp_listener
    requested = []

    async def capture_listener(**kwargs):
        requested.append(kwargs["reuse_port"])
        # Exercise a real listener without requiring this host to support SO_REUSEPORT.
        return await create_listener(**(kwargs | {"reuse_port": False}))

    async def handler(stream):
        await stream.aclose()

    monkeypatch.setattr(tempfile, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(tempfile, "create_tcp_listener", capture_listener)
    async with tempfile.TemporaryTcpListener(handler, reuse_port=explicit):
        assert requested == [expected]
