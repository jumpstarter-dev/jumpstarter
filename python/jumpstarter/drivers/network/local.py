from . import Network
from dataclasses import dataclass
import anyio


@dataclass(kw_only=True)
class TcpNetwork(Network):
    host: str
    port: int

    async def connect(self):
        return await anyio.connect_tcp(remote_host=self.host, remote_port=self.port)


@dataclass(kw_only=True)
class UnixNetwork(Network):
    path: str

    async def connect(self):
        return await anyio.connect_unix(path=self.path)
