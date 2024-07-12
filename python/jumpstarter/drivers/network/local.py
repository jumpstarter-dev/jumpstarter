from . import Network
from dataclasses import dataclass
import contextlib
import anyio


@dataclass(kw_only=True)
class TcpNetwork(Network):
    host: str
    port: int

    @contextlib.asynccontextmanager
    async def connect(self):
        stream = await anyio.connect_tcp(remote_host=self.host, remote_port=self.port)
        try:
            yield stream
        finally:
            await stream.aclose()


@dataclass(kw_only=True)
class UnixNetwork(Network):
    path: str

    @contextlib.asynccontextmanager
    async def connect(self):
        stream = await anyio.connect_unix(path=self.path)
        try:
            yield stream
        finally:
            await stream.aclose()


class EchoNetwork(Network):
    @contextlib.asynccontextmanager
    async def connect(self):
        tx, rx = anyio.create_memory_object_stream[bytes](32)
        stream = anyio.streams.stapled.StapledObjectStream(tx, rx)
        try:
            yield stream
        finally:
            await stream.aclose()
