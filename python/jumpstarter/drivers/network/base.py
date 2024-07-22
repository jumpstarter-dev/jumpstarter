from jumpstarter.drivers import Driver, DriverClient, streamcall
from dataclasses import dataclass
from contextlib import asynccontextmanager
from anyio.streams.stapled import StapledObjectStream
from abc import ABC, abstractmethod
from anyio import (
    connect_tcp,
    create_connected_udp_socket,
    connect_unix,
    create_memory_object_stream,
)


class NetworkInterface(ABC):
    @classmethod
    def interface(cls) -> str:
        return "network"

    @classmethod
    def version(cls) -> str:
        return "0.0.1"

    @abstractmethod
    @asynccontextmanager
    async def connect(self): ...


class NetworkClient(NetworkInterface, DriverClient):
    @asynccontextmanager
    async def connect(self):
        async with self._stream() as stream:
            yield stream

    @asynccontextmanager
    async def portforward(self, listener):
        async with self._portforward(listener):
            yield


@dataclass(kw_only=True)
class TcpNetwork(NetworkInterface, Driver):
    host: str
    port: int

    @streamcall
    @asynccontextmanager
    async def connect(self):
        async with await connect_tcp(remote_host=self.host, remote_port=self.port) as stream:
            yield stream


@dataclass(kw_only=True)
class UdpNetwork(NetworkInterface, Driver):
    host: str
    port: int

    @streamcall
    @asynccontextmanager
    async def connect(self):
        async with await create_connected_udp_socket(remote_host=self.host, remote_port=self.port) as stream:
            yield stream


@dataclass(kw_only=True)
class UnixNetwork(NetworkInterface, Driver):
    path: str

    @streamcall
    @asynccontextmanager
    async def connect(self):
        async with await connect_unix(path=self.path) as stream:
            yield stream


class EchoNetwork(NetworkInterface, Driver):
    @streamcall
    @asynccontextmanager
    async def connect(self):
        tx, rx = create_memory_object_stream[bytes](32)
        async with StapledObjectStream(tx, rx) as stream:
            yield stream
