from abc import ABCMeta, abstractmethod
from tempfile import NamedTemporaryFile
from uuid import UUID

import click
from anyio import from_thread
from anyio.streams.file import FileWriteStream

from jumpstarter.drivers import Driver, DriverClient, export


class StorageMuxInterface(metaclass=ABCMeta):
    @classmethod
    def client_module(cls) -> str:
        return "jumpstarter.drivers.storage"

    @classmethod
    def client_class(cls) -> str:
        return "StorageMuxClient"

    @abstractmethod
    async def host(self): ...

    @abstractmethod
    async def dut(self): ...

    @abstractmethod
    async def off(self): ...

    @abstractmethod
    async def write(self, src: str): ...


class StorageMuxClient(StorageMuxInterface, DriverClient):
    async def host(self):
        return await self.call("host")

    async def dut(self):
        return await self.call("dut")

    async def off(self):
        return await self.call("off")

    async def write(self, src: str):
        return await self.call("write", src)

    def cli(self):
        @click.group
        def base():
            """Generic storage mux"""
            pass

        @base.command()
        def host():
            """Connect storage to host"""
            from_thread.run(self.host)

        @base.command()
        def dut():
            """Connect storage to dut"""
            from_thread.run(self.dut)

        @base.command()
        def off():
            """Disconnect storage"""
            from_thread.run(self.off)

        @base.command()
        @click.argument("file")
        def write(file):
            async def write_impl():
                async with self.local_file(file) as handle:
                    await self.write(handle)

            from_thread.run(write_impl)

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
                async for chunk in self.resources[UUID(src)]:
                    await stream.send(chunk)
