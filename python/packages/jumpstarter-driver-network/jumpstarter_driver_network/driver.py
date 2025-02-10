from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass

from anyio import (
    connect_tcp,
    connect_unix,
    create_connected_udp_socket,
    create_memory_object_stream,
)
from anyio.streams.stapled import StapledObjectStream

from jumpstarter.driver import Driver, exportstream


class NetworkInterface(metaclass=ABCMeta):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_network.client.NetworkClient"

    @abstractmethod
    @asynccontextmanager
    async def connect(self): ...


@dataclass(kw_only=True)
class TcpNetwork(NetworkInterface, Driver):
    '''
    TcpNetwork is a driver for connecting to TCP sockets

    >>> addr = getfixture("tcp_echo_server") # start a tcp echo server
    >>> config = f"""
    ... type: jumpstarter_driver_network.driver.TcpNetwork
    ... config:
    ...   host: {addr[0]} # 127.0.0.1
    ...   port: {addr[1]} # random port
    ... """
    >>> with run(config) as tcp:
    ...     with tcp.stream() as conn:
    ...         conn.send(b"hello")
    ...         assert conn.receive() == b"hello"
    '''

    host: str
    port: int

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("Connecting TCP host=%s port=%d", self.host, self.port)
        async with await connect_tcp(remote_host=self.host, remote_port=self.port) as stream:
            yield stream


@dataclass(kw_only=True)
class UdpNetwork(NetworkInterface, Driver):
    """
    UdpNetwork is a driver for connecting to UDP sockets
    """

    host: str
    port: int

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("Connecting UDP host=%s port=%d", self.host, self.port)
        async with await create_connected_udp_socket(remote_host=self.host, remote_port=self.port) as stream:
            yield stream


@dataclass(kw_only=True)
class UnixNetwork(NetworkInterface, Driver):
    """
    UnixNetwork is a driver for connecting to Unix domain sockets
    """

    path: str

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("Connecting UDS path=%s", self.path)
        async with await connect_unix(path=self.path) as stream:
            yield stream


class EchoNetwork(NetworkInterface, Driver):
    '''
    EchoNetwork is a mock driver implementing the NetworkInterface

    >>> config = """
    ... type: jumpstarter_driver_network.driver.EchoNetwork
    ... """
    >>> with run(config) as echo:
    ...     with echo.stream() as conn:
    ...         conn.send(b"hello")
    ...         assert conn.receive() == b"hello"
    '''

    @exportstream
    @asynccontextmanager
    async def connect(self):
        tx, rx = create_memory_object_stream[bytes](32)
        self.logger.debug("Connecting Echo")
        async with StapledObjectStream(tx, rx) as stream:
            yield stream
