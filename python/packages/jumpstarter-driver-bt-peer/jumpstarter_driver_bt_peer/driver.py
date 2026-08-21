import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import anyio
from bumble.a2dp import (
    A2DP_SBC_CODEC_TYPE,
    SbcMediaCodecInformation,
    make_audio_source_service_sdp_records,
)
from bumble.avdtp import (
    AVDTP_AUDIO_MEDIA_TYPE,
    Listener,
    MediaCodecCapabilities,
    MediaPacketPump,
)
from bumble.core import BT_BR_EDR_TRANSPORT, ClassOfDevice, DeviceClass
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import HCI_PERIPHERAL_ROLE
from bumble.host import Host
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.rtp import MediaPacket
from bumble.transport import open_transport

from jumpstarter.driver import Driver, export


class BtPeerError(Exception):
    pass


class AutoAcceptDelegate(PairingDelegate):
    """Auto-accepts all pairing requests"""

    def __init__(self, io_capability=None):
        super().__init__(
            io_capability=io_capability or PairingDelegate.IoCapability.NO_OUTPUT_NO_INPUT,
            local_initiator_key_distribution=PairingDelegate.DEFAULT_KEY_DISTRIBUTION,
            local_responder_key_distribution=PairingDelegate.DEFAULT_KEY_DISTRIBUTION,
        )

    async def confirm(self, auto=False) -> bool:
        return True

    async def compare_numbers(self, number: int, digits: int) -> bool:
        return True

    async def accept(self) -> bool:
        return True

    async def get_number(self) -> int | None:
        return 0


@dataclass(kw_only=True)
class BtPeer(Driver):
    """Bluetooth peer device powered by bumble.

    Exposes a configurable Bluetooth device that can pair, connect, and
    stream A2DP audio to a DUT over BR/EDR. Transport-agnostic: works over
    TCP (rootcanal/netsim), USB dongle, serial UART, or any bumble transport.
    """

    transport: str = "tcp-client:127.0.0.1:7300"

    _device: Device | None = field(default=None, init=False, repr=False)
    _transport: Any = field(default=None, init=False, repr=False)
    _avdtp_listener: Listener | None = field(default=None, init=False, repr=False)
    _events: deque = field(default_factory=lambda: deque(maxlen=1000), init=False, repr=False)
    _connections: dict[int, Connection] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_bt_peer.client.BtPeerClient"

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        entry = {
            "ts": time.time(),
            "type": event_type,
            **(data or {}),
        }
        self._events.append(entry)
        self.logger.info("event: %s %s", event_type, json.dumps(data or {}))

    def _on_connection(self, connection: Connection) -> None:
        self._connections[connection.handle] = connection
        self._emit("connection", {
            "handle": connection.handle,
            "address": str(connection.peer_address),
            "transport": "BR/EDR" if connection.transport == BT_BR_EDR_TRANSPORT else "LE",
            "role": "peripheral" if connection.role == HCI_PERIPHERAL_ROLE else "central",
            "encrypted": connection.is_encrypted,
        })

        def on_disconnect(reason):
            self._connections.pop(connection.handle, None)
            self._emit("disconnection", {
                "handle": connection.handle,
                "address": str(connection.peer_address),
                "reason": reason,
            })

        connection.on("disconnection", on_disconnect)

    def _on_avdtp_connection(self, protocol) -> None:
        S = SbcMediaCodecInformation
        sbc_capabilities = MediaCodecCapabilities(
            media_type=AVDTP_AUDIO_MEDIA_TYPE,
            media_codec_type=A2DP_SBC_CODEC_TYPE,
            media_codec_information=S(
                sampling_frequency=S.SamplingFrequency.SF_48000 | S.SamplingFrequency.SF_44100,
                channel_mode=S.ChannelMode.JOINT_STEREO | S.ChannelMode.STEREO,
                block_length=S.BlockLength.BL_16 | S.BlockLength.BL_12 | S.BlockLength.BL_8,
                subbands=S.Subbands.S_8,
                allocation_method=S.AllocationMethod.LOUDNESS,
                minimum_bitpool_value=2,
                maximum_bitpool_value=53,
            ),
        )

        async def silence():
            """Yield timestamped RTP packets so MediaPacketPump can stream."""
            sequence_number = 0
            timestamp_seconds = 0.0
            while True:
                packet = MediaPacket(
                    2,
                    0,
                    0,
                    0,
                    sequence_number,
                    int(timestamp_seconds * 8000),
                    0,
                    [],
                    96,
                    b"\x00",
                )
                packet.timestamp_seconds = timestamp_seconds
                yield packet
                sequence_number = (sequence_number + 1) & 0xFFFF
                timestamp_seconds += 0.02
                await anyio.sleep(0.02)

        pump = MediaPacketPump(silence())
        protocol.add_source(sbc_capabilities, pump)
        self._emit("avdtp_connected", {})

    @export
    async def start_peer(self, config_json: str = "{}") -> str:
        """Start the Bluetooth peer device.

        config_json fields:
          name: device name (default: "Bumble-Phone")
          classic_enabled: enable BR/EDR (default: true)
          class_of_device: CoD integer (default: smartphone with audio/telephony)

        Returns JSON with assigned address.
        """
        if self._device is not None:
            raise BtPeerError("peer already running — call stop_peer first")

        try:
            config = json.loads(config_json) if config_json else {}
        except json.JSONDecodeError as exc:
            raise BtPeerError(f"invalid config JSON: {exc.msg}") from exc
        if not isinstance(config, dict):
            raise BtPeerError(
                f"config must be a JSON object, got {type(config).__name__}"
            )

        name = config.get("name", "Bumble-Phone")
        classic_enabled = config.get("classic_enabled", True)
        cod = config.get("class_of_device", None)

        self.logger.info("opening HCI transport: %s", self.transport)

        self._transport = await open_transport(self.transport)

        try:
            device_config = DeviceConfiguration()
            device_config.name = name
            device_config.classic_enabled = classic_enabled

            if cod is not None:
                device_config.class_of_device = cod
            else:
                device_config.class_of_device = int(ClassOfDevice(
                    ClassOfDevice.MajorServiceClasses.AUDIO | ClassOfDevice.MajorServiceClasses.TELEPHONY,
                    ClassOfDevice.MajorDeviceClass.PHONE,
                    DeviceClass.PHONE_SMARTPHONE_MINOR_DEVICE_CLASS,
                ))

            device_config.classic_sc_enabled = True
            device_config.classic_ssp_enabled = True

            host = Host(
                controller_source=self._transport.source,
                controller_sink=self._transport.sink,
            )
            self._device = Device(config=device_config, host=host)

            self._device.pairing_config_factory = lambda connection: PairingConfig(
                sc=True,
                mitm=False,
                bonding=True,
                delegate=AutoAcceptDelegate(),
            )

            service_record_handle = 0x00010001
            self._device.sdp_service_records = {
                service_record_handle: make_audio_source_service_sdp_records(
                    service_record_handle
                )
            }

            self._avdtp_listener = Listener.for_device(self._device)
            self._avdtp_listener.on("connection", self._on_avdtp_connection)

            self._device.on("connection", self._on_connection)

            await self._device.power_on()

            if classic_enabled:
                await self._device.set_discoverable(True)
                await self._device.set_connectable(True)
        except Exception:
            if self._device is not None:
                try:
                    await self._device.power_off()
                except Exception:
                    pass
                self._device = None
            self._avdtp_listener = None
            if self._transport is not None:
                await self._transport.close()
                self._transport = None
            raise

        address = str(self._device.public_address)
        self._emit("peer_started", {"address": address, "name": name})

        return json.dumps({"address": address, "name": name})

    @export
    async def stop_peer(self) -> str:
        """Stop the Bluetooth peer device and clean up."""
        if self._device is None and self._transport is None:
            return json.dumps({"status": "not_running"})

        self._avdtp_listener = None
        device = self._device
        transport = self._transport
        self._device = None
        self._transport = None
        self._connections.clear()
        self._events.clear()

        first_error: Exception | None = None
        try:
            if device is not None:
                await device.power_off()
        except Exception as exc:
            first_error = exc
        finally:
            try:
                if transport is not None:
                    await transport.close()
            except Exception as exc:
                if first_error is None:
                    first_error = exc

        self._emit("peer_stopped", {})

        if first_error is not None:
            raise BtPeerError(f"failed to stop peer: {first_error}") from first_error

        return json.dumps({"status": "stopped"})

    @export
    async def wait_connection(self, timeout: int = 30) -> str:
        """Wait for an incoming BR/EDR or LE connection.

        Returns JSON with connection details (handle, address, transport, role, encrypted).
        Raises on timeout.
        """
        if self._device is None:
            raise BtPeerError("peer not running — call start_peer first")

        if self._connections:
            conn = next(iter(self._connections.values()))
            return json.dumps({
                "handle": conn.handle,
                "address": str(conn.peer_address),
                "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
                "role": "peripheral" if conn.role == HCI_PERIPHERAL_ROLE else "central",
                "encrypted": conn.is_encrypted,
            })

        event = anyio.Event()
        result_holder: list[Connection] = []

        def on_connect(connection: Connection):
            result_holder.append(connection)
            event.set()

        self._device.on("connection", on_connect)
        try:
            with anyio.fail_after(timeout):
                await event.wait()
        except TimeoutError:
            raise BtPeerError(f"no connection within {timeout}s") from None
        finally:
            self._device.remove_listener("connection", on_connect)

        conn = result_holder[0]
        return json.dumps({
            "handle": conn.handle,
            "address": str(conn.peer_address),
            "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
            "role": "peripheral" if conn.role == HCI_PERIPHERAL_ROLE else "central",
            "encrypted": conn.is_encrypted,
        })

    @export
    async def wait_disconnection(self, timeout: int = 30) -> str:
        """Wait for any active connection to disconnect.

        Returns JSON with disconnection details (handle, address, reason).
        Raises on timeout if no disconnection occurs.
        """
        if self._device is None:
            raise BtPeerError("peer not running")
        if not self._connections:
            return json.dumps({"status": "no_connections"})

        event = anyio.Event()
        result_holder: list[dict] = []
        registered: list[tuple[Connection, Any]] = []

        for conn in list(self._connections.values()):
            def on_disconnect(reason, c=conn):
                result_holder.append({
                    "handle": c.handle,
                    "address": str(c.peer_address),
                    "reason": reason,
                })
                event.set()

            conn.on("disconnection", on_disconnect)
            registered.append((conn, on_disconnect))

        try:
            with anyio.fail_after(timeout):
                await event.wait()
        except TimeoutError:
            raise BtPeerError(f"no disconnection within {timeout}s") from None
        finally:
            for registered_conn, registered_handler in registered:
                registered_conn.remove_listener("disconnection", registered_handler)

        return json.dumps(result_holder[0])

    @export
    async def pair(self, handle: int = 0) -> str:
        """Initiate authentication + encryption on a connection.

        AutoAcceptDelegate handles numeric comparison automatically.
        """
        if self._device is None:
            raise BtPeerError("peer not running")

        handle = int(handle)
        conn = self._connections.get(handle)
        if conn is None:
            raise BtPeerError(f"no connection with handle {handle}")

        await conn.authenticate()
        await conn.encrypt()

        self._emit("paired", {
            "handle": handle,
            "address": str(conn.peer_address),
            "encrypted": conn.is_encrypted,
        })

        return json.dumps({
            "handle": handle,
            "address": str(conn.peer_address),
            "encrypted": conn.is_encrypted,
        })

    @export
    async def connect_to(self, address: str, timeout: int = 30) -> str:
        """Initiate outgoing BR/EDR connection to a remote device.

        Returns JSON with connection details.
        """
        if self._device is None:
            raise BtPeerError("peer not running - call start_peer first")

        from bumble.hci import Address as HciAddress

        target = HciAddress(
            address,
            address_type=HciAddress.PUBLIC_DEVICE_ADDRESS,
        )
        self.logger.info("connecting to %s", target)
        try:
            with anyio.fail_after(timeout):
                connection = await self._device.connect(
                    target, transport=BT_BR_EDR_TRANSPORT
                )
        except TimeoutError:
            raise BtPeerError(f"connect to {address} timed out after {timeout}s") from None

        return json.dumps({
            "handle": connection.handle,
            "address": str(connection.peer_address),
            "transport": "BR/EDR" if connection.transport == BT_BR_EDR_TRANSPORT else "LE",
            "role": "central",
            "encrypted": connection.is_encrypted,
        })

    @export
    def get_events(self, since: str = "0") -> str:
        """Get events since a timestamp. Pass "0" for all events."""
        try:
            since_ts = float(since)
        except (TypeError, ValueError) as exc:
            raise BtPeerError(f"invalid since value: {since!r}") from exc
        events = [e for e in self._events if e["ts"] > since_ts]
        return json.dumps(events)

    @export
    def get_address(self) -> str:
        """Return the peer's assigned Bluetooth address."""
        if self._device is None:
            raise BtPeerError("peer not running")
        return str(self._device.public_address)

    @export
    def get_connections(self) -> str:
        """Return currently active connections as JSON array."""
        conns = []
        for conn in self._connections.values():
            conns.append({
                "handle": conn.handle,
                "address": str(conn.peer_address),
                "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
                "encrypted": conn.is_encrypted,
            })
        return json.dumps(conns)
