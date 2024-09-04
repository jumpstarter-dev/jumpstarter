from dataclasses import dataclass

from anyio import create_task_group, create_tcp_listener
from anyio.abc import SocketAttribute

from jumpstarter.streams import forward_stream

from .common import ClientAdapter


@dataclass(kw_only=True)
class PortforwardAdapter(ClientAdapter):
    local_host: str = "127.0.0.1"
    local_port: int = 0
    method: str = "connect"

    async def __aenter__(self):
        self.listener = await create_tcp_listener(
            local_host=self.local_host, local_port=self.local_port, reuse_port=True
        )
        self.tg = create_task_group()

        await self.tg.__aenter__()

        self.tg.start_soon(self.listener.serve, self.handler, self.tg)

        return self.listener.extra(SocketAttribute.local_address)

    async def __aexit__(self, exc_type, exc_value, traceback):
        self.tg.cancel_scope.cancel()

        await self.tg.__aexit__(exc_type, exc_value, traceback)
        await self.listener.aclose()

    async def handler(self, conn):
        async with conn:
            async with self.client.stream_async(self.method) as stream:
                async with forward_stream(conn, stream):
                    pass
