import contextlib
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import anyio
from bumble.a2dp import (
    A2DP_SBC_CODEC_TYPE,
    SbcFrame,
    SbcMediaCodecInformation,
    SbcPacketSource,
    SbcParser,
    make_audio_source_service_sdp_records,
)
from bumble.avdtp import (
    AVDTP_AUDIO_MEDIA_TYPE,
    Listener,
    MediaCodecCapabilities,
    MediaPacketPump,
    State,
)
from bumble.avrcp import Delegate as AvrcpDelegate
from bumble.avrcp import EventId, PlayStatus, TargetServiceSdpRecord
from bumble.avrcp import Protocol as AvrcpProtocol
from bumble.core import BT_BR_EDR_TRANSPORT, ClassOfDevice, DeviceClass
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import HCI_PERIPHERAL_ROLE
from bumble.host import Host
from bumble.l2cap import ClassicChannel
from bumble.pairing import PairingConfig, PairingDelegate
from bumble.rtp import MediaPacket
from bumble.transport import open_transport
from bumble.utils import AsyncRunner

from .media import (
    MAX_TRACK_BYTES,
    SBC_ALLOCATION_METHOD,
    SBC_BLOCK_COUNT,
    SBC_CHANNEL_MODE,
    SBC_MAX_BITPOOL,
    SBC_MIN_BITPOOL,
    SBC_SAMPLING_FREQUENCY,
    SBC_SUBBAND_COUNT,
    describe_sbc_format,
)
from jumpstarter.driver import Driver, export

# Duration of a filler packet emitted while no track is playing.
SILENCE_PACKET_DURATION = 0.02

# Fallback when the AVDTP transport channel does not report a usable peer MTU.
DEFAULT_RTP_MTU = 672

# The L2CAP minimum; anything smaller cannot hold an RTP header plus a frame.
MIN_RTP_MTU = 48


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


class _AvrcpDelegate(AvrcpDelegate):
    """Reports the peer's transport state to the DUT's AVRCP controller.

    Without an AVRCP target the DUT sees an A2DP source that never claims to
    be playing, so it leaves the sink inactive and renders nothing even though
    packets are arriving. Key presses are forwarded to `on_key` so the head
    unit's transport controls reach the driver.
    """

    def __init__(self, on_key=None):
        super().__init__(
            supported_events=[
                EventId.PLAYBACK_STATUS_CHANGED,
                EventId.TRACK_CHANGED,
            ]
        )
        self._on_key = on_key

    async def on_key_event(self, key, pressed: bool, data: bytes) -> None:
        if self._on_key is not None:
            self._on_key(key, pressed)


class _ByteReader:
    """An async ``read(n)`` over an in-memory buffer, as bumble's parsers expect."""

    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    async def read(self, byte_count: int) -> bytes:
        chunk = self.data[self.position : self.position + byte_count]
        self.position += len(chunk)
        return chunk


class _Track:
    """An uploaded SBC file being streamed to the DUT.

    The whole file is held in memory so that looping can rewind without
    re-uploading, and so the packet source never blocks the event loop on disk.

    The track does not own an MTU: that belongs to the media transport
    channel, which may not exist yet when a track is queued.
    """

    def __init__(self, name: str, data: bytes, loop: bool = False):
        self.name = name
        self.data = data
        self.loop = loop
        self.last_timestamp = 0.0
        self._reader = _ByteReader(data)
        self._packets = None

    @property
    def position(self) -> int:
        return self._reader.position

    def packets(self, mtu: int):
        """The packet generator, built on first use so it can size to the MTU.

        SbcPacketSource.packets is a property that builds a fresh generator on
        every access, so it has to be held rather than re-read each time.
        """
        if self._packets is None:
            self._packets = SbcPacketSource(self._reader.read, mtu).packets
        return self._packets

    def rewind(self) -> None:
        self._reader = _ByteReader(self.data)
        self.last_timestamp = 0.0
        self._packets = None


def _is_streaming(stream) -> bool:
    return stream is not None and stream.state == State.STREAMING


def _sbc_capabilities() -> MediaCodecCapabilities:
    """The single SBC configuration the peer advertises.

    The peer is the AVDTP acceptor, so the sink picks a configuration out of
    whatever is offered. Advertising exactly one means play() can reject
    mismatched audio up front instead of letting it reach the speakers as
    noise.
    """
    S = SbcMediaCodecInformation
    return MediaCodecCapabilities(
        media_type=AVDTP_AUDIO_MEDIA_TYPE,
        media_codec_type=A2DP_SBC_CODEC_TYPE,
        media_codec_information=S(
            sampling_frequency=S.SamplingFrequency.SF_44100,
            channel_mode=S.ChannelMode.STEREO,
            block_length=S.BlockLength.BL_16,
            subbands=S.Subbands.S_8,
            allocation_method=S.AllocationMethod.LOUDNESS,
            minimum_bitpool_value=SBC_MIN_BITPOOL,
            maximum_bitpool_value=SBC_MAX_BITPOOL,
        ),
    )


async def _no_packets():
    """Stands in until START supplies the real source."""
    return
    yield  # pragma: no cover


class _StartPump(MediaPacketPump):
    """Builds the packet stream at START, once the RTP channel exists.

    The MTU that bounds an RTP payload belongs to the media transport
    channel, which is only created when the stream opens. Taking it from the
    signalling channel at connection time reads a different channel's value.
    """

    def __init__(self, session: "_A2dpSession"):
        super().__init__(_no_packets())
        self._session = session

    async def start(self, rtp_channel) -> None:
        self.packets = self._session.packets(getattr(rtp_channel, "peer_mtu", None))
        await super().start(rtp_channel)


class _A2dpSession:
    """Everything belonging to one AVDTP connection.

    Scoped to the connection rather than the driver, so a closed channel
    takes its protocol, source and stream with it instead of leaving stale
    objects behind for the next play() to operate on.

    The track itself stays with the driver: it is playback intent, it can be
    set before any connection exists, and it should survive a reconnect.
    """

    def __init__(self, protocol, get_track, on_exhausted, emit):
        self.protocol = protocol
        self.mtu = DEFAULT_RTP_MTU
        # The sink's ceiling once discovered, which may be below what the
        # peer advertises.
        self.max_bitpool = SBC_MAX_BITPOOL
        self._get_track = get_track
        self._on_exhausted = on_exhausted
        self._emit = emit
        self.source = protocol.add_source(_sbc_capabilities(), _StartPump(self))

    # -- packets ---------------------------------------------------------

    def packets(self, mtu):
        """Start a fresh packet stream sized to the media channel's MTU."""
        self.mtu = mtu if isinstance(mtu, int) and mtu >= MIN_RTP_MTU else DEFAULT_RTP_MTU
        self._emit("rtp_channel_open", {"mtu": self.mtu})
        return self._media_source()

    def _make_packet(self, sequence_number: int, timestamp_seconds: float, payload: bytes) -> MediaPacket:
        packet = MediaPacket(
            2, 0, 0, 0, sequence_number, int(1000 * timestamp_seconds), 0, [], 96, payload
        )
        packet.timestamp_seconds = timestamp_seconds
        return packet

    async def _media_source(self):
        """Packet source backing the stream for as long as it is started.

        Emits filler while no track is set and the track's packets otherwise,
        so a track can be swapped without renegotiating. MediaPacketPump
        paces itself against packet.timestamp_seconds relative to the first
        packet it sees, so timestamps have to keep rising for the life of the
        stream. Each track's own timestamps restart at zero, so per-track
        deltas are accumulated into a stream-wide clock rather than passed
        through; otherwise the pump would see time jump backwards on a track
        change and flush the whole file at wire speed.
        """
        sequence_number = 0
        timestamp_seconds = 0.0

        while True:
            track = self._get_track()

            if track is None:
                # No sleep here: the pump already waits until each packet's
                # timestamp before writing it, so sleeping as well would make
                # real time run ahead of the stream clock by the sleep
                # overhead. That gap is invisible while idle, but the pump
                # makes it up the instant a track starts by flushing packets
                # at wire speed, which overruns the sink's buffer.
                yield self._make_packet(sequence_number, timestamp_seconds, b"\x00")
                sequence_number = (sequence_number + 1) & 0xFFFF
                timestamp_seconds += SILENCE_PACKET_DURATION
                continue

            try:
                packet = await anext(track.packets(self.mtu))
            except StopAsyncIteration:
                # Rewinds for a loop, or clears the track so the next pass
                # falls through to filler.
                self._on_exhausted(track)
                continue

            delta = packet.timestamp_seconds - track.last_timestamp
            track.last_timestamp = packet.timestamp_seconds
            if delta > 0:
                timestamp_seconds += delta

            packet.sequence_number = sequence_number
            packet.timestamp_seconds = timestamp_seconds
            packet.timestamp = int(1000 * timestamp_seconds)
            sequence_number = (sequence_number + 1) & 0xFFFF
            yield packet

    # -- stream lifecycle ------------------------------------------------

    async def _open_stream(self):
        """Return a stream ready to be started, creating one if needed.

        A stream left IDLE by a close cannot be started again, so it needs
        recreating exactly as if there had never been one.
        """
        stream = self.source.stream
        if stream is not None and stream.state != State.IDLE:
            return stream

        await self.protocol.discover_remote_endpoints()
        sink = self.protocol.find_remote_sink_by_codec(AVDTP_AUDIO_MEDIA_TYPE, A2DP_SBC_CODEC_TYPE)
        if sink is None:
            raise BtPeerError(
                "the DUT exposes no free SBC audio sink endpoint; "
                "is Bluetooth media enabled on the head unit?"
            )
        self._adopt_sink_bitpool(sink)
        return await self.protocol.create_stream(self.source, sink)

    async def prepare(self) -> None:
        """Discover the sink and configure a stream, without starting it.

        Separated from start() so an upload can be checked against the
        negotiated bitpool before any of it is transmitted.
        """
        await self._open_stream()

    async def start(self) -> dict[str, Any]:
        """Put the stream into STREAMING, initiating it if necessary.

        A2DP sources drive the stream: a phone configures, opens and starts
        it when the user hits play, and the sink renders what arrives. The
        DUT connects the signalling channel and then waits, so nothing is
        pumped until the peer initiates here.
        """
        stream = await self._open_stream()
        if stream.state == State.STREAMING:
            return {"streaming": True, "reason": "already streaming"}
        # start() opens the media transport channel itself when CONFIGURED.
        await stream.start()
        return {"streaming": True, "reason": "started by the peer"}

    async def suspend(self) -> bool:
        """Send AVDTP SUSPEND, leaving the stream OPEN and resumable."""
        stream = self.source.stream
        if stream is None or stream.state != State.STREAMING:
            return False
        await stream.stop()
        return True

    def _adopt_sink_bitpool(self, sink) -> None:
        """Narrow the advertised bitpool range to what the sink accepts.

        The sink rejects a configuration whose bitpool range is wider than
        its own, so the offer has to be clamped before SET_CONFIGURATION.
        """
        for capability in sink.capabilities:
            if not isinstance(capability, MediaCodecCapabilities):
                continue
            remote = capability.media_codec_information
            if not isinstance(remote, SbcMediaCodecInformation):
                continue
            for local_capability in self.source.capabilities:
                local = getattr(local_capability, "media_codec_information", None)
                if isinstance(local, SbcMediaCodecInformation):
                    local.minimum_bitpool_value = remote.minimum_bitpool_value
                    local.maximum_bitpool_value = remote.maximum_bitpool_value
            self.max_bitpool = remote.maximum_bitpool_value
            self._emit("sink_bitpool", {
                "minimum": remote.minimum_bitpool_value,
                "maximum": remote.maximum_bitpool_value,
            })
            return


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
    _track: _Track | None = field(default=None, init=False, repr=False)
    _session: _A2dpSession | None = field(default=None, init=False, repr=False)
    _avrcp: AvrcpProtocol | None = field(default=None, init=False, repr=False)
    _avrcp_delegate: _AvrcpDelegate | None = field(default=None, init=False, repr=False)

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
        self._session = _A2dpSession(
            protocol,
            get_track=lambda: self._track,
            on_exhausted=self._on_track_exhausted,
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
        if self._track is not None:
            AsyncRunner.spawn(self._resume_queued_track())

    def _on_avdtp_close(self) -> None:
        self._session = None
        self._emit("avdtp_disconnected", {})

    async def _resume_queued_track(self) -> None:
        queued = self._track
        try:
            state = await self._ensure_streaming()
        except Exception as exc:  # noqa: BLE001
            if self._track is queued:
                self._emit("stream_resume_failed", {"error": str(exc)})
            return
        # stop_playback() may have cleared or replaced the track while
        # endpoint discovery was in flight. Reporting PLAYING for a track
        # that is no longer current would leave the DUT showing the wrong
        # state; the stream itself is left started, sending filler.
        if self._track is not queued:
            self._emit("stream_resume_discarded", {"reason": "track changed while starting"})
            return
        if state.get("streaming"):
            self._report_playback_status(PlayStatus.PLAYING)
        self._emit("stream_resumed", state)

    async def _ensure_streaming(self) -> dict[str, Any]:
        """Start the stream on the current session, if there is one."""
        if self._session is None:
            return {"streaming": False, "reason": "no AVDTP connection from the DUT yet"}
        return await self._session.start()

    def _report_playback_status(self, status: PlayStatus) -> None:
        """Tell the DUT's AVRCP controller what the peer is doing."""
        if self._avrcp_delegate is None:
            return
        self._avrcp_delegate.playback_status = status
        if self._avrcp is None:
            return
        try:
            self._avrcp.notify_playback_status_changed(status)
        except Exception as exc:  # noqa: BLE001
            # Only a notification: a controller that never registered, or a
            # dropped AVCTP channel, must not fail the play call.
            self.logger.debug("could not notify playback status: %s", exc)

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

    def _on_track_exhausted(self, track: _Track) -> None:
        """Called by the session when the track's packets run out."""
        if track.loop:
            track.rewind()
            self._emit("track_looped", {"name": track.name})
            return
        # Only fall back to silence if play() has not already swapped in a
        # different track while this one was draining.
        if self._track is track:
            self._track = None
            self._report_playback_status(PlayStatus.STOPPED)
        self._emit("track_finished", {"name": track.name})

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

            self._avrcp_delegate = _AvrcpDelegate(on_key=self._on_avrcp_key)
            self._avrcp = AvrcpProtocol(self._avrcp_delegate)
            self._avrcp.listen(self._device)

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
        self._track = None
        self._session = None
        self._avrcp = None
        self._avrcp_delegate = None
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

    @export
    async def play(self, src: str, name: str = "track", loop: bool = False) -> str:
        """Stream an uploaded SBC file to the DUT over A2DP.

        src is a client resource handle. The audio must already be encoded in
        the single SBC configuration the peer advertises; the client's play
        command transcodes with ffmpeg to match.

        Replaces any track already playing. Returns JSON describing the track.
        """
        if self._device is None:
            raise BtPeerError("peer not running — call start_peer first")

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

        session = self._session
        if session is not None:
            # Suspend first so a track swap restarts the stream cleanly
            # rather than splicing into one already in flight.
            await session.suspend()
            # Configure before validating, so the upload is checked against
            # the bitpool the sink actually accepted rather than only the
            # wider range the peer advertises.
            await session.prepare()

        max_bitpool = session.max_bitpool if session is not None else SBC_MAX_BITPOOL
        frame = await _validate_sbc(bytes(data), max_bitpool=max_bitpool)

        previous, self._track = self._track, _Track(name=name, data=bytes(data), loop=bool(loop))

        try:
            stream_state = await self._ensure_streaming()
        except Exception:
            # Leaving the track installed would make playback_status claim to
            # be playing, and let a later AVDTP event start it unannounced.
            self._track = previous
            self._report_playback_status(PlayStatus.STOPPED)
            raise
        if stream_state.get("streaming"):
            self._report_playback_status(PlayStatus.PLAYING)

        info = {
            "name": name,
            "bytes": len(data),
            "loop": bool(loop),
            "bitpool": frame.bitpool,
            **stream_state,
        }
        self._emit("track_started", info)
        return json.dumps(info)

    @export
    async def pause(self) -> str:
        """Suspend the stream, keeping the track and its position."""
        if self._track is None:
            return json.dumps({"status": "not_playing"})
        suspended = self._session is not None and await self._session.suspend()
        self._report_playback_status(PlayStatus.PAUSED)
        self._emit("track_paused", {"name": self._track.name, "suspended": suspended})
        return json.dumps({"status": "paused", "name": self._track.name})

    @export
    async def resume(self) -> str:
        """Restart a stream suspended by pause()."""
        if self._track is None:
            return json.dumps({"status": "not_playing"})
        state = await self._ensure_streaming()
        if state.get("streaming"):
            self._report_playback_status(PlayStatus.PLAYING)
        self._emit("track_resumed", {"name": self._track.name, **state})
        return json.dumps({"status": "resumed", "name": self._track.name, **state})

    @export
    async def stop_playback(self) -> str:
        """Stop the current track and suspend the stream."""
        track = self._track
        if track is None:
            return json.dumps({"status": "not_playing"})
        self._track = None
        suspended = self._session is not None and await self._session.suspend()
        self._report_playback_status(PlayStatus.STOPPED)
        self._emit("track_stopped", {"name": track.name, "suspended": suspended})
        return json.dumps({"status": "stopped", "name": track.name})

    @export
    def playback_status(self) -> str:
        """Return what the peer is currently streaming."""
        track = self._track
        session = self._session
        stream = session.source.stream if session is not None else None
        if track is None:
            return json.dumps({"playing": False, "streaming": _is_streaming(stream)})
        return json.dumps({
            "playing": True,
            "name": track.name,
            "loop": track.loop,
            "bytes": len(track.data),
            "position": track.position,
            "streaming": _is_streaming(stream),
        })


async def _validate_sbc(data: bytes, max_bitpool: int = SBC_MAX_BITPOOL) -> SbcFrame:
    """Check every frame of an upload, returning the first.

    Every frame is checked, not just the first. SbcPacketSource concatenates
    later frames without looking at their parameters, so a file whose first
    frame matches and whose later frames do not would be transmitted under a
    configuration the sink never agreed to. Parsing is header-only, so the
    walk costs little even for a long track.
    """
    first: SbcFrame | None = None
    index = 0
    async for frame in SbcParser(_ByteReader(data).read).frames:
        _check_sbc_frame(frame, max_bitpool=max_bitpool, index=index)
        if first is None:
            first = frame
        index += 1

    if first is None:
        raise BtPeerError(
            "uploaded file is not SBC audio (no valid frame header found); "
            f"expected {describe_sbc_format()}"
        )
    return first


def _check_sbc_frame(frame: SbcFrame, max_bitpool: int = SBC_MAX_BITPOOL, index: int = 0) -> None:
    """Reject audio the DUT's sink was never offered.

    max_bitpool is the sink's ceiling once one has been negotiated, which may
    be lower than the advertised maximum.
    """
    mismatches = []
    if frame.sampling_frequency != SBC_SAMPLING_FREQUENCY:
        mismatches.append(f"sampling frequency {frame.sampling_frequency} Hz")
    if frame.channel_mode != SBC_CHANNEL_MODE:
        mismatches.append(f"channel mode {frame.channel_mode} (expected stereo)")
    if frame.block_count != SBC_BLOCK_COUNT:
        mismatches.append(f"{frame.block_count} blocks")
    if frame.subband_count != SBC_SUBBAND_COUNT:
        mismatches.append(f"{frame.subband_count} subbands")
    if frame.allocation_method != SBC_ALLOCATION_METHOD:
        mismatches.append(f"allocation method {frame.allocation_method} (expected loudness)")
    if not SBC_MIN_BITPOOL <= frame.bitpool <= max_bitpool:
        mismatches.append(f"bitpool {frame.bitpool} (sink accepts {SBC_MIN_BITPOOL}-{max_bitpool})")

    if mismatches:
        where = "SBC file" if index == 0 else f"SBC frame {index}"
        raise BtPeerError(
            f"{where} does not match the advertised stream configuration "
            f"({describe_sbc_format()}): got {', '.join(mismatches)}"
        )
