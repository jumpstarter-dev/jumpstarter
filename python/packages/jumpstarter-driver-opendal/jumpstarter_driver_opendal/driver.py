from __future__ import annotations

import hashlib
from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory, _TemporaryFileWrapper
from typing import Any
from uuid import UUID, uuid4

from anyio.streams.file import FileReadStream, FileWriteStream
from opendal import AsyncFile, AsyncOperator
from opendal import Metadata as OpendalMetadata
from pydantic import validate_call

from .adapter import AsyncFileStream
from .common import Capability, HashAlgo, Metadata, Mode, PresignedRequest
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Opendal(Driver):
    scheme: str
    kwargs: dict[str, str]
    remove_created_on_close: bool = False

    _operator: AsyncOperator = field(init=False)
    _fds: dict[UUID, AsyncFile] = field(init=False, default_factory=dict)
    _metadata: dict[UUID, OpendalMetadata] = field(init=False, default_factory=dict)

    # Track file/directory operations
    _created_files: set[str] = field(init=False, default_factory=set)
    _created_dirs: set[str] = field(init=False, default_factory=set)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.OpendalClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._operator = AsyncOperator(self.scheme, **self.kwargs)

    @export
    @validate_call(validate_return=True)
    async def open(self, /, path: str, mode: Mode) -> UUID:
        try:
            metadata = await self._operator.stat(path)
        except Exception:
            metadata = None

        file = await self._operator.open(path, mode)
        uuid = uuid4()

        self._metadata[uuid] = metadata
        self._fds[uuid] = file

        # Track file creation for any write mode (assume pre-existing files are remnants from failed cleanup)
        if mode in ("wb", "w", "ab", "a"):
            self._created_files.add(path)

        return uuid

    @export
    @validate_call(validate_return=True)
    async def file_read(self, /, fd: UUID, dst: Any) -> None:
        async with self.resource(dst) as res:
            stream = AsyncFileStream(file=self._fds[fd], metadata=self._metadata[fd])
            async for chunk in stream:
                await res.send(chunk)

    @export
    @validate_call(validate_return=True)
    async def file_write(self, /, fd: UUID, src: Any) -> None:
        async with self.resource(src) as res:
            stream = AsyncFileStream(file=self._fds[fd], metadata=self._metadata[fd])
            async for chunk in res:
                await stream.send(chunk)

    @export
    @validate_call(validate_return=True)
    async def file_seek(self, /, fd: UUID, pos: int, whence: int = 0) -> int:
        return await self._fds[fd].seek(pos, whence)

    @export
    @validate_call(validate_return=True)
    async def file_tell(self, /, fd: UUID) -> int:
        return await self._fds[fd].tell()

    @export
    @validate_call(validate_return=True)
    async def file_close(self, /, fd: UUID) -> None:
        await self._fds[fd].close()

    @export
    @validate_call(validate_return=True)
    async def file_closed(self, /, fd: UUID) -> bool:
        return await self._fds[fd].closed

    @export
    @validate_call(validate_return=True)
    async def file_readable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].readable()

    @export
    @validate_call(validate_return=True)
    async def file_seekable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].seekable()

    @export
    @validate_call(validate_return=True)
    async def file_writable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].writable()

    @export
    @validate_call(validate_return=True)
    async def stat(self, /, path: str) -> Metadata:
        return Metadata.model_validate(await self._operator.stat(path), from_attributes=True)

    @export
    @validate_call(validate_return=True)
    async def hash(self, /, path: str, algo: HashAlgo = "sha256") -> str:
        match algo:
            case "md5":
                m = hashlib.md5()
            case "sha256":
                m = hashlib.sha256()
        async with await self._operator.open(path, "rb") as f:
            while True:
                data = await f.read(size=65536)
                if len(data) == 0:
                    break
                m.update(data)

        return m.hexdigest()

    @export
    @validate_call(validate_return=True)
    async def copy(self, /, source: str, target: str):
        await self._operator.copy(source, target)

    @export
    @validate_call(validate_return=True)
    async def rename(self, /, source: str, target: str):
        await self._operator.rename(source, target)

    @export
    @validate_call(validate_return=True)
    async def remove_all(self, /, path: str):
        await self._operator.remove_all(path)

    @export
    @validate_call(validate_return=True)
    async def create_dir(self, /, path: str):
        await self._operator.create_dir(path)
        self._created_dirs.add(path)

    @export
    @validate_call(validate_return=True)
    async def delete(self, /, path: str):
        await self._operator.delete(path)

    @export
    @validate_call(validate_return=True)
    async def exists(self, /, path: str) -> bool:
        return await self._operator.exists(path)

    @export
    async def list(self, /, path: str) -> AsyncGenerator[str, None]:
        async for entry in await self._operator.list(path):
            yield entry.path

    @export
    async def scan(self, /, path: str) -> AsyncGenerator[str, None]:
        async for entry in await self._operator.scan(path):
            yield entry.path

    @export
    @validate_call(validate_return=True)
    async def presign_stat(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_stat(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def presign_read(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_read(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def presign_write(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_write(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def capability(self, /) -> Capability:
        return Capability.model_validate(self._operator.capability(), from_attributes=True)

    async def copy_exporter_file(self, /, source: Path, target: str):
        """Copy a file from the exporter to the target path.
        This function is intended to be used on the exporter side to copy files to the target path.
        """
        async with await AsyncOperator("fs", root=source.parent.as_posix()).open(source.name, "rb") as src:
            async with await self._operator.open(target, "wb") as dst:
                while True:
                    data = await src.read(size=65536)
                    if len(data) == 0:
                        break
                    await dst.write(bs=data)

        # Always track file creation (assume pre-existing files are just uncleaned remnants)
        self._created_files.add(target)

    @export
    async def get_created_files(self) -> list[str]:
        """Get list of files that have been created during this session."""
        return list(self._created_files)

    @export
    async def get_created_directories(self) -> list[str]:
        """Get list of directories that have been created during this session."""
        return list(self._created_dirs)

    @export
    async def get_all_created_resources(self) -> tuple[list[str], list[str]]:
        """Get all resources created during this session as a tuple: (created_directories, created_files)."""
        return (list(self._created_dirs), list(self._created_files))


    def close(self):
        """Close driver and report what was created."""
        if self._created_files or self._created_dirs:
            self.logger.debug(
                "OpenDAL session summary - Created files: %d, Created directories: %d",
                len(self._created_files),
                len(self._created_dirs)
            )
            if self._created_files:
                self.logger.debug("Created files: %s", sorted(self._created_files))
            if self._created_dirs:
                self.logger.debug("Created directories: %s", sorted(self._created_dirs))

        # Remove created resources if requested
        if self.remove_created_on_close:
            self._cleanup_created_resources()

        super().close()

    def _cleanup_created_resources(self):
        """Remove all created files and directories."""
        # Only support filesystem cleanup for now (scheme == "fs")
        if self.scheme != "fs":
            self.logger.warning(f"Cleanup not supported for scheme '{self.scheme}' - only 'fs' is supported")
            return

        import os
        import shutil

        # Get root path from kwargs
        root_path = self.kwargs.get("root", "/")

        # Remove files first
        for file_path in self._created_files:
            try:
                full_path = os.path.join(root_path, file_path.lstrip("/"))
                if os.path.exists(full_path) and os.path.isfile(full_path):
                    os.remove(full_path)
                    self.logger.debug(f"Removed created file: {file_path}")
                else:
                    self.logger.debug(f"File already removed or not found: {file_path}")
            except Exception as e:
                self.logger.error(f"Failed to remove file {file_path}: {e}")

        # Remove directories (in reverse order to handle nested dirs)
        for dir_path in sorted(self._created_dirs, reverse=True):
            try:
                full_path = os.path.join(root_path, dir_path.lstrip("/"))
                if os.path.exists(full_path) and os.path.isdir(full_path):
                    # Only remove if empty
                    try:
                        os.rmdir(full_path)
                        self.logger.debug(f"Removed created directory: {dir_path}")
                    except OSError:
                        # Directory not empty, try to remove recursively if it was created by us
                        shutil.rmtree(full_path)
                        self.logger.debug(f"Removed created directory tree: {dir_path}")
                else:
                    self.logger.debug(f"Directory already removed or not found: {dir_path}")
            except Exception as e:
                self.logger.error(f"Failed to remove directory {dir_path}: {e}")


class FlasherInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.FlasherClient"

    @abstractmethod
    def flash(self, source, target: str | None = None): ...

    @abstractmethod
    def dump(self, target, partition: str | None = None): ...


@dataclass
class MockFlasher(FlasherInterface, Driver):
    _tempdir: TemporaryDirectory = field(default_factory=TemporaryDirectory)

    def __path(self, partition: str | None = None) -> str:
        if partition is None:
            partition = "default"
        return str(Path(self._tempdir.name) / partition)

    @export
    async def flash(self, source, partition: str | None = None):
        async with await FileWriteStream.from_path(self.__path(partition)) as stream:
            async with self.resource(source) as res:
                async for chunk in res:
                    await stream.send(chunk)

    @export
    async def dump(self, target, partition: str | None = None):
        async with await FileReadStream.from_path(self.__path(partition)) as stream:
            async with self.resource(target) as res:
                async for chunk in stream:
                    await res.send(chunk)


class StorageMuxInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.StorageMuxClient"

    @abstractmethod
    async def host(self): ...

    @abstractmethod
    async def dut(self): ...

    @abstractmethod
    async def off(self): ...

    @abstractmethod
    async def write(self, src: str): ...

    @abstractmethod
    async def read(self, dst: str): ...


class StorageMuxFlasherInterface(StorageMuxInterface):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.StorageMuxFlasherClient"


@dataclass
class MockStorageMux(StorageMuxInterface, Driver):
    file: _TemporaryFileWrapper = field(default_factory=NamedTemporaryFile)

    @export
    async def host(self):
        pass

    @export
    async def dut(self):
        pass

    @export
    async def off(self):
        pass

    @export
    async def write(self, src: str):
        async with await FileWriteStream.from_path(self.file.name) as stream:
            async with self.resource(src) as res:
                async for chunk in res:
                    await stream.send(chunk)

    @export
    async def read(self, dst: str):
        async with await FileReadStream.from_path(self.file.name) as stream:
            async with self.resource(dst) as res:
                async for chunk in stream:
                    await res.send(chunk)


@dataclass
class MockStorageMuxFlasher(StorageMuxFlasherInterface, MockStorageMux):
    pass
