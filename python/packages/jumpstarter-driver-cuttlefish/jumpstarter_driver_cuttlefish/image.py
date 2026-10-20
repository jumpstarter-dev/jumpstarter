"""Partition images on their way into a CVD's image directory.

Decoding (decompressed bytes in, a raw image out), staging the result next to
the image it replaces, and installing it. Nothing here knows about CVDs,
leases or health: the flasher decides when each step may run, and every
blocking step runs on the ``Worker`` it passes in.
"""

import os
import struct
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import aclosing
from pathlib import Path
from typing import Any, BinaryIO

from anyio import CancelScope, to_thread


class ImageError(Exception):
    """An image that cannot be decoded or does not fit."""


class Worker:
    """Runs one operation's blocking steps one at a time, in order, on its own thread.

    Waiting for a step can be cancelled, and gRPC cancels a call by cancelling
    its task, which an anyio shield does not hold off. The step still runs, and
    so does anything submitted after it: cleanup submitted from an exception
    handler runs after the write it cleans up, and ``close``'s last step after
    everything the operation started.
    """

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="cuttlefish-image")

    async def run(self, func: Callable[..., Any], *args) -> Any:
        return await to_thread.run_sync(self._executor.submit(func, *args).result)

    def submit(self, func: Callable[..., Any], *args) -> None:
        """Queue ``func`` without waiting; it runs even if the caller is cancelled."""
        self._executor.submit(func, *args)

    def close(self, last: Callable[[], None]) -> None:
        """Queue ``last`` behind every step submitted so far, and accept no more."""
        self._executor.submit(last)
        self._executor.shutdown(wait=False)

    async def drained(self) -> None:
        """Wait until every step, ``last`` included, has run; shielded from anyio cancellation."""
        with CancelScope(shield=True):
            await to_thread.run_sync(self._executor.shutdown)


# Granularity of hole detection. Decompressed chunks mix data and long zero
# runs, so a chunk is never all zeros as a whole.
SPARSE_BLOCK = 64 * 1024

# Android sparse images (libsparse v1.0), the format fastboot images and
# Android CI's super.img and userdata.img come in: a header, then chunks of
# raw blocks, a repeated 4-byte fill, don't-care blocks or a CRC32. A CVD
# reads its images as raw disks and cvd would expand them on the next boot,
# outside the volume budget, so they are expanded while flashing.
SPARSE_MAGIC = 0xED26FF3A
SPARSE_HEADER = struct.Struct("<IHHHHIIII")
SPARSE_CHUNK = struct.Struct("<HHII")
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
# Bytes after the chunk header for each type, as libsparse checks them; a raw
# chunk carries its blocks.
CHUNK_BODY = {CHUNK_FILL: 4, CHUNK_DONT_CARE: 0, CHUNK_CRC32: 4}
FILL_PIECE = 1024 * 1024


class _Pending:
    """Exact-size reads from an async stream of chunks of any size.

    An empty chunk is not the end of the stream: a decompressor can return
    nothing for a while (bz2 holds output back until it has a whole block, up
    to 900 kB of input). Only the iterator ending is.
    """

    def __init__(self, chunks: AsyncIterator[bytes]):
        self._chunks = chunks
        self._data = b""

    async def _more(self) -> bool:
        """Append the next chunk with data; False at the end of the stream."""
        async for chunk in self._chunks:
            if chunk:
                self._data += chunk
                return True
        return False

    async def peek(self, size: int) -> bytes:
        while len(self._data) < size and await self._more():
            pass
        return self._data[:size]

    async def read(self, size: int) -> bytes:
        data = await self.peek(size)
        if len(data) < size:
            raise ImageError("Android sparse image ends early")
        self._data = self._data[size:]
        return data

    async def take(self, limit: int | None = None) -> bytes:
        """Up to ``limit`` bytes of what has arrived; empty only at the end of the stream."""
        if not self._data and not await self._more():
            return b""
        if limit is None or limit >= len(self._data):
            piece, self._data = self._data, b""
        else:
            piece, self._data = self._data[:limit], self._data[limit:]
        return piece


async def raw_image(chunks: AsyncIterator[bytes]) -> AsyncGenerator[bytes | int, None]:
    """The raw image in ``chunks`` (decompressed bytes), as data and holes (yielded as their length).

    Android sparse images are expanded; anything else passes through as it is.
    """
    stream = _Pending(chunks)
    head = await stream.peek(SPARSE_HEADER.size)
    if int.from_bytes(head[:4], "little") != SPARSE_MAGIC:
        while piece := await stream.take():
            yield piece
        return
    async for piece in _expand_android_sparse(stream):
        yield piece


async def _expand_android_sparse(stream: _Pending) -> AsyncGenerator[bytes | int, None]:
    _, major, minor, header_size, chunk_header_size, block_size, blocks, chunk_count, _ = SPARSE_HEADER.unpack(
        await stream.read(SPARSE_HEADER.size)
    )
    if (major, header_size, chunk_header_size) != (1, SPARSE_HEADER.size, SPARSE_CHUNK.size) or block_size % 4:
        raise ImageError(
            f"unsupported Android sparse image: version {major}.{minor}, {header_size}/{chunk_header_size}-byte "
            f"headers, {block_size}-byte blocks"
        )
    covered = 0
    for _ in range(chunk_count):
        kind, _, chunk_blocks, size = SPARSE_CHUNK.unpack(await stream.read(SPARSE_CHUNK.size))
        length = chunk_blocks * block_size
        body = length if kind == CHUNK_RAW else CHUNK_BODY.get(kind)
        if body is None:
            raise ImageError(f"unknown Android sparse chunk type {kind:#x}")
        if size != SPARSE_CHUNK.size + body:
            raise ImageError(
                f"corrupt Android sparse chunk: type {kind:#x} with {chunk_blocks} blocks is "
                f"{SPARSE_CHUNK.size + body} bytes, not {size}"
            )
        async for piece in _sparse_chunk(stream, kind, length):
            yield piece
        if kind != CHUNK_CRC32:  # a checksum stands for no blocks, whatever it declares
            covered += chunk_blocks
    if covered != blocks:
        raise ImageError(f"Android sparse image has {blocks} blocks but its chunks cover {covered}")


async def _sparse_chunk(stream: _Pending, kind: int, length: int) -> AsyncGenerator[bytes | int, None]:
    """The ``length`` raw bytes a chunk of a known ``kind`` stands for."""
    if kind == CHUNK_RAW:
        while length:
            piece = await stream.take(length)
            if not piece:
                raise ImageError("Android sparse image ends early")
            length -= len(piece)
            yield piece
    elif kind == CHUNK_FILL:
        pattern = await stream.read(4)
        if pattern == bytes(4):
            yield length
        else:
            piece = pattern * (FILL_PIECE // 4)
            for start in range(0, length, FILL_PIECE):
                yield piece[: length - start]
    elif kind == CHUNK_DONT_CARE:
        yield length
    else:  # CHUNK_CRC32, not checked
        await stream.read(4)


def _write_sparse(out: BinaryIO, chunk: bytes) -> int:
    """Write ``chunk`` at the current position, seeking over all-zero blocks.

    Returns the number of bytes written, i.e. allocated on disk.
    """
    written = 0
    view = memoryview(chunk)
    for start in range(0, len(chunk), SPARSE_BLOCK):
        end = min(start + SPARSE_BLOCK, len(chunk))
        if chunk.count(0, start, end) == end - start:
            out.seek(end - start, os.SEEK_CUR)
        else:
            out.write(view[start:end])
            written += end - start
    return written


def _finish(out: BinaryIO, size: int) -> None:
    out.truncate(size)  # a trailing run of zeros stays a hole
    out.flush()
    os.fsync(out.fileno())
    out.close()


class _StagedFile:
    """The file being staged, opened and discarded on the worker.

    It records the file as the worker opens it: a caller cancelled while
    waiting for the open never sees the result, but the discard queued after
    it does.
    """

    def __init__(self, path: Path):
        self.path = path
        self.out: BinaryIO | None = None

    def open(self) -> BinaryIO:
        self.out = self.path.open("wb")
        return self.out

    def discard(self) -> None:
        if self.out:
            self.out.close()
        self.path.unlink(missing_ok=True)


def space_left(directory: Path, limit: int, margin: int) -> int:
    """Bytes a new image may allocate in ``directory``'s volume of ``limit`` bytes, keeping ``margin`` free.

    Counts allocated blocks, so holes are free, and the image being replaced
    counts as used: it keeps its blocks until the new one is installed.
    """
    used = 0
    for root, _, files in os.walk(directory):
        used += sum(os.lstat(os.path.join(root, name)).st_blocks * 512 for name in files)
    return max(0, limit - used - margin)


async def stage(chunks: AsyncIterator[bytes], staged: Path, budget: int | None, worker: Worker) -> None:
    """Write the raw image in ``chunks`` (decompressed bytes) to ``staged``.

    Zero blocks become holes: super.img and userdata.img are multi-GiB images
    that are mostly holes, and writing them out would fill the volume. Stops
    before allocating more than ``budget`` bytes. On any failure ``staged`` is
    removed, after the write in progress has finished.
    """
    staged_file = _StagedFile(staged)
    try:
        out = await worker.run(staged_file.open)
        size = allocated = 0
        async with aclosing(raw_image(chunks)) as pieces:
            async for piece in pieces:
                if isinstance(piece, int):  # a hole
                    await worker.run(out.seek, piece, os.SEEK_CUR)
                    size += piece
                    continue
                allocated += await worker.run(_write_sparse, out, piece)
                size += len(piece)
                if budget is not None and allocated > budget:
                    raise ImageError(f"{staged.stem} needs more than the {budget} bytes left in the image volume")
        await worker.run(_finish, out, size)
    except BaseException:
        worker.submit(staged_file.discard)
        raise


def install(staged: Path, image: Path) -> None:
    """Rename ``staged`` over ``image``."""
    # The CVD user, not the exporter, loads the image and may rewrite it in
    # place: cvd load pads vbmeta images to 64 KiB. The volume is the Pod's own.
    staged.chmod(0o666)
    staged.replace(image)
