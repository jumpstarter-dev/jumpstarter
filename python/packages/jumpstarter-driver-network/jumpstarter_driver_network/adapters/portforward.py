from dataclasses import dataclass

from jumpstarter.client.adapters import ClientAdapter
from jumpstarter.common import TemporaryTcpListener, TemporaryUnixListener
from jumpstarter.streams import forward_stream


@dataclass(kw_only=True)
class PortforwardAdapter(ClientAdapter):
    method: str = "connect"

    async def __aexit__(self, exc_type, exc_value, traceback):
        return await self.listener.__aexit__(exc_type, exc_value, traceback)

    async def handler(self, conn):
        async with conn:
            async with self.client.stream_async(self.method) as stream:
                async with forward_stream(conn, stream):
                    pass


@dataclass(kw_only=True)
class TcpPortforwardAdapter(PortforwardAdapter):
    local_host: str = "127.0.0.1"
    local_port: int = 0

    async def __aenter__(self):
        self.listener = TemporaryTcpListener(
            self.handler, local_host=self.local_host, local_port=self.local_port, reuse_port=True
        )

        return await self.listener.__aenter__()


@dataclass(kw_only=True)
class UnixPortforwardAdapter(PortforwardAdapter):
    async def __aenter__(self):
        self.listener = TemporaryUnixListener(self.handler)

        return await self.listener.__aenter__()
