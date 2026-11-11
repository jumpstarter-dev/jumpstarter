"""The exported driver: device lifecycle, connections, and playback commands.

The Bluetooth detail lives next door. `a2dp` owns the stream and the SBC
wire format, `avrcp` owns what the DUT displays, and `player` owns what the
peer is playing. This module wires those together and exposes them.
"""

import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, assert_never

import anyio
from bumble.a2dp import make_audio_source_service_sdp_records
from bumble.avdtp import Listener
from bumble.avrcp import TargetServiceSdpRecord
from bumble.core import BT_BR_EDR_TRANSPORT, ClassOfDevice, DeviceClass
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import HCI_PERIPHERAL_ROLE
from bumble.host import Host
from bumble.l2cap import ClassicChannel
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.transport import open_transport
from bumble.utils import AsyncRunner

from .a2dp import ADVERTISED, A2dpSession, Track, validate_sbc
from .avrcp import PeerDelegate, PeerProtocol, Reporter, parse_metadata
from .errors import BtPeerError
from .media import MAX_TRACK_BYTES
from .player import Idle, Loaded, Paused, Player, Playing
from jumpstarter.driver import Driver, export

__all__ = ["AutoAcceptDelegate", "BtPeer", "BtPeerError"]


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
    # Whether PLAY/PAUSE/STOP from the DUT control playback. Off by default so
    # a DUT cannot silence a track the caller explicitly started.
    follow_transport_keys: bool = False

    _device: Device | None = field(default=None, init=False, repr=False)
    _transport: Any = field(default=None, init=False, repr=False)
    _avdtp_listener: Listener | None = field(default=None, init=False, repr=False)
    _events: deque = field(default_factory=lambda: deque(maxlen=1000), init=False, repr=False)
    _connections: dict[int, Connection] = field(default_factory=dict, init=False, repr=False)
    _session: A2dpSession | None = field(default=None, init=False, repr=False)
    _avrcp: PeerProtocol | None = field(default=None, init=False, repr=False)
    _avrcp_delegate: PeerDelegate | None = field(default=None, init=False, repr=False)
    _reporter: Reporter | None = field(default=None, init=False, repr=False)
    # Built in __post_init__ rather than by a default_factory, because it
    # closes over this instance's callbacks.
    _player: Player = field(init=False, repr=False)

    def __post_init__(self):
        super().__post_init__()
        self._player = Player(
            get_session=lambda: self._session,
            get_reporter=lambda: self._reporter,
            emit=self._emit,
        )

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
        self._session = A2dpSession(
            protocol,
            get_track=lambda: self._player.track,
            on_exhausted=self._player.on_track_exhausted,
            emit=self._emit,
        )
        self._emit("avdtp_connected", {})

        channel = getattr(protocol, "l2cap_channel", None)
        if channel is not None:
            # Without this the session outlives its channel, and the next
            # play() operates on a protocol that is no longer connected.
            channel.on(ClassicChannel.EVENT_CLOSE, self._on_avdtp_close)

        # play() may have been called in the window between the DUT's ACL
        # connection and its AVDTP channel, in which case it had no session
        # to start a stream on and gave up. Pick the queued track up now,
        # otherwise it sits there silently until something calls play again.
        # A paused track stays paused: resuming it here would restart audio
        # the caller deliberately stopped.
        if isinstance(self._player.state, Playing):
            AsyncRunner.spawn(self._resume_queued_track())

    def _on_avdtp_close(self) -> None:
        self._session = None
        self._emit("avdtp_disconnected", {})

    async def _resume_queued_track(self) -> None:
        """Start a track that was queued before the DUT's AVDTP channel existed."""
        before = self._player.state
        if not isinstance(before, Playing):
            return
        session = self._session
        if session is None:
            return

        try:
            # The track was validated before any sink was known, against the
            # advertised ceiling. Now that a stream is configured, re-check it
            # rather than streaming audio the sink cannot decode.
            config = await session.prepare()
            await validate_sbc(before.track.data, config)
            # Re-affirming the state it is already in starts the stream under
            # the same lock as any other change.
            state = await self._player.transition(before, before)
        except Exception as exc:  # noqa: BLE001
            if self._player.state is before:
                self._emit("stream_resume_failed", {"error": str(exc)})
            return

        if state is None:
            # stop_playback() or another play() landed while endpoint
            # discovery was in flight. The stream is left started, sending
            # filler, but this track is no longer the one to announce.
            self._emit("stream_resume_discarded", {"reason": "playback changed while starting"})
            return
        self._emit("stream_resumed", state)

    def _on_avrcp_key(self, key, pressed: bool) -> None:
        """Handle transport keys pressed on the head unit.

        Acting on these is opt-in. A DUT may send PAUSE as soon as it
        connects -- Android does when it cannot resolve an addressed player --
        which would otherwise silence a track the caller just asked to play.
        The key is always recorded as an event so it stays observable.
        """
        name = getattr(key, "name", str(key))
        self._emit("avrcp_key", {"key": name, "pressed": pressed, "honored": self.follow_transport_keys})
        if not pressed or not self.follow_transport_keys:
            return
        if name == "PAUSE":
            AsyncRunner.spawn(self.pause())
        elif name == "STOP":
            AsyncRunner.spawn(self.stop_playback())
        elif name == "PLAY":
            AsyncRunner.spawn(self.resume())

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

            # A2DP source plus AVRCP target, the pair a phone advertises. The
            # DUT needs the AVRCP record (UUID 110C) to treat this as a real
            # media device; with the A2DP record alone it leaves its sink
            # inactive and discards the audio.
            audio_source_handle = 0x00010001
            avrcp_target_handle = 0x00010002
            self._device.sdp_service_records = {
                audio_source_handle: make_audio_source_service_sdp_records(audio_source_handle),
                avrcp_target_handle: TargetServiceSdpRecord(
                    avrcp_target_handle
                ).to_service_attributes(),
            }

            self._avdtp_listener = Listener.for_device(self._device)
            self._avdtp_listener.on("connection", self._on_avdtp_connection)

            self._avrcp_delegate = PeerDelegate(
                on_key=self._on_avrcp_key,
                now_playing=self._player.now_playing,
            )
            self._avrcp = PeerProtocol(self._avrcp_delegate)
            self._avrcp.listen(self._device)
            self._reporter = Reporter(self._avrcp_delegate, self._avrcp, self.logger)

            self._device.on("connection", self._on_connection)

            await self._device.power_on()

            if classic_enabled:
                await self._device.set_discoverable(True)
                await self._device.set_connectable(True)
        except Exception:
            if self._device is not None:
                with contextlib.suppress(Exception):
                    await self._device.power_off()
                self._device = None
            self._avdtp_listener = None
            self._avrcp = None
            self._avrcp_delegate = None
            self._reporter = None
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
        self._player.reset()
        self._session = None
        self._avrcp = None
        self._avrcp_delegate = None
        self._reporter = None
        self._connections.clear()
        self._events.clear()

        first_error: Exception | None = None
        try:
            if device is not None:
                await device.power_off()
        except Exception as exc:  # noqa: BLE001
            first_error = exc
        finally:
            try:
                if transport is not None:
                    await transport.close()
            except Exception as exc:  # pragma: no cover  # noqa: BLE001
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
            return json.dumps(_describe_connection(conn))

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

        return json.dumps(_describe_connection(result_holder[0]))

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

        result = {
            "handle": handle,
            "address": str(conn.peer_address),
            "encrypted": conn.is_encrypted,
        }
        self._emit("paired", result)
        return json.dumps(result)

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

        return json.dumps({**_describe_connection(connection), "role": "central"})

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
        return json.dumps([
            {
                "handle": conn.handle,
                "address": str(conn.peer_address),
                "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
                "encrypted": conn.is_encrypted,
            }
            for conn in self._connections.values()
        ])

    async def _receive_upload(self, src: str) -> bytes:
        """Read a client resource into memory, bounded by MAX_TRACK_BYTES."""
        data = bytearray()
        async with self.resource(src) as res:
            async for chunk in res:
                data.extend(chunk)
                if len(data) > MAX_TRACK_BYTES:
                    raise BtPeerError(
                        f"track is larger than the {MAX_TRACK_BYTES // (1024 * 1024)} MiB limit"
                    )
        if not data:
            raise BtPeerError("uploaded track is empty")
        return bytes(data)

    @export
    async def play(
        self, src: str, name: str = "track", loop: bool = False, metadata_json: str = "{}"
    ) -> str:
        """Stream an uploaded SBC file to the DUT over A2DP.

        src is a client resource handle. The audio must already be encoded in
        the single SBC configuration the peer advertises; the client's play
        command transcodes with ffmpeg to match.

        metadata_json carries what the DUT should display for the track:
        title, artist, album and duration_ms. The client fills it in from the
        source file's tags.

        Replaces any track already playing. Returns JSON describing the track.
        """
        if self._device is None:
            raise BtPeerError("peer not running — call start_peer first")

        data = await self._receive_upload(src)
        metadata = parse_metadata(metadata_json, default_title=name)

        session = self._session
        # Configure before validating, so the upload is checked against the
        # bitpool that was actually agreed rather than the wider range the
        # peer advertises. Configuring leaves a running stream alone.
        config = await session.prepare() if session is not None else ADVERTISED
        # Validate before touching the state. Doing it later would silence
        # whatever is playing even when the new upload turns out to be
        # unusable.
        frame = await validate_sbc(data, config)

        target = Playing(Track(name=name, data=data, loop=bool(loop)), metadata)
        # Read the state as late as possible: the upload above can take a
        # while, and anything that happened during it is not a conflict.
        before = self._player.state
        stream_state = await self._player.transition(before, target)
        if stream_state is None:
            raise BtPeerError("playback changed while the track was being prepared")

        info = {
            "name": name,
            "bytes": len(data),
            "loop": bool(loop),
            "bitpool": frame.bitpool,
            **metadata.as_dict(),
            **stream_state,
        }
        self._emit("track_started", info)
        return json.dumps(info)

    @export
    async def pause(self) -> str:
        """Suspend the stream, keeping the track and its position."""
        match self._player.state:
            case Idle():
                return json.dumps({"status": "not_playing"})
            case Paused(track=track):
                return json.dumps({"status": "paused", "name": track.name})
            case Playing(track=track, metadata=metadata) as state:
                result = await self._player.transition(state, Paused(track, metadata))
                if result is None:
                    return json.dumps({"status": "not_playing"})
                self._emit("track_paused", {"name": track.name, **result})
                return json.dumps({"status": "paused", "name": track.name})
            case _:  # pragma: no cover
                assert_never(self._player.state)

    @export
    async def resume(self) -> str:
        """Restart a stream suspended by pause()."""
        state = self._player.state
        if not isinstance(state, Loaded):
            return json.dumps({"status": "not_playing"})

        result = await self._player.transition(state, Playing(state.track, state.metadata))
        if result is None:
            return json.dumps({"status": "not_playing"})
        self._emit("track_resumed", {"name": state.track.name, **result})
        return json.dumps({"status": "resumed", "name": state.track.name, **result})

    @export
    async def stop_playback(self) -> str:
        """Stop the current track and suspend the stream."""
        state = self._player.state
        if not isinstance(state, Loaded):
            return json.dumps({"status": "not_playing"})

        name = state.track.name
        result = await self._player.transition(state, Idle())
        if result is None:
            return json.dumps({"status": "not_playing"})
        self._emit("track_stopped", {"name": name, **result})
        return json.dumps({"status": "stopped", "name": name})

    @export
    def playback_status(self) -> str:
        """Return what the peer is currently streaming."""
        return json.dumps(self._player.describe())


def _describe_connection(conn: Connection) -> dict[str, Any]:
    return {
        "handle": conn.handle,
        "address": str(conn.peer_address),
        "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
        "role": "peripheral" if conn.role == HCI_PERIPHERAL_ROLE else "central",
        "encrypted": conn.is_encrypted,
    }
