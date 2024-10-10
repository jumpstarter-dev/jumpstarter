from dataclasses import dataclass

from jumpstarter.common import TemporaryTcpListener
from jumpstarter.streams import forward_stream

from .common import ClientAdapter


@dataclass(kw_only=True)
class PortforwardAdapter(ClientAdapter):
    local_host: str = "127.0.0.1"
    local_port: int = 0
    method: str = "connect"

    async def __aenter__(self):
        self.listener = TemporaryTcpListener(
            self.handler, local_host=self.local_host, local_port=self.local_port, reuse_port=True
        )

        return await self.listener.__aenter__()

    async def __aexit__(self, exc_type, exc_value, traceback):
        return await self.listener.__aexit__(exc_type, exc_value, traceback)

    async def handler(self, conn):
        async with conn:
            async with self.client.stream_async(self.method) as stream:
                async with forward_stream(conn, stream):
                    pass
