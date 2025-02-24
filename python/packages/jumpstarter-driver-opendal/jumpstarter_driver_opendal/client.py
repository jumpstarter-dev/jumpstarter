from collections.abc import Generator
from pathlib import Path

import asyncclick as click
from opendal import Operator

from .adapter import OpendalAdapter
from jumpstarter.client import DriverClient


class OpendalClient(DriverClient):
    def copy(self, /, source, target):
        self.call("copy", source, target)

    def rename(self, /, source, target):
        self.call("rename", source, target)

    def remove_all(self, /, path):
        self.call("remove_all", path)

    def create_dir(self, /, path):
        self.call("create_dir", path)

    def delete(self, /, path):
        self.call("delete", path)

    def exists(self, /, path) -> bool:
        return self.call("exists", path)

    def list(self, /, path) -> Generator[str, None, None]:
        yield from self.streamingcall("list", path)

    def scan(self, /, path) -> Generator[str, None, None]:
        yield from self.streamingcall("scan", path)


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
