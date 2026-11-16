"""The exported driver: device lifecycle, connections, and playback commands.

The Bluetooth detail lives next door. `a2dp` owns the stream and the SBC
wire format, `avrcp` owns what the DUT displays, and `player` owns what the
peer is playing. This module wires those together and exposes them.
"""

import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, assert_never

import anyio
from anyio import to_thread
from anyio.abc import TaskGroup
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

from .a2dp import A2dpSession, Track
from .avrcp import PeerDelegate, PeerProtocol, Reporter, parse_metadata
from .errors import BtPeerError, TrackRejected
from .media import MAX_TRACK_BYTES
from .player import Idle, Loaded, Paused, Player, Playing
from jumpstarter.driver import Driver, export

__all__ = ["AutoAcceptDelegate", "BtPeer", "BtPeerError"]

# How long each step of shutting the peer down may take: powering the
# controller off, then closing its transport. bumble sends HCI commands with
# no timeout of its own, so a wedged adapter would otherwise hold whoever is
# stopping the peer, the end of the session included, forever.
STOP_STEP_TIMEOUT = 5.0
# How long the end of the session waits for the peer to stop. Powering down
# bounds itself; this also covers waiting for a start or stop in flight.
SESSION_STOP_TIMEOUT = 3 * STOP_STEP_TIMEOUT


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


@dataclass(eq=False)
class Running:
    """Everything that exists only between start_peer and stop_peer.

    One object rather than a field each, so the peer is either running with
    all of it or stopped with none of it: there is no half-started
    combination for a command to trip over, and stopping drops it in one
    assignment. Handlers registered on the device are bound to the Running
    they were registered for, so what they record lands in it rather than in
    whatever the driver holds by the time they fire.
    """

    device: Device
    transport: Any
    avdtp_listener: Listener
    avrcp: PeerProtocol
    delegate: PeerDelegate
    player: Player
    connections: dict[int, Connection] = field(default_factory=dict)
    # Comes and goes with the DUT's AVDTP channel while the peer runs.
    session: A2dpSession | None = None


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

    _running: Running | None = field(default=None, init=False, repr=False)
    # Serializes start_peer and stop_peer, which both await before they have
    # finished changing `_running`.
    _lifecycle: anyio.Lock = field(default_factory=anyio.Lock, init=False, repr=False)
    # Opened by serving(), so the work device events start has an owner.
    _tasks: TaskGroup = field(init=False, repr=False)
    _events: deque = field(default_factory=lambda: deque(maxlen=1000), init=False, repr=False)

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_bt_peer.client.BtPeerClient"

    @asynccontextmanager
    async def serving(self):
        """Own the work device events start, and stop the peer as the session ends.

        A lease can end without anyone calling stop_peer. The next session
        builds a driver of its own, so a peer left running here would keep the
        adapter powered and its transport open with nothing left to close them,
        and the next lease's start_peer would find the adapter taken.
        """
        async with super().serving():
            try:
                async with anyio.create_task_group() as tasks:
                    self._tasks = tasks
                    yield
            finally:
                # This runs cancelled, so it must be shielded to run at all.
                # The deadline sits on the shield itself: one outside it would
                # be shielded out along with the session's cancellation.
                with anyio.move_on_after(SESSION_STOP_TIMEOUT, shield=True) as stopping:
                    await self.stop_peer()
                if stopping.cancelled_caught:
                    self.logger.warning("The peer did not stop within %ss of the session ending", SESSION_STOP_TIMEOUT)

    def _spawn(self, what: str, work: Callable[..., Awaitable[object]], *args: object) -> None:
        """Run the work a device event asked for, owned by the session.

        Nobody awaits a device event, so a failure is logged here. Raised into
        the task group, it would end serving(), and with it every other
        event's work and the peer itself.
        """

        async def run() -> None:
            try:
                await work(*args)
            except Exception:
                self.logger.exception("%s failed", what)

        self._tasks.start_soon(run)

    def _require_running(self) -> Running:
        if self._running is None:
            raise BtPeerError("peer not running — call start_peer first")
        return self._running

    def _emit(self, event_type: str, data: dict | None = None) -> None:
        entry = {
            "ts": time.time(),
            "type": event_type,
            **(data or {}),
        }
        self._events.append(entry)
        self.logger.info("event: %s %s", event_type, json.dumps(data or {}))

    def _on_connection(self, running: Running, connection: Connection) -> None:
        running.connections[connection.handle] = connection
        self._emit("connection", {
            "handle": connection.handle,
            "address": str(connection.peer_address),
            "transport": "BR/EDR" if connection.transport == BT_BR_EDR_TRANSPORT else "LE",
            "role": "peripheral" if connection.role == HCI_PERIPHERAL_ROLE else "central",
            "encrypted": connection.is_encrypted,
        })

        def on_disconnect(reason):
            running.connections.pop(connection.handle, None)
            self._emit("disconnection", {
                "handle": connection.handle,
                "address": str(connection.peer_address),
                "reason": reason,
            })

        connection.on("disconnection", on_disconnect)

    def _on_avdtp_connection(self, running: Running, protocol) -> None:
        session = A2dpSession(
            protocol,
            get_track=lambda: running.player.track,
            on_exhausted=running.player.on_track_exhausted,
            emit=self._emit,
        )
        running.session = session
        self._emit("avdtp_connected", {})

        channel = getattr(protocol, "l2cap_channel", None)
        if channel is not None:
            # Without this the session outlives its channel, and the next
            # play() operates on a protocol that is no longer connected.
            channel.on(ClassicChannel.EVENT_CLOSE, lambda: self._on_avdtp_close(running, session))

        # play() may have been called in the window between the DUT's ACL
        # connection and its AVDTP channel, in which case it had no session
        # to start a stream on and queued the track. Pick it up now, otherwise
        # it sits there silently until something calls play again. Or play()
        # may be changing playback right now, against a session read before
        # this one existed; what it ends up with is not known until it
        # finishes, so the resume waits for it and decides then. A paused
        # track stays paused: resuming it here would restart audio the caller
        # deliberately stopped.
        player = running.player
        if isinstance(player.state, Playing) or player.changing:
            self._spawn("Starting the queued track", self._resume_queued_track, player)

    def _on_avdtp_close(self, running: Running, session: A2dpSession) -> None:
        # Bound to the session it was registered for: a DUT that reconnects
        # before the old channel's CLOSE is delivered must not lose the new
        # session to the old channel's late event.
        if running.session is session:
            running.session = None
        self._emit("avdtp_disconnected", {})

    async def _resume_queued_track(self, player: Player) -> None:
        """Start a track that was queued before the DUT's AVDTP channel existed.

        Re-affirming the state it is already in configures the stream, checks
        the track against what the sink agreed, and starts it, all under the
        player's lock and against whichever session is current by then. A
        DUT that reconnects meanwhile is handled by the same call: there is no
        verdict from an older session to apply to the newer one.
        """
        await player.settled()
        before = player.state
        if not isinstance(before, Playing):
            return

        try:
            state = await player.transition(before, before)
        except TrackRejected as exc:
            # Queued against the advertised range, the sink turned out
            # narrower. Leaving it Playing would hand rejected audio to the pump
            # the moment the DUT sends START itself. If playback has moved on
            # meanwhile, the track is no longer ours to drop.
            if await player.transition(before, Idle()) is not None:
                self._emit("stream_resume_failed", {"error": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            # The track is fine; the stream would not start. It stays queued
            # for the next START or play().
            self._emit("stream_resume_failed", {"error": str(exc)})
            return

        if state is None:
            # Playback changed before this got the lock, or the track ran out
            # while the stream was starting: it is no longer the one to start
            # or announce.
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
            self._spawn("The DUT's PAUSE", self.pause)
        elif name == "STOP":
            self._spawn("The DUT's STOP", self.stop_playback)
        elif name == "PLAY":
            self._spawn("The DUT's PLAY", self.resume)

    @export
    async def start_peer(self, config_json: str = "{}") -> str:
        """Start the Bluetooth peer device.

        config_json fields:
          name: device name (default: "Bumble-Phone")
          classic_enabled: enable BR/EDR (default: true)
          class_of_device: CoD integer (default: smartphone with audio/telephony)

        Returns JSON with assigned address.
        """
        async with self._lifecycle:
            if self._running is not None:
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
            self.logger.info("opening HCI transport: %s", self.transport)
            transport = await open_transport(self.transport)
            device = None
            try:
                device, running = self._build(transport, config, name)
                await device.power_on()
                if config.get("classic_enabled", True):
                    await device.set_discoverable(True)
                    await device.set_connectable(True)
            except BaseException:
                # Cancellation included: a client that gives up on start_peer
                # must not leave the adapter held by a peer nobody can stop.
                # The error that ended the start is the one the caller needs.
                if (error := await self._power_down(device, transport)) is not None:
                    self.logger.warning("Cleaning up after a failed start failed: %s", error)
                raise

            # Published only once it is whole, so nothing sees a peer that is
            # half started, and a failed start leaves nothing to undo.
            self._running = running

        address = str(device.public_address)
        self._emit("peer_started", {"address": address, "name": name})

        return json.dumps({"address": address, "name": name})

    def _build(self, transport, config: dict, name: str) -> tuple[Device, Running]:
        """Assemble the device and the profiles it serves, without starting it."""
        classic_enabled = config.get("classic_enabled", True)
        cod = config.get("class_of_device", None)

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
            controller_source=transport.source,
            controller_sink=transport.sink,
        )
        device = Device(config=device_config, host=host)

        device.pairing_config_factory = lambda connection: PairingConfig(
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
        device.sdp_service_records = {
            audio_source_handle: make_audio_source_service_sdp_records(audio_source_handle),
            avrcp_target_handle: TargetServiceSdpRecord(
                avrcp_target_handle
            ).to_service_attributes(),
        }

        listener = Listener.for_device(device)
        delegate = PeerDelegate(on_key=self._on_avrcp_key, now_playing=lambda: running.player.now_playing())
        avrcp = PeerProtocol(delegate)
        avrcp.listen(device)
        running = Running(
            device=device,
            transport=transport,
            avdtp_listener=listener,
            avrcp=avrcp,
            delegate=delegate,
            player=Player(
                get_session=lambda: running.session,
                reporter=Reporter(delegate, avrcp, self.logger),
                emit=self._emit,
            ),
        )
        listener.on("connection", lambda protocol: self._on_avdtp_connection(running, protocol))
        device.on("connection", lambda connection: self._on_connection(running, connection))
        return device, running

    @export
    async def stop_peer(self) -> str:
        """Stop the Bluetooth peer device and clean up."""
        async with self._lifecycle:
            running = self._running
            if running is None:
                return json.dumps({"status": "not_running"})

            self._running = None
            # A command still in flight drives this run's player, which nothing
            # reads any more. Without a session it cannot reach the stream of
            # a device that is about to power off.
            running.session = None
            self._events.clear()
            error = await self._power_down(running.device, running.transport)

        self._emit("peer_stopped", {})

        if error is not None:
            raise BtPeerError(f"failed to stop peer: {error!r}") from error

        return json.dumps({"status": "stopped"})

    async def _power_down(self, device: Device | None, transport) -> Exception | None:
        """Power the device off and close its transport, returning the first failure.

        Shielded, because it runs on the way out of a cancelled start and at the
        end of the session, where skipping it strands the adapter. Each step is
        bounded, and the transport is closed even when powering off failed or
        timed out: closing it is what lets go of the adapter.
        """
        first_error: Exception | None = None
        with anyio.CancelScope(shield=True):
            if device is not None:  # None when the start failed building it
                try:
                    with anyio.fail_after(STOP_STEP_TIMEOUT):
                        await device.power_off()
                except Exception as exc:  # noqa: BLE001 - returned once the transport is closed
                    first_error = exc
            try:
                with anyio.fail_after(STOP_STEP_TIMEOUT):
                    await transport.close()
            except Exception as exc:  # noqa: BLE001
                first_error = first_error or exc
        return first_error

    @export
    async def wait_connection(self, timeout: int = 30) -> str:
        """Wait for an incoming BR/EDR or LE connection.

        Returns JSON with connection details (handle, address, transport, role, encrypted).
        Raises on timeout.
        """
        running = self._require_running()

        if running.connections:
            conn = next(iter(running.connections.values()))
            return json.dumps(_describe_connection(conn))

        event = anyio.Event()
        result_holder: list[Connection] = []

        def on_connect(connection: Connection):
            result_holder.append(connection)
            event.set()

        running.device.on("connection", on_connect)
        try:
            with anyio.fail_after(timeout):
                await event.wait()
        except TimeoutError:
            raise BtPeerError(f"no connection within {timeout}s") from None
        finally:
            running.device.remove_listener("connection", on_connect)

        return json.dumps(_describe_connection(result_holder[0]))

    @export
    async def wait_disconnection(self, timeout: int = 30) -> str:
        """Wait for any active connection to disconnect.

        Returns JSON with disconnection details (handle, address, reason).
        Raises on timeout if no disconnection occurs.
        """
        running = self._require_running()
        if not running.connections:
            return json.dumps({"status": "no_connections"})

        event = anyio.Event()
        result_holder: list[dict] = []
        registered: list[tuple[Connection, Any]] = []

        for conn in list(running.connections.values()):
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
        running = self._require_running()

        handle = int(handle)
        conn = running.connections.get(handle)
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
        running = self._require_running()

        from bumble.hci import Address as HciAddress

        target = HciAddress(
            address,
            address_type=HciAddress.PUBLIC_DEVICE_ADDRESS,
        )
        self.logger.info("connecting to %s", target)
        try:
            with anyio.fail_after(timeout):
                connection = await running.device.connect(
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
        return str(self._require_running().device.public_address)

    @export
    def get_connections(self) -> str:
        """Return currently active connections as JSON array."""
        # Not an error when stopped, unlike the other commands: a stopped
        # peer has no connections, which is an answer.
        connections = self._running.connections if self._running else {}
        return json.dumps([
            {
                "handle": conn.handle,
                "address": str(conn.peer_address),
                "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
                "encrypted": conn.is_encrypted,
            }
            for conn in connections.values()
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
        running = self._require_running()

        data = await self._receive_upload(src)
        metadata = parse_metadata(metadata_json, default_title=name)
        # Parsed before anything is configured: an upload that is not SBC at
        # all should not send SET_CONFIGURATION to the DUT. In a worker thread,
        # because a long track takes seconds to parse and the exporter's other
        # drivers share this event loop.
        track = await to_thread.run_sync(Track, name, data, bool(loop))

        # The transition checks the track against the bitpool the sink agreed
        # before it touches the state, so an unusable upload leaves whatever
        # is playing alone. The state is read as late as possible: the upload
        # above can take a while, and anything that happened during it is not
        # a conflict.
        player = running.player
        stream_state = await player.transition(player.state, Playing(track, metadata))
        if self._running is not running:
            # stop_peer ran meanwhile. The track went to the stopped run's
            # player, which nothing will play.
            raise BtPeerError("the peer was stopped while the track was being prepared")
        if stream_state is None:
            raise BtPeerError("playback changed while the track was being prepared")

        info = {
            "name": name,
            "bytes": len(data),
            "loop": bool(loop),
            "bitpool": track.frames[0].bitpool,
            **metadata.as_dict(),
            **stream_state,
        }
        self._emit("track_started", info)
        return json.dumps(info)

    @export
    async def pause(self) -> str:
        """Suspend the stream, keeping the track and its position."""
        player = self._require_running().player
        match player.state:
            case Idle():
                return json.dumps({"status": "not_playing"})
            case Paused(track=track):
                return json.dumps({"status": "paused", "name": track.name})
            case Playing(track=track, metadata=metadata) as state:
                result = await player.transition(state, Paused(track, metadata))
                if result is None:
                    return json.dumps({"status": "not_playing"})
                self._emit("track_paused", {"name": track.name, **result})
                return json.dumps({"status": "paused", "name": track.name})
            case _:  # pragma: no cover
                assert_never(player.state)

    @export
    async def resume(self) -> str:
        """Restart a stream suspended by pause()."""
        player = self._require_running().player
        state = player.state
        if not isinstance(state, Loaded):
            return json.dumps({"status": "not_playing"})

        result = await player.transition(state, Playing(state.track, state.metadata))
        if result is None:
            return json.dumps({"status": "not_playing"})
        self._emit("track_resumed", {"name": state.track.name, **result})
        return json.dumps({"status": "resumed", "name": state.track.name, **result})

    @export
    async def stop_playback(self) -> str:
        """Stop the current track and suspend the stream."""
        player = self._require_running().player
        state = player.state
        if not isinstance(state, Loaded):
            return json.dumps({"status": "not_playing"})

        name = state.track.name
        result = await player.transition(state, Idle())
        if result is None:
            return json.dumps({"status": "not_playing"})
        self._emit("track_stopped", {"name": name, **result})
        return json.dumps({"status": "stopped", "name": name})

    @export
    def playback_status(self) -> str:
        """Return what the peer is currently streaming."""
        # Not an error when stopped, like get_connections: a stopped peer
        # plays nothing, which is an answer.
        running = self._running
        if running is None:
            return json.dumps({"playing": False, "paused": False, "streaming": False})
        return json.dumps(running.player.describe())


def _describe_connection(conn: Connection) -> dict[str, Any]:
    return {
        "handle": conn.handle,
        "address": str(conn.peer_address),
        "transport": "BR/EDR" if conn.transport == BT_BR_EDR_TRANSPORT else "LE",
        "role": "peripheral" if conn.role == HCI_PERIPHERAL_ROLE else "central",
        "encrypted": conn.is_encrypted,
    }
