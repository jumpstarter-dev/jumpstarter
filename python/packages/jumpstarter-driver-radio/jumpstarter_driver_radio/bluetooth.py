from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import anyio
from anyio.abc import TaskGroup
from bumble.transport import open_transport

from .bluetooth_bridge import PROTOCOL, BridgeHalf
from .common import BluetoothIdentity
from .driver import RadioInterface, only_port, port_reports
from .identity import AdbIdentity, source_from_config
from jumpstarter.driver import Driver, Port, PortDirection, export

STOP_TIMEOUT = 15.0


@dataclass(kw_only=True)
class BluetoothBridgeRadio(RadioInterface, Driver):
    """A Bluetooth radio that is reached through its HCI port, as one half of a bridge to another radio.

    The driver is a Bluetooth host (bumble) on the radio's HCI port. Its port, ``bluetooth``, is ``connects``:
    a forward between the ``bluetooth`` ports of two radios is a path between two halves of one bridge. In this
    radio the half is a device with the *far* device's name and class; what the device of this radio says to
    it is said to the far device by the far half, and the other way round. Pairing is per radio: each device
    pairs with the half in its own radio, which accepts.

    ``name``, ``address`` and ``class_of_device`` say what the device of *this* radio is on the air. They are
    sent to the far half, which carries them; the address is also where this half calls the device.
    """

    # What the device of this radio is on the air. A test can set these once it has asked the device
    # (``set_identity``); the address is learned by itself if the device is the one that calls.
    name: str = "Jumpstarter device"
    address: str = ""
    class_of_device: int = 0x001F00
    # The radio's HCI port (H4 over TCP) ...
    host: str = "127.0.0.1"
    port: int = 7300
    # ... or any bumble transport instead, for example ``hci-socket:0`` or ``usb:0``.
    transport: str | None = None
    # Seconds before a radio that is not there, or went away, is looked for again.
    retry: float = 2.0
    # Where to ask the device itself who it is, and go on asking: ``{adb: "127.0.0.1:6520"}`` for an Android
    # device the exporter reaches over ADB (with ``server_port``, ``adb_path``, ``interval`` if they are not the
    # usual). What it says replaces `name`, `address` and `class_of_device` above, which are what is said until
    # it answers. None: only those, and what a test says with `set_identity`.
    identity_from: dict | None = None
    # Ask a device that has linked for its name over the radio, when there is nothing else to ask.
    learn_name: bool = False
    # Seconds a call to the device may take, and seconds the device that started a pairing is kept waiting
    # for the other to agree to its own.
    call_timeout: float = 8.0
    pair_timeout: float = 25.0
    # Start with the exporter's session for a lease, so that a forward that is there finds the radio's end
    # running, and no test has to start it. False: only `start` does.
    autostart: bool = True

    _half: BridgeHalf = field(init=False, repr=False)
    _tasks: TaskGroup | None = field(init=False, default=None, repr=False)
    _scope: anyio.CancelScope | None = field(init=False, default=None, repr=False)
    _stopped: anyio.Event | None = field(init=False, default=None, repr=False)
    _source: AdbIdentity | None = field(init=False, default=None, repr=False)
    _source_state: dict = field(init=False, default_factory=dict, repr=False)
    _ask_now: anyio.Event | None = field(init=False, default=None, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_radio.client.BluetoothRadioClient"

    def __post_init__(self):
        if not self.ports:
            self.ports = [Port(name="bluetooth", direction=PortDirection.CONNECTS, protocol=PROTOCOL)]
        super().__post_init__()
        only_port(self, bridged=True)
        identity = BluetoothIdentity(name=self.name, address=self.address, class_of_device=self.class_of_device)
        self._half = BridgeHalf(
            open_radio=self._open_radio,
            identity=identity,
            logger=self.logger,
            retry=self.retry,
            call_timeout=self.call_timeout,
            pair_timeout=self.pair_timeout,
            learn_name=self.learn_name,
        )
        self._source = source_from_config(self.identity_from)
        self._source_state = {"kind": self._source.kind if self._source else "", "ok": False, "error": ""}
        self._half.on_trouble = self._ask_again

    def _transport(self) -> str:
        return self.transport or f"tcp-client:{self.host}:{self.port}"

    async def _open_radio(self):
        return await open_transport(self._transport())

    @asynccontextmanager
    async def connect_port(self, port: Port):
        """The local side of the path: this radio's half of the bridge, not a socket."""
        async with self._half.path() as stream:
            yield stream

    @asynccontextmanager
    async def serving(self):
        async with super().serving(), anyio.create_task_group() as tasks:
            self._tasks = tasks
            try:
                if self._source is not None:
                    tasks.start_soon(self._watch_device, self._source)
                if self.autostart:
                    await self.start()
                yield
            finally:
                # The session is over: the half is stopped with it. (This runs cancelled: the half's own
                # teardown is shielded and bounded.)
                self._tasks = self._scope = self._stopped = None
                tasks.cancel_scope.cancel()

    def _ask_again(self) -> None:
        if self._ask_now is not None:
            self._ask_now.set()

    async def _watch_device(self, source: AdbIdentity) -> None:
        """Ask the device who it is, from the start of the session (before the device is even powered on) to
        its end: what it says is what the far half carries, and whether its Bluetooth is on decides whether it
        is called."""
        interval = float((self.identity_from or {}).get("interval", 10.0))
        half, state = self._half, self._source_state
        while True:
            self._ask_now = anyio.Event()
            try:
                reading = await source.read()
            except Exception as error:  # noqa: BLE001 - the device is off, booting, or not there yet
                if state["ok"] or not state["error"]:
                    self.logger.info("Bluetooth: the device does not say who it is yet: %s", error)
                state.update(ok=False, error=str(error) or type(error).__name__)
                half.device_up = (
                    None  # (not known: a source that does not answer does not keep the device from being called)
                )
            else:
                if not state["ok"]:
                    self.logger.info("Bluetooth: the device says it is %r (%s)", reading.name, reading.address)
                state.update(ok=True, error="")
                if reading.on is not False and half.device_up is False:
                    half.device_came_up()  # (it may be called again at once)
                half.device_up = reading.on is not False
                half.learn(name=reading.name, address=reading.address, class_of_device=reading.class_of_device)
            with anyio.move_on_after(interval if state["ok"] else min(interval, 3.0)):
                await self._ask_now.wait()
                await anyio.sleep(1.0)  # (something went wrong with the device: give it a moment, then ask)

    async def _run(self, scope: anyio.CancelScope, stopped: anyio.Event) -> None:
        try:
            with scope:
                await self._half.run()
        finally:
            stopped.set()

    @export
    async def start(self) -> None:
        """Take the radio's HCI port and stand in it. The port is looked for until it is there; starting twice
        does nothing. The half is on the air once the far half has said whose name to carry."""
        if self._scope is not None:
            return
        if self._tasks is None:
            raise RuntimeError("the driver is not being served")
        self._scope, self._stopped = anyio.CancelScope(), anyio.Event()
        self._tasks.start_soon(self._run, self._scope, self._stopped)

    @export
    async def stop(self) -> None:
        """Leave the radio: the link is hung up and the HCI port released. The devices' bonds stay."""
        scope, stopped = self._scope, self._stopped
        self._scope = self._stopped = None
        if scope is None or stopped is None:
            return
        scope.cancel()
        with anyio.move_on_after(STOP_TIMEOUT):
            await stopped.wait()

    @export
    async def status(self) -> dict:
        """``attached``: the HCI port is open. ``peer``: the far half is on the path. ``on_air``: this half is
        in the radio under the far device's name. ``state``: ``off``, ``idle``, ``calling``, ``linked``,
        ``pairing`` or ``secure``, with ``linked``, ``calling``, ``pairing`` and ``encrypted`` as flags and
        ``opened_by`` (``device`` or ``bridge``). ``bonded``: a key is kept for the device. ``paired``: both
        devices have a secure link to the bridge. ``far``: what the far half says of its own link.
        ``last_error``: why the last call or pairing failed. ``heard`` counts what the device said and was
        passed on, ``sent`` what it was told. ``identity_source``: whether the device answers who it is."""
        return {**self._half.status(), "identity_source": dict(self._source_state)}

    @export
    async def declared_ports(self) -> list[dict]:
        return port_reports(self)

    @export
    async def identity(self) -> dict:
        """What the device of this radio is on the air, as the far half is told."""
        return self._half.identity.model_dump()

    @export
    async def set_identity(self, name: str = "", address: str = "", class_of_device: int = -1) -> dict:
        """Say what the device of this radio is on the air. What is left out stays as it is; what is said
        stays said, whatever the device itself is found to say later."""
        changes: dict = {}
        if name:
            changes["name"] = name
        if address:
            changes["address"] = address
        if class_of_device >= 0:
            changes["class_of_device"] = class_of_device
        identity = BluetoothIdentity.model_validate({**self._half.identity.model_dump(), **changes})
        self._half.set_identity(identity, pinned=set(changes))
        return identity.model_dump()

    @export
    async def peer(self) -> dict:
        """The far device, whose name this half carries: empty until the far half has said."""
        return self._half.persona.model_dump() if self._half.persona is not None else {}


@dataclass(kw_only=True)
class RootcanalBluetooth(BluetoothBridgeRadio):
    """The Bluetooth of a virtual device whose radio is rootcanal (Cuttlefish, the Android emulator's netsim):
    its HCI port, ``--hci_port``, 7300 for the first instance.

    The half joins the virtual radio as one more controller. Give the device's own address (``address``, or
    ``set_identity`` from a test): rootcanal gives every instance the same one unless it is told otherwise, and
    two devices that are to pair need two.
    """


@dataclass(kw_only=True)
class ControllerBluetooth(BluetoothBridgeRadio):
    """The Bluetooth of a physical device, through a real controller beside it: the controller's HCI port as
    an edge gateway offers it (H4 over TCP), or any bumble transport (``transport: hci-socket:0``).

    The half takes the controller for itself, and is on real air under the far device's name: the physical
    device finds it when it looks for devices to pair with. The controller's address is its own; a gateway
    that serves one host at a time is held from ``start`` to ``stop``.
    """

    # (closing a gateway's port resets the controller: it is not asked again in a hurry)
    retry: float = 5.0
    # There is no asking a physical device who it is but over the radio: once it has linked, it is asked its
    # name, and the far half carries that instead of the configured one.
    learn_name: bool = True
