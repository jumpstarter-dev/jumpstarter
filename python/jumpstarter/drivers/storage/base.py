from abc import ABCMeta, abstractmethod
from tempfile import NamedTemporaryFile
from uuid import UUID

from anyio.streams.file import FileWriteStream

from jumpstarter.drivers import Driver, DriverClient, drivercall


class StorageMuxInterface(metaclass=ABCMeta):
    @classmethod
    def interface(cls) -> str:
        return "storage_mux"

    @classmethod
    def version(cls) -> str:
        return "0.0.1"

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
        return await self.drivercall("host")

    async def dut(self):
        return await self.drivercall("dut")

    async def off(self):
        return await self.drivercall("off")

    async def write(self, src: str):
        return await self.drivercall("write", src)


class MockStorageMux(StorageMuxInterface, Driver):
    @drivercall
    async def host(self):
        pass

    @drivercall
    async def dut(self):
        pass

    @drivercall
    async def off(self):
        pass

    @drivercall
    async def write(self, src: str):
        with NamedTemporaryFile(delete=False) as file:
            print(f"MockStorageMux: writing to {file.name}")
            async with FileWriteStream(file) as stream:
                async for chunk in self.resources[UUID(src)]:
                    await stream.send(chunk)
