from abc import ABCMeta, abstractmethod
from tempfile import NamedTemporaryFile

import click
from anyio.streams.file import FileWriteStream
from opendal import Operator

from jumpstarter.client import DriverClient
from jumpstarter.client.mixins import ResourceMixin
from jumpstarter.driver import Driver, export


class StorageMuxInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.storage.StorageMuxClient"

    @abstractmethod
    async def host(self): ...

    @abstractmethod
    async def dut(self): ...

    @abstractmethod
    async def off(self): ...

    @abstractmethod
    async def write(self, src: str): ...


class StorageMuxClient(DriverClient, ResourceMixin):
    def host(self):
        return self.call("host")

    def dut(self):
        return self.call("dut")

    def off(self):
        return self.call("off")

    def write(self, handle):
        return self.call("write", handle)

    def write_file(self, operator: Operator, path: str):
        with self.file(operator, path) as handle:
            return self.call("write", handle)

    def write_local_file(self, filepath):
        with self.file(Operator("fs", root="/"), filepath) as handle:
            return self.call("write", handle)

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


class MockStorageMux(StorageMuxInterface, Driver):
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
        with NamedTemporaryFile() as file:
            async with FileWriteStream(file) as stream:
                async with self.resource(src) as res:
                    async for chunk in res:
                        await stream.send(chunk)
