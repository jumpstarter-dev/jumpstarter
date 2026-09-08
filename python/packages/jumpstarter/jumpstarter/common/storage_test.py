import bz2
import gzip
import lzma
import sys

import pytest

if sys.version_info >= (3, 14):
    from compression import zstd
else:
    from backports import zstd

from .storage import write_to_storage_device

pytestmark = pytest.mark.anyio


async def _chunks(data: bytes, chunk_size: int = 16):
    for i in range(0, len(data), chunk_size):
        yield data[i : i + chunk_size]


@pytest.mark.parametrize(
    "compress",
    [
        lambda data: data,
        gzip.compress,
        lambda data: lzma.compress(data, format=lzma.FORMAT_XZ),
        bz2.compress,
        zstd.compress,
    ],
    ids=["raw", "gzip", "xz", "bz2", "zstd"],
)
async def test_write_to_storage_device_auto_decompress(tmp_path, compress):
    original = b"jumpstarter" * 1024
    device = tmp_path / "device"
    # simulate a present storage device: wait_for_storage_device requires a nonzero size
    device.write_bytes(b"\x00" * len(original))

    await write_to_storage_device(device, _chunks(compress(original)), leeway=0)

    assert device.read_bytes() == original
