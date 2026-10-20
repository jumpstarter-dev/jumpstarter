import os
import struct

import pytest

from .image import raw_image

RAW, FILL, DONT_CARE, CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4


def _android_sparse(
    *chunks: tuple[int, int, bytes] | tuple[int, int, bytes, int], blocks: int | None = None, major: int = 1,
) -> bytes:
    """(type, blocks, data[, size]) chunks as an Android sparse image with 4 KiB blocks, as libsparse writes it.

    A chunk's size field is its header plus data unless given.
    """
    total = sum(chunk[1] for chunk in chunks) if blocks is None else blocks
    header = struct.pack("<IHHHHIIII", 0xED26FF3A, major, 0, 28, 12, 4096, total, len(chunks), 0)
    body = b"".join(
        struct.pack("<HHII", kind, 0, count, size[0] if size else 12 + len(data)) + data
        for kind, count, data, *size in chunks
    )
    return header + body


def _sparse_sample(dont_care_blocks: int) -> tuple[bytes, bytes]:
    """An Android sparse image with every chunk type, and the raw image it stands for."""
    data, tail = os.urandom(2 * 4096), os.urandom(4096)
    sparse = _android_sparse(
        (RAW, 2, data), (FILL, 3, b"\x01\x02\x03\x04"), (DONT_CARE, dont_care_blocks, b""),
        (FILL, 1024, bytes(4)), (CRC32, 0, b"\x12\x34\x56\x78"), (RAW, 1, tail),
    )
    return sparse, data + b"\x01\x02\x03\x04" * 3072 + bytes((dont_care_blocks + 1024) * 4096) + tail


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize("read_size", [1, 13])
@pytest.mark.parametrize("sparse", [True, False], ids=["android-sparse", "raw"])
async def test_any_stream_boundaries_give_the_whole_image(read_size, sparse, anyio_backend):
    # Where a stream is cut depends on the transport, which a serve() round trip
    # cannot choose; and a decompressor can hand over nothing for a while.
    source, image = _sparse_sample(dont_care_blocks=2) if sparse else (os.urandom(5000),) * 2

    async def reads():
        for start in range(0, len(source), read_size):
            yield b""
            yield source[start:start + read_size]

    expanded = bytearray()
    async for piece in raw_image(reads()):
        expanded += bytes(piece) if isinstance(piece, int) else piece
    assert expanded == image
