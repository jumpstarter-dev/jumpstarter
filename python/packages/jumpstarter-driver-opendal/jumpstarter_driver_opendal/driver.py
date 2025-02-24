from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper

from anyio.streams.file import FileReadStream, FileWriteStream
from opendal import AsyncOperator
from pydantic import validate_call

from .common import Capability, Metadata, PresignedRequest
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

    @export
    async def stat(self, /, path) -> Metadata:
        return Metadata.model_validate(await self._operator.stat(path), from_attributes=True)

    @export
    async def copy(self, /, source, target):
        await self._operator.copy(source, target)

    @export
    async def rename(self, /, source, target):
        await self._operator.rename(source, target)

    @export
    async def remove_all(self, /, path):
        await self._operator.remove_all(path)

    @export
    async def create_dir(self, /, path):
        await self._operator.create_dir(path)

    @export
    async def delete(self, /, path):
        await self._operator.delete(path)

    @export
    async def exists(self, /, path) -> bool:
        return await self._operator.exists(path)

    @export
    async def list(self, /, path) -> AsyncGenerator[str, None]:
        async for entry in await self._operator.list(path):
            yield entry.path

    @export
    async def scan(self, /, path) -> AsyncGenerator[str, None]:
        async for entry in await self._operator.scan(path):
            yield entry.path

    @export
    @validate_call(validate_return=True)
    async def presign_stat(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_stat(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def presign_read(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_read(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def presign_write(self, /, path: str, expire_second: int) -> PresignedRequest:
        return PresignedRequest.model_validate(
            await self._operator.presign_write(path, expire_second), from_attributes=True
        )

    @export
    @validate_call(validate_return=True)
    async def capability(self, /) -> Capability:
        return Capability.model_validate(self._operator.capability(), from_attributes=True)


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
