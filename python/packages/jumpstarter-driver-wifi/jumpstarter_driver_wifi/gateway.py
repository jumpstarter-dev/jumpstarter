from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
import anyio.lowlevel
from anyio.abc import TaskGroup

from .interface import WifiDriver, only_port
from jumpstarter.common.ports import parse_port_address
from jumpstarter.driver import Port, PortDirection, export
from jumpstarter.streams.common import forward_stream


@dataclass(kw_only=True)
class WifiGateway(WifiDriver):
    """One TCP port on a real test Wi-Fi network, answered by a service of another member of the lease.

    Beside a physical device, an edge gateway runs an access point and offers one port on it. A device on the
    Wi-Fi that connects to that port is talking to whoever is attached behind the gateway. This driver is what
    is attached: it keeps connections waiting at the gateway's attach port, and each one a device uses is
    handed to the driver's ``requires`` port, which a forward of the lease joins to a ``provides`` port: a
    TCP service inside a virtual device, for example.

    The port is ``requires`` because connections start on this side, at the devices on the Wi-Fi, and the
    service they want is on the other member. The gateway's attach port cannot be a forward's local side by
    itself: it listens, and so does the exporter for a ``requires`` port. The driver connects the two.

    It joins at the level of a connection: the device reaches one service, the two are not on one network.
    """

    # The gateway's attach port: what waits here answers the gateway's port on the Wi-Fi.
    attach: str = "127.0.0.1:7410"
    # Where the exporter listens for the forward (the ``listen`` of the driver's port).
    listen: str = "127.0.0.1:7411"
    # How many connections are kept waiting at the gateway: as many devices can connect at the same moment.
    waiting: int = 3
    # Seconds before the gateway is tried again after it could not be reached.
    retry: float = 2.0
    # Start with the exporter's session for a lease, so that a forward that is there finds the radio's end
    # running, and no test has to start it. False: only `start` does.
    autostart: bool = True

    _tasks: TaskGroup | None = field(init=False, default=None, repr=False)
    _scope: anyio.CancelScope | None = field(init=False, default=None, repr=False)
    _stats: dict = field(init=False, default_factory=dict, repr=False)

    CAPABILITIES = ("link",)

    def __post_init__(self):
        if not self.ports:
            self.ports = [Port(name="wifi-service", direction=PortDirection.REQUIRES, listen=self.listen)]
        super().__post_init__()
        port = only_port(self)
        if port.direction != PortDirection.REQUIRES or port.listen is None:
            raise ValueError(f"WifiGateway: port '{port.name}' is a 'requires' port with a 'listen' address")
        parse_port_address(self.attach)
        self._reset()

    def _reset(self) -> None:
        self._stats = {"waiting": 0, "active": 0, "joined": 0, "refused": 0, "error": ""}

    @asynccontextmanager
    async def serving(self):
        async with super().serving(), anyio.create_task_group() as tasks:
            self._tasks = tasks
            try:
                if self.autostart:
                    await self.start()
                yield
            finally:
                # The session is over: the driver detaches with it.
                self._tasks = self._scope = None
                tasks.cancel_scope.cancel()

    async def _answer(self, gateway, first: bytes) -> None:
        """A device on the Wi-Fi has connected, and said something: give it the service."""
        port = only_port(self)
        try:
            service = await anyio.connect_tcp(*port.listen_address)  # ty: ignore[not-iterable]
        except OSError:
            # No forward has this port: the exporter is not listening. The device's connection is closed.
            self._stats["refused"] += 1
            return
        self._stats["joined"] += 1
        self._stats["active"] += 1
        try:
            async with service:
                await service.send(first)
                async with forward_stream(gateway, service):
                    pass
        except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
            pass
        finally:
            self._stats["active"] -= 1

    async def _run(self, scope: anyio.CancelScope) -> None:
        with scope:
            async with anyio.create_task_group() as waiters:
                for _ in range(self.waiting):
                    waiters.start_soon(self._wait, waiters)

    async def _wait(self, waiters: TaskGroup) -> None:
        """Keep one connection waiting at the gateway. Nothing comes from it until a device on the Wi-Fi
        connects and speaks; it is then that device's connection, and another is opened to wait in its place."""
        host, tcp_port = parse_port_address(self.attach)
        while True:
            try:
                gateway = await anyio.connect_tcp(host, tcp_port)
            except OSError as error:
                self._stats["error"] = f"cannot reach the gateway at {self.attach}: {error.strerror or error}"
                await anyio.sleep(self.retry)
                continue
            self._stats["error"] = ""
            self._stats["waiting"] += 1
            first = b""
            try:
                async with gateway:
                    try:
                        first = await gateway.receive()
                    except (anyio.EndOfStream, anyio.BrokenResourceError):
                        first = b""
                    finally:
                        self._stats["waiting"] -= 1
                    if first:
                        waiters.start_soon(self._wait, waiters)  # the replacement
                        await self._answer(gateway, first)
                        return
            except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
                pass
            await anyio.sleep(self.retry)

    @export
    async def start(self) -> None:
        """Attach to the gateway. Starting it twice does nothing."""
        if self._scope is not None:
            return
        if self._tasks is None:
            raise RuntimeError("the driver is not being served")
        self._reset()
        self._scope = anyio.CancelScope()
        self._tasks.start_soon(self._run, self._scope)

    @export
    async def stop(self) -> None:
        """Detach: the gateway's port is answered by nobody, and the connections in use are closed."""
        scope, self._scope = self._scope, None
        if scope is not None:
            scope.cancel()
            await anyio.lowlevel.checkpoint()

    @export
    async def status(self) -> dict:
        """``attached``: connections are waiting at the gateway. ``peer``: a device's connection is joined to
        the service right now. ``joined`` counts those there have been, ``refused`` those that found no forward."""
        running = self._scope is not None
        stats = self._stats if running else {"waiting": 0, "active": 0, "joined": 0, "refused": 0, "error": ""}
        return {
            "running": running,
            "attached": running and stats["waiting"] + stats["active"] > 0,
            "peer": stats["active"] > 0,
            **stats,
        }
