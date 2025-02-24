from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper
from typing import Optional

from anyio.streams.file import FileReadStream, FileWriteStream
from opendal import AsyncOperator, Capability

from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Opendal(Driver):
    scheme: str
    kwargs: dict[str, str]

    _operator: AsyncOperator = field(init=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.OpendalClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._operator = AsyncOperator(self.scheme, **self.kwargs)

    async def open(self, /, path, mode):
        pass

    async def read(self, /, path):
        pass

    async def write(self, /, path, bs, **kwargs):
        pass

    async def stat(self, /, path):
        pass

    async def copy(self, /, source, target):
        pass

    async def rename(self, /, source, target):
        pass

    async def remove_all(self, /, path):
        pass

    @export
    async def create_dir(self, /, path):
        await self._operator.create_dir(path)

    async def delete(self, /, path):
        pass

    async def exists(self, /, path):
        pass

    async def list(self, /, path):
        pass

    async def scan(self, /, path):
        pass

    async def presign_stat(self, /, path, expire_second):
        pass

    async def presign_read(self, /, path, expire_second):
        pass

    async def presign_write(self, /, path, expire_second):
        pass

    async def capability(self, /) -> Capability:
        pass


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
