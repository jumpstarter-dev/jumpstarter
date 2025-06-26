from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections.abc import Generator
from contextlib import closing
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from uuid import UUID

import click
from anyio import EndOfStream
from anyio.abc import ObjectStream
from opendal import Operator
from pydantic import ConfigDict, validate_call

from .adapter import OpendalAdapter
from .common import Capability, HashAlgo, Metadata, Mode, PathBuf, PresignedRequest
from jumpstarter.client import DriverClient
from jumpstarter.common.exceptions import ArgumentError
from jumpstarter.streams.encoding import Compression


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


def operator_for_path(path: PathBuf) -> tuple[PathBuf, Operator, str]:
    """Create an operator for the given path
    Return a tuple of:
        - the path
        - the operator for the given path
        - the scheme of the operator.
    """
    if type(path) is str and path.startswith(("http://", "https://")):
        parsed_url = urlparse(path)
        operator = Operator("http", root="/", endpoint=f"{parsed_url.scheme}://{parsed_url.netloc}")
        return Path(parsed_url.path), operator, "http"
    else:
        return Path(path).resolve(), Operator("fs", root="/"), "fs"


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

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def write_from_path(self, path: PathBuf, operator: Operator | None = None):
        """
        Write into remote file with content from local file
        """
        if operator is None:
            path, operator, _ = operator_for_path(path)

        with OpendalAdapter(client=self.client, operator=operator, path=path) as handle:
            return self.__write(handle)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def read_into_path(self, path: PathBuf, operator: Operator | None = None):
        """
        Read content from remote file into local file
        """
        if operator is None:
            path, operator, _ = operator_for_path(path)

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
        """
        Write data into path

        >>> opendal.write_bytes("file.txt", b"content")
        """
        with closing(self.open(path, "wb")) as f:
            f.write_bytes(data)

    @validate_call(validate_return=True)
    def read_bytes(self, /, path: PathBuf) -> bytes:
        """
        Read data from path

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.read_bytes("file.txt")
        b'content'
        """
        with closing(self.open(path, "rb")) as f:
            return f.read_bytes()

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def write_from_path(self, dst: PathBuf, src: PathBuf, operator: Operator | None = None) -> None:
        """
        Write data from src into dst

        >>> _ = (tmp / "src").write_bytes(b"content")
        >>> opendal.write_from_path("file.txt", tmp / "src")
        >>> opendal.read_bytes("file.txt")
        b'content'
        """
        with closing(self.open(dst, "wb")) as f:
            f.write_from_path(src, operator)

    @validate_call(validate_return=True, config=ConfigDict(arbitrary_types_allowed=True))
    def read_into_path(self, src: PathBuf, dst: PathBuf, operator: Operator | None = None) -> None:
        """
        Read data into dst from src

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.read_into_path("file.txt", tmp / "dst")
        >>> (tmp / "dst").read_bytes()
        b'content'
        """
        with closing(self.open(src, "rb")) as f:
            f.read_into_path(dst, operator)

    @validate_call
    def open(self, /, path: PathBuf, mode: Mode) -> OpendalFile:
        """
        Open a file-like reader for the given path

        >>> file = opendal.open("file.txt", "wb")
        >>> file.write_bytes(b"content")
        >>> file.close()
        """
        return OpendalFile(client=self, fd=self.call("open", path, mode))

    @validate_call(validate_return=True)
    def stat(self, /, path: PathBuf) -> Metadata:
        """
        Get current path's metadata

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.stat("file.txt").mode.is_file()
        True
        """
        return self.call("stat", path)

    @validate_call(validate_return=True)
    def hash(self, /, path: PathBuf, algo: HashAlgo = "sha256") -> str:
        """
        Get current path's hash

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.hash("file.txt")
        'ed7002b439e9ac845f22357d822bac1444730fbdb6016d3ec9432297b9ec9f73'
        """
        return self.call("hash", path, algo)

    @validate_call(validate_return=True)
    def copy(self, /, source: PathBuf, target: PathBuf):
        """
        Copy source to target

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.copy("file.txt", "copy.txt")
        >>> opendal.exists("copy.txt")
        True
        """
        self.call("copy", source, target)

    @validate_call(validate_return=True)
    def rename(self, /, source: PathBuf, target: PathBuf):
        """
        Rename source to target

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.rename("file.txt", "rename.txt")
        >>> opendal.exists("file.txt")
        False
        >>> opendal.exists("rename.txt")
        True
        """
        self.call("rename", source, target)

    @validate_call(validate_return=True)
    def remove_all(self, /, path: PathBuf):
        """
        Remove all file under path

        >>> opendal.write_bytes("dir/file.txt", b"content")
        >>> opendal.remove_all("dir/")
        >>> opendal.exists("dir/file.txt")
        False
        """
        self.call("remove_all", path)

    @validate_call(validate_return=True)
    def create_dir(self, /, path: PathBuf):
        """
        Create a dir at given path

        To indicate that a path is a directory, it is compulsory to include a trailing / in the path.

        Create on existing dir will succeed.
        Create dir is always recursive, works like mkdir -p.

        >>> opendal.create_dir("a/b/c/")
        >>> opendal.exists("a/b/c/")
        True
        """
        self.call("create_dir", path)

    @validate_call(validate_return=True)
    def delete(self, /, path: PathBuf):
        """
        Delete given path

        Delete not existing error won't return errors

        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.exists("file.txt")
        True
        >>> opendal.delete("file.txt")
        >>> opendal.exists("file.txt")
        False
        """
        self.call("delete", path)

    @validate_call(validate_return=True)
    def exists(self, /, path: PathBuf) -> bool:
        """
        Check if given path exists

        >>> opendal.exists("file.txt")
        False
        >>> opendal.write_bytes("file.txt", b"content")
        >>> opendal.exists("file.txt")
        True
        """
        return self.call("exists", path)

    @validate_call
    def list(self, /, path: PathBuf) -> Generator[str, None, None]:
        """
        List files and directories under given path

        >>> opendal.write_bytes("dir/file.txt", b"content")
        >>> opendal.write_bytes("dir/another.txt", b"content")
        >>> sorted(opendal.list("dir/"))
        ['dir/', 'dir/another.txt', 'dir/file.txt']
        """
        yield from self.streamingcall("list", path)

    @validate_call
    def scan(self, /, path: PathBuf) -> Generator[str, None, None]:
        """
        List files and directories under given path recursively

        >>> opendal.write_bytes("dir/a/file.txt", b"content")
        >>> opendal.write_bytes("dir/b/another.txt", b"content")
        >>> sorted(opendal.scan("dir/"))
        ['dir/', 'dir/a/', 'dir/a/file.txt', 'dir/b/', 'dir/b/another.txt']
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

        >>> cap = opendal.capability()
        >>> cap.copy
        True
        >>> cap.presign_read
        False
        """
        return self.call("capability")

    def cli(self):  # noqa: C901
        arg_path = click.argument("path", type=click.Path())
        arg_source = click.argument("source", type=click.Path())
        arg_target = click.argument("target", type=click.Path())
        arg_src = click.argument("src", type=click.Path())
        arg_dst = click.argument("dst", type=click.Path())
        opt_expire_second = click.option("--expire-second", type=int, required=True)

        @click.group
        def base():
            """Opendal Storage"""

        @base.command
        @arg_path
        def write_bytes(path):
            data = click.get_binary_stream("stdin").read()
            self.write_bytes(path, data)

        @base.command
        @arg_path
        def read_bytes(path):
            data = self.read_bytes(path)
            click.echo(data, nl=False)

        @base.command
        @arg_dst
        @arg_src
        def write_from_path(dst, src):
            self.write_from_path(dst, src)

        @base.command
        @arg_src
        @arg_dst
        def read_into_path(src, dst):
            self.read_into_path(src, dst)

        @base.command
        @arg_path
        def stat(path):
            click.echo(self.stat(path).model_dump_json(indent=2, by_alias=True))

        @base.command
        @arg_path
        @click.option("--algo", type=click.Choice(["md5", "sha256"]))
        def hash(path, algo):
            click.echo(self.hash(path, algo))

        @base.command
        @arg_source
        @arg_target
        def copy(source, target):
            self.copy(source, target)

        @base.command
        @arg_source
        @arg_target
        def rename(source, target):
            self.rename(source, target)

        @base.command
        @arg_path
        def remove_all(path):
            self.remove_all(path)

        @base.command
        @arg_path
        def create_dir(path):
            self.create_dir(path)

        @base.command
        @arg_path
        def delete(path):
            self.delete(path)

        @base.command
        @arg_path
        def exists(path):
            if not self.exists(path):
                raise click.ClickException(f"path {path} does not exist")

        @base.command
        @arg_path
        def list(path):
            for entry in self.list(path):
                click.echo(entry)

        @base.command
        @arg_path
        def scan(path):
            for entry in self.scan(path):
                click.echo(entry)

        @base.command
        @arg_path
        @opt_expire_second
        def presign_stat(path, expire_second):
            click.echo(self.presign_stat(path, expire_second).model_dump_json(indent=2))

        @base.command
        @arg_path
        @opt_expire_second
        def presign_read(path, expire_second):
            click.echo(self.presign_read(path, expire_second).model_dump_json(indent=2))

        @base.command
        @arg_path
        @opt_expire_second
        def presign_write(path, expire_second):
            click.echo(self.presign_write(path, expire_second).model_dump_json(indent=2))

        @base.command
        def capability():
            click.echo(self.capability().model_dump_json(indent=2))

        return base


class FlasherClientInterface(metaclass=ABCMeta):
    @abstractmethod
    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        partition: str | None = None,
        operator: Operator | dict[str, Operator] | None = None,
        compression: Compression | dict[str, Compression] | None = None,
    ):
        """Flash image to DUT"""
        ...

    @abstractmethod
    def dump(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        compression: Compression | None = None,
    ):
        """Dump image from DUT"""
        ...

    def cli(self):
        @click.group
        def base():
            """Generic flasher interface"""
            pass

        @base.command()
        @click.argument("file", nargs=-1, required=False)
        @click.option(
            "--partition",
            "-p",
            "partition_specs",
            multiple=True,
            help="name:file",
        )
        @click.option("--single-partition", type=str, help="Partition name when flashing a single file")
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def flash(file, partition_specs, single_partition, compression):
            if partition_specs:
                mapping: dict[str, str] = {}
                for spec in partition_specs:
                    if ":" not in spec:
                        raise click.ClickException(f"Invalid partition spec '{spec}', expected name:file")
                    name, img = spec.split(":", 1)
                    mapping[name] = img
                self.flash(cast(dict[str, PathBuf], mapping), compression=compression)
                return

            if not file:
                raise click.ClickException("FILE argument is required unless --partition/-p is used")

            self.flash(file[0], partition=single_partition, compression=compression)

        @base.command()
        @click.argument("file")
        @click.option("--partition", type=str)
        @click.option("--compression", type=click.Choice(Compression, case_sensitive=False))
        def dump(file, partition, compression):
            """Dump image from DUT to file"""
            self.dump(file, partition=partition, compression=compression)

        return base


class FlasherClient(FlasherClientInterface, DriverClient):
    def _should_upload_file(self, storage, filename: str, src_path: PathBuf, src_operator: Operator) -> bool:
        """Check if file should be uploaded by comparing existence and hash."""
        if not storage.exists(filename):
            return True

        try:
            import hashlib

            m = hashlib.sha256()
            with src_operator.open(src_path, "rb") as f:
                while True:
                    data = f.read(size=65536)
                    if len(data) == 0:
                        break
                    m.update(data)
            src_hash = m.hexdigest()

            storage_hash = storage.hash(filename)

            if storage_hash == src_hash:
                return False
            else:
                return True
        except Exception:
            return True

    def _flash_single(
        self,
        image: PathBuf,
        *,
        partition: str | None,
        operator: Operator | None,
        compression: Compression | None,
    ):
        """Flash image to DUT"""
        if operator is None:
            image, operator, _ = operator_for_path(image)

        with OpendalAdapter(client=self, operator=operator, path=image, mode="rb", compression=compression) as handle:
            return self.call("flash", handle, partition)

    def flash(
        self,
        path: PathBuf | dict[str, PathBuf],
        *,
        partition: str | None = None,
        operator: Operator | dict[str, Operator] | None = None,
        compression: Compression | dict[str, Compression] | None = None,
    ):
        if isinstance(path, dict):
            if partition is not None:
                raise ArgumentError("'partition' parameter is not valid when flashing multiple images")

            results: dict[str, object] = {}

            oper_map = operator if isinstance(operator, dict) else {}
            comp_map = compression if isinstance(compression, dict) else {}

            for part, img in path.items():
                op_val = oper_map.get(part) if isinstance(operator, dict) else operator
                comp_val = comp_map.get(part) if isinstance(compression, dict) else compression
                results[part] = self._flash_single(
                    img, partition=part, operator=cast(Operator | None, op_val), compression=comp_val
                )

            return results

        if isinstance(operator, dict):
            raise ArgumentError("operator mapping provided for single image flash")
        if isinstance(compression, dict):
            raise ArgumentError("compression mapping provided for single image flash")

        return self._flash_single(path, partition=partition, operator=operator, compression=compression)

    def dump(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        compression: Compression | None = None,
    ):
        """Dump image from DUT"""
        if operator is None:
            path, operator, _ = operator_for_path(path)

        with OpendalAdapter(client=self, operator=operator, path=path, mode="wb", compression=compression) as handle:
            return self.call("dump", handle, partition)


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

    def cli(self, base=None):
        if base is None:
            base = click.group(lambda: None)

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

        @base.command
        @click.argument("file")
        def write_local_file(file):
            self.write_local_file(file)

        return base


class StorageMuxFlasherClient(FlasherClient, StorageMuxClient):
    def flash(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        compression: Compression | None = None,
    ):
        """Flash image to DUT"""
        if partition is not None:
            raise ArgumentError(f"partition is not supported for StorageMuxFlasherClient, {partition} provided")

        self.host()

        if operator is None:
            path, operator, _ = operator_for_path(path)

        with OpendalAdapter(client=self, operator=operator, path=path, mode="rb", compression=compression) as handle:
            try:
                return self.write(handle)
            finally:
                self.dut()

    def dump(
        self,
        path: PathBuf,
        *,
        partition: str | None = None,
        operator: Operator | None = None,
        compression: Compression | None = None,
    ):
        """Dump image from DUT"""
        if partition is not None:
            raise ArgumentError(f"partition is not supported for StorageMuxFlasherClient, {partition} provided")

        self.call("host")

        if operator is None:
            path, operator, _ = operator_for_path(path)

        with OpendalAdapter(client=self, operator=operator, path=path, mode="wb", compression=compression) as handle:
            try:
                return self.call("read", handle)
            finally:
                self.call("dut")

    def cli(self):
        top_cli = FlasherClient.cli(self)
        return StorageMuxClient.cli(self, top_cli)
