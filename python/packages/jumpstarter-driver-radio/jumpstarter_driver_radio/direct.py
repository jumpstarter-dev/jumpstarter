"""Two virtual devices on one simulated Bluetooth controller simulator, with no bridge between them.

rootcanal, the simulator behind a Cuttlefish device's Bluetooth, takes any number of hosts on its HCI port and
gives each a controller of its own, all on one simulated air. A second virtual device that is started without
a simulator (Cuttlefish: ``rootcanal_instance_num``) and whose HCI goes to the first device's simulator is
then simply a second phone in the same room: its own Android stack pairs with the other's, once, with the
names and the code each really shows.

The HCI of the second device is one TCP stream (H4), which a forward of the lease carries:

- `RootcanalController`, beside the device that has the simulator: a ``provides`` port to its HCI port.
- `RemoteController`, beside the device that has none: a ``requires`` port, and a small stand-in between the
  device and the forward, because the device does not survive a stream that goes away (see the class).
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
from anyio.abc import ByteStream, TaskGroup

from .driver import RadioInterface, only_port, port_reports
from jumpstarter.common.ports import parse_port_address
from jumpstarter.driver import Driver, Port, PortDirection, export

PROTOCOL = "hci-h4"

# An H4 packet is a type, a header, and as many bytes as the header says. By type: the size of the header,
# where in it the length is, and how many bytes the length has (little endian).
_HEADERS = {
    0x01: (3, 2, 1),  # command: opcode, length
    0x02: (4, 2, 2),  # ACL data: handle, length
    0x03: (3, 2, 1),  # SCO data: handle, length
    0x04: (2, 1, 1),  # event: code, length
    0x05: (4, 2, 2),  # ISO data: handle, length (14 bits)
}
_GONE = (anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError, OSError)


class NotH4(ValueError):
    """What came is not an H4 packet: the stream is not HCI, or lost its place."""


async def read_packet(stream: ByteStream, buffer: bytearray) -> bytes:
    """The next whole H4 packet of a stream. ``buffer`` holds what was read beyond it, for the next call."""
    while True:
        if buffer:
            header = _HEADERS.get(buffer[0])
            if header is None:
                raise NotH4(f"0x{buffer[0]:02x} is not an H4 packet type")
            size, at, width = header
            if len(buffer) > size:
                length = int.from_bytes(buffer[1 + at : 1 + at + width], "little")
                if buffer[0] == 0x05:
                    length &= 0x3FFF
                total = 1 + size + length
                if len(buffer) >= total:
                    packet = bytes(buffer[:total])
                    del buffer[:total]
                    return packet
        buffer += await stream.receive()


@dataclass(kw_only=True)
class RootcanalController(RadioInterface, Driver):
    """The HCI port of a virtual device's controller simulator (rootcanal: ``--hci_port``, 7300 for the first
    Cuttlefish instance), offered to another device of the lease.

    Port ``bluetooth`` (``provides``, ``hci-h4``): each connection that a forward brings is one more host of
    the simulator, with a controller and an address of its own, on the same air as the device beside it.
    There is nothing to start: the port is there while the device is powered on.
    """

    host: str = "127.0.0.1"
    port: int = 7300

    _hosts: int = field(init=False, default=0, repr=False)
    _served: int = field(init=False, default=0, repr=False)
    _error: str = field(init=False, default="", repr=False)

    def __post_init__(self):
        if not self.ports:
            self.ports = [Port(name="bluetooth", direction=PortDirection.PROVIDES, protocol=PROTOCOL)]
        super().__post_init__()
        if only_port(self).direction != PortDirection.PROVIDES:
            raise ValueError("RootcanalController: the port of a controller simulator is 'provides'")

    def port_address(self, port: Port) -> tuple[str, int] | None:
        return self.host, self.port

    @asynccontextmanager
    async def connect_port(self, port: Port):
        try:
            stream = await anyio.connect_tcp(self.host, self.port)
        except OSError as error:
            # (the device is not powered on yet: whoever asked asks again)
            self._error = str(error) or type(error).__name__
            raise
        async with stream:
            self._hosts += 1
            self._served += 1
            self._error = ""
            try:
                yield stream
            finally:
                self._hosts -= 1

    @export
    async def start(self) -> None:
        """Nothing to start: the simulator is the device's own."""

    @export
    async def stop(self) -> None:
        """Nothing to stop."""

    @export
    async def status(self) -> dict:
        """``peer``: a far device is a host of the simulator now. ``hosts``: how many are; ``served``: how
        many there have been. ``last_error``: why the last one could not be connected (the device is off)."""
        return {
            "running": True,
            "attached": not self._error,
            "peer": self._hosts > 0,
            "hosts": self._hosts,
            "served": self._served,
            "last_error": self._error,
        }

    @export
    async def declared_ports(self) -> list[dict]:
        return port_reports(self)


@dataclass(kw_only=True)
class RemoteController(RadioInterface, Driver):
    """The Bluetooth of a virtual device that has no controller simulator: it uses the one of another device
    of the lease, as one more of its hosts.

    Port ``bluetooth`` (``requires``, ``hci-h4``): the device's HCI, to a `RootcanalController`.

    The device's connector (Cuttlefish's ``tcp_connector``, which dials ``127.0.0.1:7301`` when the device is
    made with ``rootcanal_instance_num`` 2) is not given the forward itself. It looks for its port once a
    second until it is there, which is fine; but when its stream goes away it is killed by its next write,
    and Cuttlefish stops the whole device with it. So this driver listens where the connector dials
    (``guest``), keeps that connection for as long as the device runs, and is itself the client of the forward
    (``listen``, where the exporter listens for it). Between the two it passes whole H4 packets only: a path
    that is cut in the middle of a packet leaves neither side out of step.

    A path that comes back is a new controller, which knows nothing of what the device's Bluetooth stack had
    set up in the old one. With ``adb`` set, the device's Bluetooth is then switched off and on, which is
    what a stack does to start with a controller. Bonds are the stack's own and stay.

    The far device must be powered on first: Android does not finish booting without a controller.
    """

    # Where the device's connector dials: 7300 + rootcanal_instance_num - 1.
    guest: str = "127.0.0.1:7301"
    # Where the exporter listens for this driver's connections to the far controller (the port's `listen`).
    listen: str = "127.0.0.1:7311"
    # Seconds before a path that is not there, or went away, is looked for again ...
    retry: float = 1.0
    # ... and seconds a path has to last before it counts as one. (The exporter takes a connection before it
    # knows whether the far end can be reached.)
    settle: float = 2.0
    # The device as the ADB server of this exporter knows it (`127.0.0.1:6520`), to switch its Bluetooth off
    # and on when the controller is a new one. None: nothing is done, and a test has to.
    adb: str | None = None
    adb_server_port: int = 15037
    adb_path: str = "adb"
    adb_timeout: float = 30.0
    # Look for the path from the start of the lease. False: only `start` does.
    autostart: bool = True

    _tasks: TaskGroup | None = field(init=False, default=None, repr=False)
    _dialing: anyio.CancelScope | None = field(init=False, default=None, repr=False)
    _device: ByteStream | None = field(init=False, default=None, repr=False)
    _far: ByteStream | None = field(init=False, default=None, repr=False)
    _joined: bool = field(init=False, default=False, repr=False)
    _listening: bool = field(init=False, default=False, repr=False)
    # What the device's stack knows of controllers so far: it has talked to one (`_carried`), or only to
    # nobody (`_dropped`).
    _carried: int = field(init=False, default=0, repr=False)
    _dropped: int = field(init=False, default=0, repr=False)
    _unsettled: int = field(init=False, default=0, repr=False)
    _heard: int = field(init=False, default=0, repr=False)
    _sent: int = field(init=False, default=0, repr=False)
    _paths: int = field(init=False, default=0, repr=False)
    _restarts: int = field(init=False, default=0, repr=False)
    _error: str = field(init=False, default="", repr=False)

    def __post_init__(self):
        if not self.ports:
            self.ports = [
                Port(name="bluetooth", direction=PortDirection.REQUIRES, protocol=PROTOCOL, listen=self.listen)
            ]
        super().__post_init__()
        port = only_port(self)
        if port.direction != PortDirection.REQUIRES or port.listen is None:
            raise ValueError("RemoteController: its port is 'requires', with 'listen'")
        if parse_port_address(self.guest) == port.listen_address:
            raise ValueError("RemoteController: 'guest' and 'listen' are two addresses")

    # The device's side: kept for as long as the device has it.

    async def _serve_device(self, *, task_status=anyio.TASK_STATUS_IGNORED) -> None:
        host, port = parse_port_address(self.guest)
        async with await anyio.create_tcp_listener(local_host=host, local_port=port) as listener:
            self._listening = True
            task_status.started()
            try:
                await listener.serve(self._device_connected)
            finally:
                self._listening = False

    async def _device_connected(self, stream: ByteStream) -> None:
        async with stream:
            if self._device is not None:
                # One device, one connector: a second connection is the device made anew.
                await self._device.aclose()
            self._device = stream
            self._carried = self._dropped = self._unsettled = 0
            self.logger.info("Bluetooth: the device's connector is here")
            buffer = bytearray()
            try:
                while True:
                    packet = await read_packet(stream, buffer)
                    self._heard += 1
                    far = self._far
                    if far is None:
                        self._dropped += 1  # (said to nobody: the stack asks again, or gives up and is restarted)
                        continue
                    try:
                        await far.send(packet)
                    except _GONE:
                        self._dropped += 1
                        continue
                    if self._joined:
                        self._carried += 1
                    else:
                        self._unsettled += 1  # (carried if the path lasts, said to nobody if it does not)
            except _GONE:
                pass
            except NotH4 as error:
                self._error = str(error)
                self.logger.warning("Bluetooth: the device's connector: %s", error)
            finally:
                if self._device is stream:
                    self._device = None
                    self.logger.info("Bluetooth: the device's connector went away")

    # The far side: looked for again whenever it is not there.

    async def _dial(self, scope: anyio.CancelScope) -> None:
        host, port = only_port(self).listen_address  # ty: ignore[not-iterable]
        with scope:
            while True:
                try:
                    async with await anyio.connect_tcp(host, port) as far, anyio.create_task_group() as path:
                        self._far = far
                        path.start_soon(self._path_lasted)
                        try:
                            await self._from_far(far)
                        finally:
                            self._far = None
                            self._dropped, self._unsettled = self._dropped + self._unsettled, 0
                            if self._joined:
                                self._joined = False
                                self.logger.info("Bluetooth: the path to the far controller went away")
                            path.cancel_scope.cancel()
                except _GONE as error:
                    self._error = str(error) or type(error).__name__
                await anyio.sleep(self.retry)

    async def _from_far(self, far: ByteStream) -> None:
        buffer = bytearray()
        try:
            while True:
                packet = await read_packet(far, buffer)
                device = self._device
                if device is None:
                    continue
                try:
                    await device.send(packet)
                    self._sent += 1
                except _GONE:
                    pass
        except _GONE:
            pass
        except NotH4 as error:
            self._error = str(error)
            self.logger.warning("Bluetooth: the far controller: %s", error)

    async def _path_lasted(self) -> None:
        # What the stack had said before this path is what tells whether it has to start again.
        carried, dropped = self._carried, self._dropped
        await anyio.sleep(self.settle)
        self._joined = True
        self._carried, self._unsettled = self._carried + self._unsettled, 0
        self._paths += 1
        self._error = ""
        self.logger.info("Bluetooth: the path to the far controller is open")
        if self.adb is None or not (carried or dropped):
            return  # (a device that has said nothing yet finds its controller by itself)
        try:
            await self._restart_bluetooth(was_set_up=carried > 0)
            self._restarts += 1
        except Exception as error:  # noqa: BLE001 - the device is off or booting: its stack starts by itself
            self.logger.info("Bluetooth: the device's Bluetooth was not restarted: %s", error)

    async def _adb(self, *arguments: str) -> str:
        environment = {**os.environ, "ANDROID_ADB_SERVER_PORT": str(self.adb_server_port)}
        command = [self.adb_path, "-s", str(self.adb), *arguments]
        with anyio.fail_after(self.adb_timeout):
            done = await anyio.run_process(command, env=environment, check=False)
        if done.returncode != 0:
            said = (done.stderr or done.stdout or b"").decode(errors="replace").strip().splitlines()
            raise RuntimeError(said[-1] if said else f"adb exited with {done.returncode}")
        return done.stdout.decode(errors="replace")

    async def _restart_bluetooth(self, *, was_set_up: bool) -> None:
        """The controller is a new one. A stack that had set the old one up is switched off and on; one that
        only ever talked to nobody is switched on, in case it gave up."""
        manager = "cmd bluetooth_manager"
        if was_set_up:
            self.logger.info("Bluetooth: a new controller: the device's Bluetooth is switched off and on")
            await self._adb("shell", f"{manager} disable; {manager} wait-for-state:STATE_OFF; {manager} enable")
        else:
            await self._adb("shell", f"{manager} enable")

    @asynccontextmanager
    async def serving(self):
        async with super().serving(), anyio.create_task_group() as tasks:
            self._tasks = tasks
            try:
                await tasks.start(self._serve_device)
                if self.autostart:
                    await self.start()
                yield
            finally:
                self._tasks = self._dialing = None
                tasks.cancel_scope.cancel()

    @export
    async def start(self) -> None:
        """Look for the path to the far controller, and again whenever it goes away. Twice does nothing."""
        if self._dialing is not None:
            return
        if self._tasks is None:
            raise RuntimeError("the driver is not being served")
        self._dialing = anyio.CancelScope()
        self._tasks.start_soon(self._dial, self._dialing)

    @export
    async def stop(self) -> None:
        """Leave the far controller: the device stays up, with a Bluetooth that hears nothing, until `start`."""
        scope, self._dialing = self._dialing, None
        if scope is not None:
            scope.cancel()

    @export
    async def status(self) -> dict:
        """``attached``: the device's connector is here (the device is powered on). ``peer``: the path to the
        far controller is open. ``paths``: how many there have been; ``restarts``: how often the device's
        Bluetooth was switched off and on for a new one. ``heard`` counts the packets the device said,
        ``carried`` those of them a controller was given, ``dropped`` those said to nobody, ``sent`` what the
        device was told. ``last_error``: why there is no path."""
        return {
            "running": self._dialing is not None,
            "listening": self._listening,
            "attached": self._device is not None,
            "peer": self._joined,
            "paths": self._paths,
            "restarts": self._restarts,
            "heard": self._heard,
            "carried": self._carried,
            "dropped": self._dropped,
            "sent": self._sent,
            "last_error": self._error,
        }

    @export
    async def declared_ports(self) -> list[dict]:
        return port_reports(self)
