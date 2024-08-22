from abc import ABCMeta, abstractmethod
from tempfile import NamedTemporaryFile

from anyio.streams.file import FileWriteStream

from jumpstarter.driver import Driver, export


class StorageMuxInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.drivers.storage.client.StorageMuxClient"

    @abstractmethod
    async def host(self): ...

    @abstractmethod
    async def dut(self): ...

    @abstractmethod
    async def off(self): ...

    @abstractmethod
    async def write(self, src: str): ...


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
