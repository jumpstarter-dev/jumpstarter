from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

from anyio import (
    connect_tcp,
    connect_unix,
    create_connected_udp_socket,
    create_memory_object_stream,
)
from anyio.streams.stapled import StapledObjectStream

from jumpstarter.drivers import Driver, DriverClient, streamcall


class NetworkInterface(metaclass=ABCMeta):
    @classmethod
    def client_module(cls) -> str:
        return "jumpstarter.drivers.network"

    @classmethod
    def client_class(cls) -> str:
        return "NetworkClient"

    @abstractmethod
    @asynccontextmanager
    async def connect(self):
        ...


class NetworkClient(NetworkInterface, DriverClient):
    @asynccontextmanager
    async def connect(self):
        async with self._stream() as stream:
            yield stream

    @contextmanager
    def portforward(self, listener):
        with self.portal.wrap_async_context_manager(self._portforward(listener)):
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
