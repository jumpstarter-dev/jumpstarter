"""Partition images on their way into a CVD's image directory.

Staging a decompressed image next to the image it replaces, and installing
it. Nothing here knows about CVDs,
leases or health: the flasher decides when each step may run, and every
blocking step runs on the ``Worker`` it passes in.
"""

import os
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, BinaryIO

from anyio import CancelScope, to_thread


class ImageError(Exception):
    """An image that does not fit."""


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
    """Write the image in ``chunks`` (decompressed bytes) to ``staged``.

    Zero blocks become holes: super.img and userdata.img are multi-GiB images
    that are mostly holes, and writing them out would fill the volume. Stops
    before allocating more than ``budget`` bytes. On any failure ``staged`` is
    removed, after the write in progress has finished.
    """
    staged_file = _StagedFile(staged)
    try:
        out = await worker.run(staged_file.open)
        size = allocated = 0
        async for chunk in chunks:
            allocated += await worker.run(_write_sparse, out, chunk)
            size += len(chunk)
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
