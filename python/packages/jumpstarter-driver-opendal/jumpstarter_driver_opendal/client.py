from collections.abc import Generator
from pathlib import Path

import asyncclick as click
from opendal import Operator
from pydantic import validate_call

from .adapter import OpendalAdapter
from .common import Capability, Metadata, PresignedRequest
from jumpstarter.client import DriverClient


class OpendalClient(DriverClient):
    @validate_call(validate_return=True)
    def stat(self, /, path: str) -> Metadata:
        return self.call("stat", path)

    @validate_call(validate_return=True)
    def copy(self, /, source: str, target: str):
        self.call("copy", source, target)

    @validate_call(validate_return=True)
    def rename(self, /, source: str, target: str):
        self.call("rename", source, target)

    @validate_call(validate_return=True)
    def remove_all(self, /, path: str):
        self.call("remove_all", path)

    @validate_call(validate_return=True)
    def create_dir(self, /, path: str):
        self.call("create_dir", path)

    @validate_call(validate_return=True)
    def delete(self, /, path: str):
        self.call("delete", path)

    @validate_call(validate_return=True)
    def exists(self, /, path: str) -> bool:
        return self.call("exists", path)

    def list(self, /, path) -> Generator[str, None, None]:
        yield from self.streamingcall("list", path)

    def scan(self, /, path) -> Generator[str, None, None]:
        yield from self.streamingcall("scan", path)

    @validate_call(validate_return=True)
    def presign_stat(self, /, path: str, expire_second: int) -> PresignedRequest:
        return self.call("presign_stat", path, expire_second)

    @validate_call(validate_return=True)
    def presign_read(self, /, path: str, expire_second: int) -> PresignedRequest:
        return self.call("presign_read", path, expire_second)

    @validate_call(validate_return=True)
    def presign_write(self, /, path: str, expire_second: int) -> PresignedRequest:
        return self.call("presign_write", path, expire_second)

    @validate_call(validate_return=True)
    def capability(self, /) -> Capability:
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
