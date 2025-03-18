import errno
import os
from logging import Logger
from typing import Literal

from anyio import fail_after, sleep
from anyio.abc import AnyByteStream
from anyio.streams.file import FileReadStream, FileWriteStream


async def wait_for_storage_device(  # noqa: C901
    storage_device: os.PathLike,
    mode: Literal["wb", "rb"],
    timeout: int = 10,
    *,
    logger: Logger | None = None,
) -> os.PathLike:
    with fail_after(timeout):
        while True:
            # https://stackoverflow.com/a/2774125
            try:
                match mode:
                    case "wb":
                        fd = os.open(storage_device, os.O_WRONLY)
                    case "rb":
                        fd = os.open(storage_device, os.O_RDONLY)
                    case _:
                        raise ValueError("invalid mode: {}".format(mode))
                with os.fdopen(fd, mode):  # to prevent fd from leaking
                    if os.lseek(fd, 0, os.SEEK_END) > 0:
                        if logger:
                            logger.info("storage device {} is ready".format(storage_device))
                        break
                if logger:
                    logger.debug("waiting for storage device {} to have a nonzero size".format(storage_device))
            except FileNotFoundError:
                if logger:
                    logger.debug("waiting for storage device {} to appear".format(storage_device))
            except OSError as e:
                match e.errno:
                    case errno.ENOMEDIUM | errno.EIO:
                        if logger:
                            logger.debug("waiting for storage device {} to be ready".format(storage_device))
                    case _:
                        raise

            await sleep(1)

    return storage_device


async def write_to_storage_device(
    storage_device: os.PathLike,
    resource: AnyByteStream,
    timeout: int = 10,
    fsync_timeout: int = 900,
    leeway: int = 6,
    *,
    logger: Logger | None = None,
):
    path = await wait_for_storage_device(
        storage_device,
        mode="wb",
        timeout=timeout,
        logger=logger,
    )
    with os.fdopen(os.open(path, os.O_WRONLY), "wb") as file:
        async with FileWriteStream(file) as stream:
            total_bytes = 0
            next_print = 0
            async for chunk in resource:
                await stream.send(chunk)
                if logger:
                    total_bytes += len(chunk)
                    if total_bytes > next_print:
                        logger.info(
                            "written {} MB to storage device {}".format(
                                total_bytes / (1024 * 1024),
                                storage_device,
                            )
                        )
                        next_print += 50 * 1024 * 1024

            with fail_after(fsync_timeout):
                while True:
                    try:
                        if logger:
                            logger.info("fsyncing storage device {}, please wait".format(storage_device))
                        os.fsync(file.fileno())
                    except OSError as e:
                        if e.errno == errno.EIO:
                            await sleep(1)
                            continue
                        else:
                            raise
                    else:
                        break

            await sleep(leeway)


async def read_from_storage_device(
    storage_device: os.PathLike,
    resource: AnyByteStream,
    timeout: int = 10,
    *,
    logger: Logger | None = None,
):
    path = await wait_for_storage_device(
        storage_device,
        mode="rb",
        timeout=timeout,
        logger=logger,
    )
    with os.fdopen(os.open(path, os.O_RDONLY), "rb") as file:
        async with FileReadStream(file) as stream:
            total_bytes = 0
            next_print = 0
            async for chunk in stream:
                await resource.send(chunk)
                if logger:
                    total_bytes += len(chunk)
                    if total_bytes > next_print:
                        logger.info(
                            "read {} MB from storage device {}".format(
                                total_bytes / (1024 * 1024),
                                storage_device,
                            )
                        )
                        next_print += 50 * 1024 * 1024
