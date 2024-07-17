from anyio.streams.file import FileWriteStream
from jumpstarter.drivers import ContextStore
from uuid import UUID


class StorageMuxLocalWriterMixin:
    async def write(self, src: str):
        path = self.host()

        async with await FileWriteStream.from_path(path) as stream:
            async for chunk in ContextStore.get().conns[UUID(src)]:
                await stream.send(chunk)
