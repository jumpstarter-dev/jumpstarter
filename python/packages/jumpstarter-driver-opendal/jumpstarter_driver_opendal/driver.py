from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper
from uuid import UUID, uuid4

from anyio.streams.file import FileReadStream, FileWriteStream
from opendal import AsyncFile, AsyncOperator
from pydantic import validate_call

from .common import Capability, Metadata, PresignedRequest
from jumpstarter.driver import Driver, export


@dataclass(kw_only=True)
class Opendal(Driver):
    scheme: str
    kwargs: dict[str, str]

    _operator: AsyncOperator = field(init=False)
    _fds: dict[UUID, AsyncFile] = field(init=False, default_factory=dict)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_opendal.client.OpendalClient"

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

        self._operator = AsyncOperator(self.scheme, **self.kwargs)

    @export
    @validate_call(validate_return=True)
    async def open(self, /, path: str, mode: str) -> UUID:
        file = await self._operator.open(path, mode)
        uuid = uuid4()

        self._fds[uuid] = file

        return uuid

    async def read(self, /, path):
        pass

    async def write(self, /, path, bs, **kwargs):
        pass

    @export
    @validate_call(validate_return=True)
    async def file_close(self, /, fd: UUID) -> None:
        await self._fds[fd].close()

    @export
    @validate_call(validate_return=True)
    async def file_closed(self, /, fd: UUID) -> bool:
        return await self._fds[fd].closed

    @export
    @validate_call(validate_return=True)
    async def file_readable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].readable()

    @export
    @validate_call(validate_return=True)
    async def file_seekable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].seekable()

    @export
    @validate_call(validate_return=True)
    async def file_writable(self, /, fd: UUID) -> bool:
        return await self._fds[fd].writable()

    @export
    @validate_call(validate_return=True)
    async def stat(self, /, path: str) -> Metadata:
        return Metadata.model_validate(await self._operator.stat(path), from_attributes=True)

    @export
    @validate_call(validate_return=True)
    async def copy(self, /, source: str, target: str):
        await self._operator.copy(source, target)

    @export
    @validate_call(validate_return=True)
    async def rename(self, /, source: str, target: str):
        await self._operator.rename(source, target)

    @export
    @validate_call(validate_return=True)
    async def remove_all(self, /, path: str):
        await self._operator.remove_all(path)

    @export
    @validate_call(validate_return=True)
    async def create_dir(self, /, path: str):
        await self._operator.create_dir(path)

    @export
    @validate_call(validate_return=True)
    async def delete(self, /, path: str):
        await self._operator.delete(path)

    @export
    @validate_call(validate_return=True)
    async def exists(self, /, path: str) -> bool:
        return await self._operator.exists(path)

    @export
    async def list(self, /, path: str) -> AsyncGenerator[str, None]:
        async for entry in await self._operator.list(path):
            yield entry.path

    @export
    async def scan(self, /, path: str) -> AsyncGenerator[str, None]:
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
