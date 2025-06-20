from io import BytesIO

import pytest
from anyio import EndOfStream, create_memory_object_stream
from anyio.streams.stapled import StapledObjectStream

from .encoding import compress_stream

pytestmark = pytest.mark.anyio


def create_buffer(size):
    tx, rx = create_memory_object_stream[bytes](size)
    return StapledObjectStream(tx, rx)


@pytest.mark.parametrize("compression", [None, "gzip", "xz", "bz2"])
async def test_compress_stream(compression):
    stream = compress_stream(create_buffer(128), compression)

    await stream.send(b"hello")
    await stream.send_eof()

    result = BytesIO()
    while True:
        try:
            result.write(await stream.receive())
        except EndOfStream:
            break
    assert result.getvalue() == b"hello"
