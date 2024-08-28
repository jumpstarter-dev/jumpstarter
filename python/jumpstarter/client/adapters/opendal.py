from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from opendal import AsyncOperator, Operator

from jumpstarter.common.opendal import AsyncFileStream
from jumpstarter.common.resources import PresignedRequestResource

from .common import ClientAdapter


@dataclass(kw_only=True)
class OpendalAdapter(ClientAdapter):
    @asynccontextmanager
    async def file_async(
        self,
        operator: AsyncOperator,
        path: str,
    ):
        if operator.capability().presign_read:
            presigned = await operator.presign_read(path, expire_second=60)
            yield PresignedRequestResource(
                headers=presigned.headers, url=presigned.url, method=presigned.method
            ).model_dump(mode="json")
        else:
            file = await operator.open(path, "rb")
            async with self.client.resource_async(AsyncFileStream(file=file)) as handle:
                yield handle

    @contextmanager
    def file(self, operator: Operator, path: str):
        with self.client.portal.wrap_async_context_manager(
            self.file_async(operator.to_async_operator(), path)
        ) as handle:
            yield handle
