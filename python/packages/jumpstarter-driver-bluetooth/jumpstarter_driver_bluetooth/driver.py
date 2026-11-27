"""The Bluetooth drivers, by the name an exporter configuration gives them."""

from __future__ import annotations

import dataclasses
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Literal

from .android import AndroidBluetooth
from .bridge import BluetoothBridge
from .controller import BluetoothControllerPort, BluetoothRemoteController
from .interface import BluetoothDriver, BluetoothInterface
from .mock import MockBluetooth
from jumpstarter.driver import Port, export

__all__ = [
    "AndroidBluetooth",
    "Bluetooth",
    "BluetoothBridge",
    "BluetoothControllerPort",
    "BluetoothDriver",
    "BluetoothInterface",
    "BluetoothRemoteController",
    "MockBluetooth",
]

LINKS: dict[str, type[BluetoothDriver]] = {
    "bridge": BluetoothBridge,
    "controller-port": BluetoothControllerPort,
    "remote-controller": BluetoothRemoteController,
}
# What of the configuration is the link's: each link takes those of these that are its own.
LINK_OPTIONS = (
    "transport", "host", "port", "name", "address", "class_of_device", "identity_from", "learn_name",
    "call_timeout", "pair_timeout", "retry", "autostart", "guest", "listen", "guest_listens", "new_controller",
)  # fmt: skip
DEVICE_OPTIONS = {"adb", "adb_path", "server_port", "timeout", "snoop_log", "poll"}


@dataclass(kw_only=True)
class Bluetooth(BluetoothDriver):
    """A device's Bluetooth as one entry of an exporter: how its radio is joined to another device of the
    lease (``link``), and the device's own stack (``device``), behind one client.

    ``link`` says which link driver this is, and the options of that driver are given beside it:

    - ``bridge``: `BluetoothBridge`, one half of a bridge between two radios;
    - ``controller-port``: `BluetoothControllerPort`, this device's controller offered to another;
    - ``remote-controller``: `BluetoothRemoteController`, this device uses the controller of another;
    - ``none``: nothing is joined.

    ``device`` says how the device itself is asked: ``{adb: "127.0.0.1:6520"}`` for an Android device
    (`AndroidBluetooth`, with its options). Left out, nothing is asked of the device's own stack. A bridge and
    a remote controller use the same ADB for what they ask of the device, unless they are told otherwise.

    The port is the link's, and is called ``bluetooth`` whatever the link: a lease template does not change
    with it.
    """

    link: Literal["bridge", "controller-port", "remote-controller", "none"] = "none"
    device: dict | None = None
    # The link's options: see the link driver. None: the link's own default.
    transport: str | None = None
    host: str | None = None
    port: int | None = None
    name: str | None = None
    address: str | None = None
    class_of_device: int | None = None
    identity_from: dict | None = None
    learn_name: bool | None = None
    call_timeout: float | None = None
    pair_timeout: float | None = None
    retry: float | None = None
    autostart: bool | None = None
    guest: str | None = None
    listen: str | None = None
    guest_listens: bool | None = None
    new_controller: str | None = None

    _link: BluetoothDriver | None = field(init=False, default=None, repr=False)
    _view: AndroidBluetooth | None = field(init=False, default=None, repr=False)

    def __post_init__(self):
        options = {name: getattr(self, name) for name in LINK_OPTIONS if getattr(self, name) is not None}
        device = dict(self.device or {})
        if unknown := set(device) - DEVICE_OPTIONS:
            raise ValueError(f"Bluetooth: device: {', '.join(sorted(unknown))} not understood")
        if device and not device.get("adb"):
            raise ValueError("Bluetooth: device: the only device there is is 'adb', its serial or host:port")
        if self.link != "none":
            driver = LINKS[self.link]
            takes = {entry.name for entry in dataclasses.fields(driver) if entry.init}
            if strange := set(options) - takes:
                raise ValueError(f"Bluetooth: {', '.join(sorted(strange))} is not an option of 'link: {self.link}'")
            if device:
                # What the link asks of the device, it asks over the same ADB as the device view.
                ask = {key: device[key] for key in ("adb", "adb_path", "server_port") if key in device}
                if self.link == "bridge":
                    options.setdefault("identity_from", ask)
                elif self.link == "remote-controller":
                    options.update({"adb_server_port" if key == "server_port" else key: ask[key] for key in ask})
            self._link = driver(**options, ports=self.ports, log_level=self.log_level)
            self.ports = list(self._link.ports)
        elif options:
            raise ValueError(f"Bluetooth: {', '.join(sorted(options))} needs a 'link' (there is 'link: none')")
        if device:
            self._view = AndroidBluetooth(**device, log_level=self.log_level)
        super().__post_init__()

    def capabilities(self) -> list[str]:
        return [*(self._link.capabilities() if self._link else []), *(self._view.capabilities() if self._view else [])]

    def _of(self, capability: str) -> BluetoothDriver:
        """The part that can do it: the link, or the device view."""
        for part in (self._link, self._view):
            if part is not None and capability in part.capabilities():
                return part
        raise self._unsupported(capability)

    # The link's port is this driver's: the exporter asks here for its local side.

    def port_address(self, port: Port) -> tuple[str, int] | None:
        return self._link.port_address(port) if self._link else None

    @asynccontextmanager
    async def connect_port(self, port: Port):
        if self._link is None:
            raise ValueError(f"port '{port.name}': there is no link")
        async with self._link.connect_port(port) as stream:
            yield stream

    async def forward_stream_closed(self, port: Port) -> None:
        if self._link is not None:
            await self._link.forward_stream_closed(port)

    @asynccontextmanager
    async def serving(self):
        async with super().serving(), AsyncExitStack() as parts:
            for part in (self._link, self._view):
                if part is not None:
                    await parts.enter_async_context(part.serving())
            yield

    def close(self):
        for part in (self._link, self._view):
            if part is not None:
                part.close()
        super().close()

    @export
    async def start(self) -> None:
        if self._link is not None:
            await self._link.start()

    @export
    async def stop(self) -> None:
        if self._link is not None:
            await self._link.stop()

    @export
    async def status(self) -> dict:
        """The link's own ``status()``. Without a link: the device view's (``attached``: the device answers)."""
        part = self._link or self._view
        if part is None:
            return {"running": True, "attached": False, "peer": False}
        return await part.status()  # ty: ignore[invalid-await]

    @export
    async def get_bluetooth_state(self) -> dict:
        """What the device itself says, where there is a device view; else what the link knows of it."""
        state = await (self._link.get_bluetooth_state() if self._link else super().get_bluetooth_state())
        if self._view is not None:
            asked = await self._view.get_bluetooth_state()
            if "error" in asked:
                # A device that does not answer: the link still says what it knows, and that is said.
                state["error"] = asked["error"]
            else:
                # What the device itself says takes the place of what the link knew, and so does what it
                # does not say: a link that kept a key is not a device that has a bond. Only whom the device
                # is linked to is the link's to say, where the device's stack does not.
                state["adapter"], state["bonds"] = asked["adapter"], asked["bonds"]
                if asked["connections"] is not None:
                    state["connections"] = asked["connections"]
        return {**state, "capabilities": self.capabilities()}

    @export
    async def identity(self) -> dict:
        return await self._of("identity").identity()

    @export
    async def set_identity(self, name: str = "", address: str = "", class_of_device: int = -1) -> dict:
        return await self._of("identity").set_identity(name, address, class_of_device)

    @export
    async def peer(self) -> dict:
        return await self._of("identity").peer()

    @export
    async def set_enabled(self, on: bool = True) -> dict:
        await self._of("enable").set_enabled(on)
        return await self.get_bluetooth_state()

    @export
    async def factory_reset(self) -> dict:
        await self._of("bonds").factory_reset()
        return await self.get_bluetooth_state()

    @export
    async def start_capture(self) -> dict:
        return await self._of("capture").start_capture()

    @export
    async def stop_capture(self, restore: bool = False) -> dict:
        return await self._of("capture").stop_capture(restore)

    @export
    async def capture_status(self) -> dict:
        return await self._of("capture").capture_status()

    @export
    async def read_capture(self, last: bool = False) -> AsyncGenerator[str, None]:
        async for chunk in self._of("capture").read_capture(last):
            yield chunk

    @export
    async def read_packets(self, event: str = "", match: dict | None = None, since: int = 0) -> list[dict]:
        return await self._of("packets").read_packets(event, match, since)

    @export
    async def wait_for_event(
        self, event: str, timeout: float = 30.0, match: dict | None = None, since: int = 0
    ) -> dict:
        return await self._of("packets").wait_for_event(event, timeout, match, since)

    @export
    async def should_not_have_event(
        self, event: str, within: float = 0.0, match: dict | None = None, since: int = 0
    ) -> dict:
        return await self._of("packets").should_not_have_event(event, within, match, since)
