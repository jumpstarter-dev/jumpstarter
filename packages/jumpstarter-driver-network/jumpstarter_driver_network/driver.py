from abc import ABCMeta, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass
from os import getenv, getuid
from typing import ClassVar, Literal

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
    path: str

    @exportstream
    @asynccontextmanager
    async def connect(self):
        self.logger.debug("Connecting UDS path=%s", self.path)
        async with await connect_unix(path=self.path) as stream:
            yield stream


@dataclass(kw_only=True)
class DbusNetwork(NetworkInterface, Driver):
    kind: Literal["system", "session"]

    KIND_LABEL: ClassVar[str] = "jumpstarter.dev/dbusnetwork/kind"

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_network.client.DbusNetworkClient"

    def extra_labels(self):
        return {self.KIND_LABEL: self.kind}

    @exportstream
    @asynccontextmanager
    async def connect(self):  # noqa: C901
        match self.kind:
            case "system":
                bus = getenv("DBUS_SYSTEM_BUS_ADDRESS", "unix:path=/run/dbus/system_bus_socket")
            case "session":
                bus = getenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{getuid()}/bus")
            case _:
                raise ValueError(f"invalid bus type: {self.kind}")

        scheme, sep, rem = bus.partition(":")
        if not sep:
            raise ValueError(f"invalid bus addr: {bus}")

        args = {}
        for part in rem.split(","):
            key, sep, value = part.partition("=")
            if not sep:
                raise ValueError(f"invalid bus addr: {bus}, missing separator in arguments")
            args[key] = value

        match scheme:
            case "unix":
                if "path" not in args:
                    raise ValueError(f"invalid bus addr: {bus}, missing path argument")

                self.logger.debug("Connecting UDS path=%s", args["path"])
                async with await connect_unix(path=args["path"]) as stream:
                    yield stream
            case "tcp":
                if "host" not in args:
                    raise ValueError(f"invalid bus addr: {bus}, missing host argument")
                if "port" not in args:
                    raise ValueError(f"invalid bus addr: {bus}, missing port argument")

                try:
                    port = int(args["port"])
                except ValueError as e:
                    raise ValueError(f"invalid bus addr: {bus}, invalid port argument") from e

                self.logger.debug("Connecting TCP host=%s port=%d", args["host"], port)
                async with await connect_tcp(remote_host=args["host"], remote_port=port) as stream:
                    yield stream
            case _:
                raise ValueError(f"invalid bus scheme: {scheme}")


class EchoNetwork(NetworkInterface, Driver):
    @exportstream
    @asynccontextmanager
    async def connect(self):
        tx, rx = create_memory_object_stream[bytes](32)
        self.logger.debug("Connecting Echo")
        async with StapledObjectStream(tx, rx) as stream:
            yield stream
