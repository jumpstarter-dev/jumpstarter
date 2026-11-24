"""Stand-ins for testing a Bluetooth bridge without a radio: bumble's in-memory air (``LocalLink``) and its
virtual controller, with the few commands added that the bridge needs and that controller does not have."""

from __future__ import annotations

import anyio
from bumble import hci
from bumble.controller import Controller
from bumble.device import Device, DeviceConfiguration
from bumble.host import Host
from bumble.keys import MemoryKeyStore
from bumble.link import LocalLink
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport.common import AsyncPipeSink, BaseSource

from .common import BluetoothIdentity

AUTHENTICATION_FAILURE = hci.HCI_ErrorCode.AUTHENTICATION_FAILURE_ERROR


class Inbox:
    """What arrived, for a test to wait for."""

    def __init__(self) -> None:
        self.items: list[bytes] = []

    def put(self, item: bytes) -> None:
        self.items.append(bytes(item))

    async def get(self) -> bytes:
        while not self.items:
            await anyio.sleep(0.01)
        return self.items.pop(0)


class VirtualController(Controller):
    """bumble's virtual controller, which can also encrypt a classic link and take a link policy."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.link_policies: dict[int, int] = {}
        self.default_link_policy: int | None = None
        self.class_of_device = 0
        # Whose key this controller is waiting for from its host: None for a link it is authenticating
        # itself, or the far controller and the key it has to match.
        self.asking: dict[hci.Address, tuple[VirtualController, bytes] | None] = {}

    def on_hci_write_class_of_device_command(self, _command):
        self.class_of_device = _command.class_of_device
        return super().on_hci_write_class_of_device_command(_command)

    def on_hci_write_default_link_policy_settings_command(self, command):
        self.default_link_policy = command.default_link_policy_settings
        return hci.HCI_StatusReturnParameters(status=hci.HCI_ErrorCode.SUCCESS)

    def on_hci_write_link_policy_settings_command(self, command):
        self.link_policies[command.connection_handle] = command.link_policy_settings
        return hci.HCI_StatusAndConnectionHandleReturnParameters(
            status=hci.HCI_ErrorCode.SUCCESS, connection_handle=command.connection_handle
        )

    # ---- a link is authenticated with the key both hosts kept, as a real link manager does -----------------
    #
    # bumble's controller pairs anew every time. A real one first asks its host for the key of an earlier
    # pairing; with one, it challenges the far controller, which asks *its* host for the key. Only the same
    # key at both ends authenticates the link: a far host without it refuses ("PIN or key missing").

    def on_hci_authentication_requested_command(self, command):
        connection = self.find_classic_connection_by_handle(command.connection_handle)
        if connection is None:
            return super().on_hci_authentication_requested_command(command)
        self._send_hci_command_status(hci.HCI_COMMAND_STATUS_PENDING, command.op_code)
        self.asking[connection.peer_address] = None  # (this side started it)
        self.send_hci_packet(hci.HCI_Link_Key_Request_Event(bd_addr=connection.peer_address))

    def challenged(self, by: VirtualController, link_key: bytes) -> None:
        self.asking[by.public_address] = (by, link_key)
        self.send_hci_packet(hci.HCI_Link_Key_Request_Event(bd_addr=by.public_address))

    def authenticated(self, peer_address: hci.Address, status: int) -> None:
        if connection := self.classic_connections.get(peer_address):
            self.send_hci_packet(
                hci.HCI_Authentication_Complete_Event(status=status, connection_handle=connection.handle)
            )

    def on_hci_link_key_request_reply_command(self, command):
        challenge = self.asking.pop(command.bd_addr, None)
        far = self.link.find_classic_controller(command.bd_addr) if self.link else None
        if challenge is not None:
            by, link_key = challenge
            same = hci.HCI_ErrorCode.SUCCESS if link_key == command.link_key else AUTHENTICATION_FAILURE
            by.authenticated(self.public_address, same)
        elif isinstance(far, VirtualController):
            far.challenged(self, command.link_key)
        else:
            return super().on_hci_link_key_request_reply_command(command)
        return hci.HCI_StatusAndAddressReturnParameters(status=hci.HCI_ErrorCode.SUCCESS, bd_addr=command.bd_addr)

    def on_hci_link_key_request_negative_reply_command(self, command):
        challenge = self.asking.pop(command.bd_addr, None)
        if challenge is None:
            # this side started it and has no key: the two pair anew
            return super().on_hci_link_key_request_negative_reply_command(command)
        challenge[0].authenticated(self.public_address, hci.HCI_ErrorCode.PIN_OR_KEY_MISSING_ERROR)
        return hci.HCI_StatusAndAddressReturnParameters(status=hci.HCI_ErrorCode.SUCCESS, bd_addr=command.bd_addr)

    def encrypted(self, peer_address: hci.Address) -> None:
        if connection := self.classic_connections.get(peer_address):
            self.send_hci_packet(
                hci.HCI_Encryption_Change_Event(
                    status=hci.HCI_ErrorCode.SUCCESS,
                    connection_handle=connection.handle,
                    encryption_enabled=hci.HCI_Encryption_Change_Event.Enabled.E0_OR_AES_CCM,
                )
            )

    def on_hci_set_connection_encryption_command(self, command):
        connection = self.find_classic_connection_by_handle(command.connection_handle)
        if connection is None:
            self._send_hci_command_status(hci.HCI_ErrorCode.UNKNOWN_CONNECTION_IDENTIFIER_ERROR, command.op_code)
            return
        self._send_hci_command_status(hci.HCI_COMMAND_STATUS_PENDING, command.op_code)
        self.encrypted(connection.peer_address)
        far = self.link.find_classic_controller(connection.peer_address) if self.link else None
        if isinstance(far, VirtualController):
            far.encrypted(self.public_address)


class HciPort:
    """A virtual controller as a bumble transport: what ``open_transport`` gives for a real HCI port."""

    class Source(BaseSource):
        def __init__(self, controller: Controller) -> None:
            super().__init__()  # (makes `terminated`, the future a transport's source ends with)
            self.controller = controller

        def set_packet_sink(self, sink) -> None:
            self.controller.set_packet_sink(sink)

    def __init__(self, controller: Controller) -> None:
        self.controller = controller
        self.source = HciPort.Source(controller)
        self.sink = AsyncPipeSink(controller)
        self.closed = False

    def lose(self) -> None:
        """The radio goes away, as when its device is power cycled."""
        if not self.source.terminated.done():
            self.source.terminated.set_result(None)
            self.controller.set_packet_sink(None)

    async def close(self) -> None:
        self.closed = True
        self.controller.set_packet_sink(None)


class _Yes(PairingDelegate):
    def __init__(self) -> None:
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_AND_YES_NO_INPUT)

    async def confirm(self, auto: bool = False) -> bool:
        return True

    async def compare_numbers(self, number: int, digits: int) -> bool:
        return True

    async def accept(self) -> bool:
        return True


class Air:
    """One radio's air: a device (standing in for a phone or a head unit) and an HCI port for a bridge half."""

    def __init__(self, identity: BluetoothIdentity, bridge_address: str) -> None:
        self.identity = identity
        self.bridge_address = bridge_address
        self.link = LocalLink()
        self.bridge_controller = VirtualController("bridge", link=self.link, public_address=bridge_address)
        self.device_controller = VirtualController("device", link=self.link, public_address=identity.address)
        config = DeviceConfiguration()
        config.name = identity.name
        config.class_of_device = identity.class_of_device
        config.classic_enabled, config.le_enabled = True, False
        config.classic_sc_enabled = False
        self.device = Device(config=config, host=Host(self.device_controller, AsyncPipeSink(self.device_controller)))
        self.device.keystore = MemoryKeyStore()
        self.device.pairing_config_factory = lambda _connection: PairingConfig(
            sc=False, mitm=False, bonding=True, delegate=_Yes()
        )
        self.ports: list[HciPort] = []

    async def power_on(self) -> None:
        await self.device.power_on()
        await self.device.set_discoverable(True)
        await self.device.set_connectable(True)

    async def open_radio(self) -> HciPort:
        port = HciPort(self.bridge_controller)
        self.ports.append(port)
        return port
