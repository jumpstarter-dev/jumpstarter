from __future__ import annotations

import math
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

import anyio
from anyio import create_memory_object_stream
from anyio.abc import ObjectStream

from .interface import BluetoothDriver, only_port
from jumpstarter.driver import Port, PortDirection, export


class _Loopback(ObjectStream[bytes]):
    """The mock's side of one forwarded connection: what is sent into it comes back out of it."""

    def __init__(self, radio: MockBluetooth) -> None:
        self.radio = radio
        self.back, self.out = create_memory_object_stream[bytes](math.inf)
        self.joined = False

    def _join(self) -> None:
        # The exporter reads and writes the local side only once the far end is on the path.
        if not self.joined:
            self.joined = True
            self.radio._joined += 1

    async def send(self, item: bytes) -> None:
        self._join()
        radio = self.radio
        if not radio._running:
            return  # a radio that is off hears nothing
        radio._heard += len(item)
        if radio._reply is not None:
            radio._reply += item
            if radio._arrived is not None:
                radio._arrived.set()
        else:
            self.say(item)

    def say(self, data: bytes) -> None:
        self.radio._sent += len(data)
        self.back.send_nowait(data)

    async def receive(self) -> bytes:
        self._join()
        try:
            return await self.out.receive()
        except anyio.ClosedResourceError:
            raise anyio.EndOfStream from None

    async def send_eof(self) -> None:
        pass

    async def aclose(self) -> None:
        if self.joined:
            self.joined = False
            self.radio._joined -= 1
        self.back.close()
        self.out.close()


@dataclass(kw_only=True)
class MockBluetooth(BluetoothDriver):
    """A Bluetooth with nothing behind it: what goes into its port comes back out. For trying a forward without a
    device.

    Its one port is ``connects`` by default: two mock radios joined by a forward are a path, and what one says
    with ``exchange`` the other gives back. As ``provides`` it is a service that echoes; as ``requires`` it is
    the client of one: ``exchange`` dials the ``listen`` address, where the exporter listens for the forward.
    """

    direction: Literal["connects", "provides", "requires"] = "connects"
    port_name: str = "echo"
    protocol: str = "echo"
    # requires only: where the exporter listens for this radio's connections.
    listen: str | None = None

    _running: bool = field(init=False, default=False, repr=False)
    _joined: int = field(init=False, default=0, repr=False)
    _heard: int = field(init=False, default=0, repr=False)
    _sent: int = field(init=False, default=0, repr=False)
    _air: _Loopback | None = field(init=False, default=None, repr=False)
    _reply: bytearray | None = field(init=False, default=None, repr=False)
    _arrived: anyio.Event | None = field(init=False, default=None, repr=False)

    CAPABILITIES = ("link",)

    def __post_init__(self):
        if not self.ports:
            self.ports = [
                Port(name=self.port_name, direction=PortDirection(self.direction), protocol=self.protocol,
                     listen=self.listen)
            ]  # fmt: skip
        super().__post_init__()
        only_port(self)

    @asynccontextmanager
    async def connect_port(self, port: Port):
        """The local side of one forwarded connection: the mock itself, not a socket."""
        air = _Loopback(self)
        self._air = air
        try:
            yield air
        finally:
            if self._air is air:
                self._air = None
            await air.aclose()

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_bluetooth.client.MockBluetoothClient"

    @export
    async def start(self) -> None:
        self._running = True

    @export
    async def stop(self) -> None:
        self._running = False

    @export
    async def status(self) -> dict:
        return {
            "running": self._running,
            "attached": self._running,
            "peer": self._joined > 0,
            "heard": self._heard,
            "sent": self._sent,
        }

    @export
    async def exchange(self, data: str, timeout: float = 5.0) -> str:
        """Say something into the port and give back as many bytes as come back in answer."""
        if not self._running:
            raise RuntimeError("the radio is stopped")
        said = data.encode()
        port = only_port(self)
        if port.direction != PortDirection.REQUIRES:
            with anyio.move_on_after(timeout):
                while self._air is None:
                    await anyio.sleep(0.02)
            if self._air is None:
                raise RuntimeError(f"nothing is joined to port '{port.name}'")
        with anyio.fail_after(timeout):
            if port.direction == PortDirection.REQUIRES:
                host, tcp_port = port.listen_address  # ty: ignore[not-iterable]
                async with await anyio.connect_tcp(host, tcp_port) as stream:
                    await stream.send(said)
                    self._sent += len(said)
                    reply = bytearray()
                    while len(reply) < len(said):
                        reply += await stream.receive()
                    self._heard += len(reply)
                    return reply.decode()
            air = self._air
            if air is None:
                raise RuntimeError(f"nothing is joined to port '{port.name}'")
            self._reply = bytearray()
            try:
                air.say(said)
                while len(self._reply) < len(said):
                    self._arrived = anyio.Event()
                    await self._arrived.wait()
                return bytes(self._reply).decode()
            finally:
                self._reply = None
