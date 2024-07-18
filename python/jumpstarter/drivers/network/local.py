from jumpstarter.drivers.network import Network
from dataclasses import dataclass
from contextlib import asynccontextmanager
from anyio.streams.stapled import StapledObjectStream
from anyio import (
    connect_tcp,
    create_connected_udp_socket,
    connect_unix,
    create_memory_object_stream,
)


@dataclass(kw_only=True)
class TcpNetwork(Network):
    host: str
    port: int

    @asynccontextmanager
    async def connect(self):
        async with await connect_tcp(
            remote_host=self.host, remote_port=self.port
        ) as stream:
            yield stream


@dataclass(kw_only=True)
class UdpNetwork(Network):
    host: str
    port: int

    @asynccontextmanager
    async def connect(self):
        async with await create_connected_udp_socket(
            remote_host=self.host, remote_port=self.port
        ) as stream:
            yield stream


@dataclass(kw_only=True)
class UnixNetwork(Network):
    path: str

    @asynccontextmanager
    async def connect(self):
        async with await connect_unix(path=self.path) as stream:
            yield stream


class EchoNetwork(Network):
    @asynccontextmanager
    async def connect(self):
        tx, rx = create_memory_object_stream[bytes](32)
        async with StapledObjectStream(tx, rx) as stream:
            yield stream
