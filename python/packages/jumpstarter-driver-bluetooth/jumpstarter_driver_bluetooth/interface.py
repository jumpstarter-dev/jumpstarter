from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import ClassVar

from .common import PROVIDED_BY
from jumpstarter.driver import Driver, Port, PortDirection, export


class BluetoothInterface(metaclass=ABCMeta):
    """A device's Bluetooth, whatever stands behind it: a virtual radio or a real controller, a link to another
    device of the lease or the device's own stack.

    Every Bluetooth driver has all of these methods, so that a suite is written once. What a driver can do is
    in ``capabilities`` (in `get_bluetooth_state`); what it cannot do raises ``NotImplementedError`` and says
    what would provide it.

    A driver that joins the device's radio to another member of the lease declares a port (JEP-0016). It does
    not join it to anything: the forwards of a lease do. Whatever has a deadline that a network cannot keep (a
    radio's timing, a pairing) is the driver's to handle locally, beside the radio.
    """

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_bluetooth.client.BluetoothClient"

    @abstractmethod
    def start(self) -> None:
        """Bring the local end up. Starting it twice does nothing."""

    @abstractmethod
    def stop(self) -> None:
        """Take the local end down: the radio is as it was without it."""

    @abstractmethod
    def status(self) -> dict:
        """At least ``running``, ``attached`` (the local end has the radio) and ``peer`` (a far end is joined)."""

    @abstractmethod
    def get_bluetooth_state(self) -> dict:
        """What is known of the device's Bluetooth, in one shape for every driver:

        - ``capabilities``: what this driver can do (see `jumpstarter_driver_bluetooth.common.PROVIDED_BY`);
        - ``adapter``: ``enabled``, ``state``, ``name``, ``address``, ``class_of_device``;
        - ``connections``: the links the device has now, each with what is known of the other end;
        - ``bonds``: the devices it is paired with;
        - ``link``: the driver's own ``status()``, when it joins the radio to another member.

        What a driver cannot know is None (or left out of ``adapter``), not made up.
        """

    @abstractmethod
    def declared_ports(self) -> list[dict]:
        """The ports the driver declares: name, direction, protocol, attachment and transport."""

    # Inspection: what was said on the device's HCI.

    @abstractmethod
    def start_capture(self) -> dict:
        """Start recording what the device's Bluetooth says. Gives back `capture_status`."""

    @abstractmethod
    def stop_capture(self, restore: bool = False) -> dict:
        """Stop recording, and keep what was recorded for `read_capture` and `read_packets`."""

    @abstractmethod
    def capture_status(self) -> dict:
        """At least ``capturing`` and ``mode``."""

    @abstractmethod
    def read_capture(self, last: bool = False) -> AsyncGenerator[str, None]:
        """The recording as a file a standard tool opens (btsnoop), as a stream of base64 chunks."""

    @abstractmethod
    def read_packets(self, event: str = "", match: dict | None = None, since: int = 0) -> list[dict]:
        """The recorded packets, decoded, with keys left out. ``event``, ``match`` and ``since`` narrow them."""

    @abstractmethod
    def wait_for_event(self, event: str, timeout: float = 30.0, match: dict | None = None, since: int = 0) -> dict:
        """The first recorded packet of that name (and with those fields). Empty when none came in time."""

    @abstractmethod
    def should_not_have_event(self, event: str, within: float = 0.0, match: dict | None = None, since: int = 0) -> dict:
        """Empty when no packet of that name was recorded, also ``within`` seconds from now; else the packet."""


def port_reports(driver: Driver) -> list[dict]:
    """A driver's ports as they are reported at registration: without the local addresses."""
    return [port.model_dump(mode="json", exclude={"listen", "connect"}, exclude_none=True) for port in driver.ports]


def only_port(driver: Driver, *, bridged: bool = False) -> Port:
    """The one port a link driver has. ``bridged``: the driver is the port's local side itself (it overrides
    ``connect_port``), so the exporter must not be told to listen in its place."""
    if len(driver.ports) != 1:
        raise ValueError(f"{type(driver).__name__} has one port, not {len(driver.ports)}")
    port = driver.ports[0]
    if bridged and (port.direction != PortDirection.CONNECTS or port.listen is not None or not port.forwardable):
        raise ValueError(
            f"{type(driver).__name__}: port '{port.name}' is the driver's own bridge: a 'connects' socket "
            "stream port without 'listen'"
        )
    return port


def hci_transport(transport: str | None, host: str, port: int) -> str:
    """The radio's HCI port as a bumble transport. ``host`` and ``port`` are short for the usual one, H4 over
    TCP; ``transport`` is anything else bumble opens (``hci-socket:0``, ``usb:0``, ``serial:/dev/ttyUSB0``)."""
    return transport or f"tcp-client:{host}:{port}"


def tcp_address(transport: str) -> tuple[str, int]:
    """Where a ``tcp-client:<host>:<port>`` transport connects. No other transport is one TCP stream."""
    kind, _, address = transport.partition(":")
    host, _, port = address.rpartition(":")
    if kind != "tcp-client" or not host or not port.isdigit():
        raise ValueError(f"'{transport}' is not a TCP HCI port (tcp-client:<host>:<port>)")
    return host, int(port)


@dataclass(kw_only=True)
class BluetoothDriver(BluetoothInterface, Driver):
    """What every Bluetooth driver starts from: the whole interface, with nothing that it can do yet.

    A driver overrides what it can do and names it in `CAPABILITIES`. The rest stays here, and says so when
    it is called.
    """

    # What this driver can do: names of `jumpstarter_driver_bluetooth.common.PROVIDED_BY`.
    CAPABILITIES: ClassVar[tuple[str, ...]] = ()

    def capabilities(self) -> list[str]:
        return list(self.CAPABILITIES)

    def _unsupported(self, capability: str) -> NotImplementedError:
        return NotImplementedError(
            f"'{capability}' is not supported by this driver ({type(self).__name__}): {PROVIDED_BY[capability]}"
        )

    def _known(self) -> dict:
        """What this driver knows of the adapter, the connections and the bonds. Nothing, unless it says."""
        return {}

    @export
    async def start(self) -> None:
        """Nothing to start."""

    @export
    async def stop(self) -> None:
        """Nothing to stop."""

    @export
    async def get_bluetooth_state(self) -> dict:
        known = self._known()
        return {
            "capabilities": self.capabilities(),
            "adapter": known.get("adapter"),
            "connections": known.get("connections"),
            "bonds": known.get("bonds"),
            "link": await self.status() if "link" in self.capabilities() else None,  # ty: ignore[invalid-await]
        }

    @export
    async def declared_ports(self) -> list[dict]:
        return port_reports(self)

    # A link that is one half of a bridge.

    @export
    async def identity(self) -> dict:
        raise self._unsupported("identity")

    @export
    async def set_identity(self, name: str = "", address: str = "", class_of_device: int = -1) -> dict:
        raise self._unsupported("identity")

    @export
    async def peer(self) -> dict:
        raise self._unsupported("identity")

    # The device's own stack.

    @export
    async def set_enabled(self, on: bool = True) -> dict:
        raise self._unsupported("enable")

    @export
    async def factory_reset(self) -> dict:
        raise self._unsupported("bonds")

    # Inspection.

    @export
    async def start_capture(self) -> dict:
        raise self._unsupported("capture")

    @export
    async def stop_capture(self, restore: bool = False) -> dict:
        raise self._unsupported("capture")

    @export
    async def capture_status(self) -> dict:
        raise self._unsupported("capture")

    @export
    async def read_capture(self, last: bool = False) -> AsyncGenerator[str, None]:
        raise self._unsupported("capture")
        yield ""  # (a streaming call: what is not supported is said when the stream is read)

    @export
    async def read_packets(self, event: str = "", match: dict | None = None, since: int = 0) -> list[dict]:
        raise self._unsupported("packets")

    @export
    async def wait_for_event(
        self, event: str, timeout: float = 30.0, match: dict | None = None, since: int = 0
    ) -> dict:
        raise self._unsupported("packets")

    @export
    async def should_not_have_event(
        self, event: str, within: float = 0.0, match: dict | None = None, since: int = 0
    ) -> dict:
        raise self._unsupported("packets")
