import pytest
from anyio import BrokenResourceError, EndOfStream

from jumpstarter.streams.upload import create_upload_stream

pytestmark = pytest.mark.anyio


async def test_upload_ends_cleanly_after_send_eof():
    remote, local = create_upload_stream()
    async with remote, local:
        await remote.send(b"image")
        await remote.send_eof()
        assert [chunk async for chunk in local] == [b"image"]
        with pytest.raises(EndOfStream):
            await local.receive()


async def test_upload_closed_without_eof_raises_after_received_data():
    remote, local = create_upload_stream()
    async with local:
        await remote.send(b"partial")
        await remote.aclose()
        assert await local.receive() == b"partial"
        with pytest.raises(BrokenResourceError):
            await local.receive()


async def test_upload_closed_without_eof_fails_async_for():
    remote, local = create_upload_stream()
    received = []
    async with local:
        await remote.send(b"partial")
        await remote.aclose()
        with pytest.raises(BrokenResourceError):
            async for chunk in local:
                received.append(chunk)
    assert received == [b"partial"]


async def test_upload_consumer_can_reply():
    remote, local = create_upload_stream()
    async with remote, local:
        await local.send(b"reply")
        await local.send_eof()
        assert [chunk async for chunk in remote] == [b"reply"]
