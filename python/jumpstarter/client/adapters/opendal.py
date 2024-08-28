from dataclasses import dataclass

from opendal import Operator

from jumpstarter.common.opendal import AsyncFileStream
from jumpstarter.common.resources import PresignedRequestResource

from .common import ClientAdapter


@dataclass(kw_only=True)
class OpendalAdapter(ClientAdapter):
    operator: Operator
    path: str

    async def __aenter__(self):
        if self.operator.capability().presign_read:
            presigned = await self.operator.to_async_operator().presign_read(self.path, expire_second=60)
            return PresignedRequestResource(
                headers=presigned.headers, url=presigned.url, method=presigned.method
            ).model_dump(mode="json")
        else:
            file = await self.operator.to_async_operator().open(self.path, "rb")

            self.resource = self.client.resource_async(AsyncFileStream(file=file))

            return await self.resource.__aenter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        if hasattr(self, "resource"):
            await self.resource.__aexit__(exc_type, exc_value, traceback)
