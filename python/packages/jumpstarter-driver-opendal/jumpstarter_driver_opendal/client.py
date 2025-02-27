from __future__ import annotations

from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from uuid import UUID

import asyncclick as click
from anyio import EndOfStream
from anyio.abc import ObjectStream
from opendal import Operator
from pydantic import ConfigDict, validate_call

from .adapter import OpendalAdapter
from .common import Capability, HashAlgo, Metadata, Mode, PathBuf, PresignedRequest
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class BytesIOStream(ObjectStream[bytes]):
    buf: BytesIO

    async def send(self, item: bytes):
        self.buf.write(item)

    async def receive(self) -> bytes:
        item = self.buf.read(65535)
        if len(item) == 0:
            raise EndOfStream
        return item

    async def send_eof(self):
        pass

    async def aclose(self):
        pass


@dataclass(kw_only=True)
class OpendalFile:
    """
    A file-like object representing a remote file
    """

    client: OpendalClient
    fd: UUID

    def __write(self, handle):
        return self.client.call("file_write", self.fd, handle)

    def __read(self, handle):
        return self.client.call("file_read", self.fd, handle)

    def __fs_operator_for_path(self, path: PathBuf) -> (PathBuf, Operator):
        return Path(path).resolve(), Operator("fs", root="/")

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def write_from_path(self, path: PathBuf, operator: Operator | None = None):
        """
        Write into remote file with content from local file
        """
        if operator is None:
            path, operator = self.__fs_operator_for_path(path)

        with OpendalAdapter(client=self.client, operator=operator, path=path) as handle:
            return self.__write(handle)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def read_into_path(self, path: PathBuf, operator: Operator | None = None):
        """
        Read content from remote file into local file
        """
        if operator is None:
            path, operator = self.__fs_operator_for_path(path)

        with OpendalAdapter(client=self.client, operator=operator, path=path, mode="wb") as handle:
            return self.__read(handle)

    @validate_call(validate_return=True)
    def write_bytes(self, data: bytes) -> None:
        buf = BytesIO(data)
        with self.client.portal.wrap_async_context_manager(BytesIOStream(buf=buf)) as stream:
            with self.client.portal.wrap_async_context_manager(self.client.resource_async(stream)) as handle:
                self.__write(handle)

    @validate_call(validate_return=True)
    def read_bytes(self) -> bytes:
        buf = BytesIO()
        with self.client.portal.wrap_async_context_manager(BytesIOStream(buf=buf)) as stream:
            with self.client.portal.wrap_async_context_manager(self.client.resource_async(stream)) as handle:
                self.__read(handle)
        return buf.getvalue()

    @validate_call(validate_return=True)
    def seek(self, pos: int, whence: int = 0) -> int:
        """
        Change the cursor position to the given byte offset.
        Offset is interpreted relative to the position indicated by whence.
        The default value for whence is SEEK_SET. Values for whence are:

            SEEK_SET or 0 – start of the file (the default); offset should be zero or positive

            SEEK_CUR or 1 – current cursor position; offset may be negative

            SEEK_END or 2 – end of the file; offset is usually negative

        Return the new cursor position
        """
        return self.client.call("file_seek", self.fd, pos, whence)

    @validate_call(validate_return=True)
    def tell(self) -> int:
        """
        Return the current cursor position
        """
        return self.client.call("file_tell", self.fd)

    @validate_call(validate_return=True)
    def close(self) -> None:
        """
        Close the file
        """
        return self.client.call("file_close", self.fd)

    @property
    @validate_call(validate_return=True)
    def closed(self) -> bool:
        """
        Check if the file is closed
        """
        return self.client.call("file_closed", self.fd)

    @validate_call(validate_return=True)
    def readable(self) -> bool:
        """
        Check if the file is readable
        """
        return self.client.call("file_readable", self.fd)

    @validate_call(validate_return=True)
    def seekable(self) -> bool:
        """
        Check if the file is seekable
        """
        return self.client.call("file_seekable", self.fd)

    @validate_call(validate_return=True)
    def writable(self) -> bool:
        """
        Check if the file is writable
        """
        return self.client.call("file_writable", self.fd)


class OpendalClient(DriverClient):
    @validate_call(validate_return=True)
    def write_bytes(self, /, path: PathBuf, data: bytes) -> None:
        with closing(self.open(path, "wb")) as f:
            f.write_bytes(data)

    @validate_call(validate_return=True)
    def read_bytes(self, /, path: PathBuf) -> bytes:
        with closing(self.open(path, "rb")) as f:
            return f.read_bytes()

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def write_from_path(self, dst: PathBuf, src: PathBuf, operator: Operator | None = None) -> None:
        with closing(self.open(dst, "wb")) as f:
            f.write_from_path(src, operator)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def read_into_path(self, src: PathBuf, dst: PathBuf, operator: Operator | None = None) -> None:
        with closing(self.open(src, "rb")) as f:
            f.read_into_path(dst, operator)

    @validate_call
    def open(self, /, path: PathBuf, mode: Mode) -> OpendalFile:
        """
        Open a file-like reader for the given path
        """
        return OpendalFile(client=self, fd=self.call("open", path, mode))

    @validate_call(validate_return=True)
    def stat(self, /, path: PathBuf) -> Metadata:
        """
        Get current path's metadata
        """
        return self.call("stat", path)

    @validate_call(validate_return=True)
    def hash(self, /, path: PathBuf, algo: HashAlgo = "sha256") -> str:
        """
        Get current path's hash
        """
        return self.call("hash", path, algo)

    @validate_call(validate_return=True)
    def copy(self, /, source: PathBuf, target: PathBuf):
        """
        Copy source to target
        """
        self.call("copy", source, target)

    @validate_call(validate_return=True)
    def rename(self, /, source: PathBuf, target: PathBuf):
        """
        Rename source to target
        """
        self.call("rename", source, target)

    @validate_call(validate_return=True)
    def remove_all(self, /, path: PathBuf):
        """
        Remove all file under path
        """
        self.call("remove_all", path)

    @validate_call(validate_return=True)
    def create_dir(self, /, path: PathBuf):
        """
        Create a dir at given path

        To indicate that a path is a directory, it is compulsory to include a trailing / in the path.

        Create on existing dir will succeed.
        Create dir is always recursive, works like mkdir -p.
        """
        self.call("create_dir", path)

    @validate_call(validate_return=True)
    def delete(self, /, path: PathBuf):
        """
        Delete given path

        Delete not existing error won't return errors
        """
        self.call("delete", path)

    @validate_call(validate_return=True)
    def exists(self, /, path: PathBuf) -> bool:
        """
        Check if given path exists
        """
        return self.call("exists", path)

    @validate_call
    def list(self, /, path: PathBuf) -> Generator[str, None, None]:
        """
        List files and directories under given path
        """
        yield from self.streamingcall("list", path)

    @validate_call
    def scan(self, /, path: PathBuf) -> Generator[str, None, None]:
        """
        List files and directories under given path recursively
        """
        yield from self.streamingcall("scan", path)

    @validate_call(validate_return=True)
    def presign_stat(self, /, path: PathBuf, expire_second: int) -> PresignedRequest:
        """
        Presign an operation for stat (HEAD) which expires after expire_second seconds
        """
        return self.call("presign_stat", path, expire_second)

    @validate_call(validate_return=True)
    def presign_read(self, /, path: PathBuf, expire_second: int) -> PresignedRequest:
        """
        Presign an operation for read (GET) which expires after expire_second seconds
        """
        return self.call("presign_read", path, expire_second)

    @validate_call(validate_return=True)
    def presign_write(self, /, path: PathBuf, expire_second: int) -> PresignedRequest:
        """
        Presign an operation for write (PUT) which expires after expire_second seconds
        """
        return self.call("presign_write", path, expire_second)

    @validate_call(validate_return=True)
    def capability(self, /) -> Capability:
        """
        Get capabilities of the underlying storage
        """
        return self.call("capability")


class StorageMuxClient(DriverClient):
    def host(self):
        """Connect storage to host"""
        return self.call("host")

    def dut(self):
        """Connect storage to dut"""
        return self.call("dut")

    def off(self):
        """Disconnect storage"""
        return self.call("off")

    def write(self, handle):
        return self.call("write", handle)

    def read(self, handle):
        return self.call("read", handle)

    def write_file(self, operator: Operator, path: str):
        with OpendalAdapter(client=self, operator=operator, path=path) as handle:
            return self.write(handle)

    def read_file(self, operator: Operator, path: str):
        with OpendalAdapter(client=self, operator=operator, path=path, mode="wb") as handle:
            return self.read(handle)

    def write_local_file(self, filepath):
        """Write a local file to the storage device"""
        absolute = Path(filepath).resolve()
        return self.write_file(operator=Operator("fs", root="/"), path=str(absolute))

    def read_local_file(self, filepath):
        """Read into a local file from the storage device"""
        absolute = Path(filepath).resolve()
        return self.read_file(operator=Operator("fs", root="/"), path=str(absolute))

    def cli(self):
        @click.group
        def base():
            """Generic storage mux"""
            pass

        @base.command()
        def host():
            """Connect storage to host"""
            self.host()

        @base.command()
        def dut():
            """Connect storage to dut"""
            self.dut()

        @base.command()
        def off():
            """Disconnect storage"""
            self.off()

        @base.command()
        @click.argument("file")
        def write_local_file(file):
            self.write_local_file(file)

        return base
