from jumpstarter.drivers import Driver, DriverClient, ContextStore, drivercall
from anyio.streams.file import FileWriteStream
from tempfile import NamedTemporaryFile
from uuid import UUID
from abc import ABC, abstractmethod


class StorageMuxInterface(ABC):
    def interface(self) -> str:
        return "storage_mux"

    def version(self) -> str:
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
        return await self._drivercall("host")

    async def dut(self):
        return await self._drivercall("dut")

    async def off(self):
        return await self._drivercall("off")

    async def write(self, src: str):
        return await self._drivercall("write", src)


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
                async for chunk in ContextStore.get().conns[UUID(src)]:
                    await stream.send(chunk)
