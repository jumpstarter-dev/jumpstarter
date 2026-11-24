"""One half of a Bluetooth bridge between two radios that do not share the air.

Two devices that each have a radio of their own (two virtual devices, or a virtual device and a real one beside
a real controller) cannot see each other. A bridge stands in both radios at once: in each it is a device that
carries the *other* device's name and class, and what a device says to it, it says to the other device.

    phone ))) [ "Head Unit" | half ] ══ path ══ [ half | "Phone" ] ((( head unit

Each half runs beside its radio, as a Bluetooth host (bumble) on the radio's HCI port, and the two halves are
joined by one byte stream: a forward of the lease between their ``connects`` ports. Everything with a deadline
stays beside the radio: the link, its pairing and its encryption are each half's own, and are done twice, once
in each radio. What crosses the path is what is above the link: L2CAP, and so SDP, RFCOMM and the profiles.

The path's wire format is a stream of records, each a 4-byte big-endian length and that many bytes (the framing
of the Wi-Fi frame port). A record's first byte says what it is:

    1  HELLO    JSON: {"version": 1, "name": ..., "address": ..., "class_of_device": ...}: the device beside
                the sender's radio. Sent when the path opens and when the identity changes.
    2  L2CAP    a 2-byte big-endian channel id, then one L2CAP PDU the sender's device said.
    3  HANGUP   the sender's device hung up its link.
    4  BYE      the sender is being stopped.
"""

from __future__ import annotations

import json
import logging
import struct
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
from anyio import create_memory_object_stream, create_task_group
from anyio.abc import TaskGroup
from anyio.streams.stapled import StapledObjectStream
from bumble.core import PhysicalTransport
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import HCI_Write_Default_Link_Policy_Settings_Command, HCI_Write_Link_Policy_Settings_Command
from bumble.host import Host
from bumble.keys import MemoryKeyStore
from bumble.pairing import PairingConfig, PairingDelegate

from .common import BluetoothIdentity

PROTOCOL = "bt-l2cap-bridge"
VERSION = 1
HELLO, L2CAP, HANGUP, BYE = 1, 2, 3, 4
MAX_RECORD = 1 << 17
# What may wait: PDUs for a path that is slower than the radio, and PDUs held for a link that is not ready.
MAX_QUEUED = 4096
MAX_HELD = 256
# Link policy: the two devices may swap who leads the link (profiles need that), and no link may sleep.
ROLE_SWITCH_ONLY = 0x0001
STEP_TIMEOUT = 5.0


def record(kind: int, payload: bytes = b"") -> bytes:
    return struct.pack(">IB", len(payload) + 1, kind) + payload


def records(buffer: bytearray):
    """Take the whole records out of the front of a buffer: (kind, payload) each."""
    while len(buffer) >= 4:
        size = struct.unpack_from(">I", buffer)[0]
        if size < 1 or size > MAX_RECORD:
            raise ValueError(f"a record of {size} bytes is not one of a Bluetooth bridge")
        if len(buffer) < 4 + size:
            return
        kind, payload = buffer[4], bytes(buffer[5 : 4 + size])
        del buffer[: 4 + size]
        yield kind, payload


def hello(identity: BluetoothIdentity) -> bytes:
    return record(HELLO, json.dumps({"version": VERSION, **identity.model_dump()}).encode())


def read_hello(payload: bytes) -> BluetoothIdentity:
    said = json.loads(payload)
    if not isinstance(said, dict) or said.get("version") != VERSION:
        raise ValueError(f"the far half speaks version {said.get('version') if isinstance(said, dict) else '?'}")
    return BluetoothIdentity(
        name=said["name"], address=said.get("address", ""), class_of_device=said.get("class_of_device", 0x001F00)
    )


def _on_air(identity: BluetoothIdentity | None) -> tuple[str, int] | None:
    """What of an identity the bridge can carry in a radio. (Not the address: that is the controller's.)"""
    return (identity.name, identity.class_of_device) if identity is not None else None


class _Accept(PairingDelegate):
    """The bridge says it has a display and a yes/no key, as a phone and a head unit do, so that each device
    asks its own user to confirm. It then answers yes itself: the people at the two devices are who decide.

    Each device pairs with the bridge: there are two pairings, and each device shows the code of its own.
    """

    def __init__(self) -> None:
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_AND_YES_NO_INPUT)

    async def confirm(self, auto: bool = False) -> bool:
        return True

    async def compare_numbers(self, number: int, digits: int) -> bool:
        return True

    async def accept(self) -> bool:
        return True


class BridgeHalf:
    """The bridge in one device's radio, under the other device's name."""

    def __init__(
        self,
        *,
        open_radio: Callable[[], Awaitable[Any]],
        identity: BluetoothIdentity,
        logger: logging.Logger | None = None,
        retry: float = 2.0,
    ) -> None:
        # The radio's HCI port: an awaitable that gives a bumble transport (source, sink, close).
        self.open_radio = open_radio
        # The device this radio belongs to. The far half carries it as its persona.
        self.identity = identity
        self.logger = logger or logging.getLogger(__name__)
        self.retry = retry

        self.running = False
        self.attached = False  # the radio's HCI port is open
        self.error = ""
        self.persona: BluetoothIdentity | None = None  # the far device, whose name the bridge carries here
        self.joined = False  # the far half is on the path
        self.device: Device | None = None
        # The keys of the pairings done here live as long as the half: a device that paired with the bridge
        # can still reconnect after the persona or the radio came back.
        self.keys = MemoryKeyStore()

        self.link: Connection | None = None
        self.held: list[tuple[int, bytes]] = []
        self.reaching = False  # the bridge is calling the device
        self.recalling = False  # ... for the second time, after a key the device no longer had
        self.closing = False  # the bridge is being taken away: it hangs up the link itself
        self.called = False  # the link is one the bridge opened (see `reach`), not one the device opened
        self.stats = {"heard": 0, "sent": 0, "dropped": 0}

        self._to_far: Any = None  # the send side of the current path
        self._changed: anyio.Event | None = None
        # What the bridge starts for the persona it is (a call, a hang-up): stopped, and waited for, with it.
        self._work: TaskGroup | None = None

    # ---- the path to the far half ----------------------------------------------------------------

    @asynccontextmanager
    async def path(self):
        """The half's side of one forwarded connection. One at a time: a new one replaces the one before."""
        to_far, far_reads = create_memory_object_stream[bytes](MAX_QUEUED)
        far_writes, from_far = create_memory_object_stream[bytes](32)
        self._to_far = to_far
        try:
            async with create_task_group() as reader:
                reader.start_soon(self._read, from_far, to_far)
                self._say_hello()
                try:
                    yield StapledObjectStream(far_writes, far_reads)
                finally:
                    reader.cancel_scope.cancel()
        finally:
            for stream in (to_far, far_reads, far_writes, from_far):
                stream.close()
            if self._to_far is to_far:
                self._to_far = None
                self._far_left()

    def _send(self, data: bytes) -> bool:
        if self._to_far is None:
            return False
        try:
            self._to_far.send_nowait(data)
        except (anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
            return False
        return True

    def _say_hello(self) -> None:
        if self.running:
            self._send(hello(self.identity))

    async def _read(self, from_far, to_far) -> None:
        buffer = bytearray()
        try:
            async for chunk in from_far:
                buffer += chunk
                for kind, payload in records(buffer):
                    self._far_said(kind, payload)
        except (ValueError, KeyError, TypeError) as error:
            # Not a bridge at the other end, or one that speaks otherwise: close the path. It is opened again.
            self.error = f"the far half sent what is not understood: {error}"
            self.logger.warning("Bluetooth bridge: %s", self.error)
        except (anyio.BrokenResourceError, anyio.ClosedResourceError):
            pass
        to_far.close()

    def _far_said(self, kind: int, payload: bytes) -> None:
        if kind == HELLO:
            persona = read_hello(payload)
            self.joined = True
            before, self.persona = self.persona, persona
            if _on_air(before) != _on_air(persona):
                self.logger.info("Bluetooth bridge: the far device is %r (%s)", persona.name, persona.address or "?")
                self._wake()
        elif kind == L2CAP:
            if len(payload) < 2:
                raise ValueError("an L2CAP record without a channel")
            self.say(struct.unpack_from(">H", payload)[0], payload[2:])
        elif kind == HANGUP:
            # one conversation: when one device hangs up, the other is hung up on. (A link that is still being
            # paired is left alone: a phone hangs up between pairing and its first use, a person takes longer.)
            if self.secure():
                self._hang_up()
        elif kind == BYE:
            self._far_left()
        # (a kind that is not known is a later version's: it is not for this one)

    def _far_left(self) -> None:
        """The far half is gone, and with it the other device: nothing said here reaches anyone."""
        if self.joined:
            self.joined = False
            self.held = []
            self._hang_up()

    def _start(self, work, *args) -> None:
        if self._work is not None:
            self._work.start_soon(work, *args)

    def _hang_up(self) -> None:
        if self.link is not None:
            self._start(self._disconnect, self.link)

    async def _disconnect(self, link: Connection) -> None:
        try:
            with anyio.move_on_after(STEP_TIMEOUT):
                await link.disconnect()
        except Exception:  # noqa: BLE001, S110 - a link the device is already dropping has nothing left to hang up
            pass

    # ---- the link to the device of this radio ----------------------------------------------------

    def secure(self) -> bool:
        return self.link is not None and self.link.is_encrypted

    def ready(self) -> bool:
        """Whether what the other device says can be passed on.

        On a link the bridge opened, not before the bridge has paired and encrypted it. On a link the device
        opened, at once: the device looks the bridge up first (SDP, in the clear), and encrypts the link itself
        before it uses anything that needs it. That is a phone reconnecting to a head unit it knows.
        """
        return self.link is not None and (self.link.is_encrypted or not self.called)

    def heard(self, _connection: Connection, cid: int, pdu: bytes) -> None:
        """Something the device of this radio said: the other device hears it."""
        if self.joined and self._send(record(L2CAP, struct.pack(">H", cid) + bytes(pdu))):
            self.stats["heard"] += 1
        else:
            self.stats["dropped"] += 1

    def say(self, cid: int, pdu: bytes) -> None:
        """Something the other device said: the device of this radio hears it from the bridge."""
        if self.device is None:
            self.stats["dropped"] += 1
            return
        if self.ready():
            self._deliver(cid, pdu)
            return
        if len(self.held) >= MAX_HELD:
            self.stats["dropped"] += 1
            return
        self.held.append((cid, pdu))
        if self.link is None and not self.reaching:
            self.reaching = True
            self._start(self.reach, self.device)

    def _deliver(self, cid: int, pdu: bytes) -> None:
        assert self.device is not None and self.link is not None
        self.device.send_l2cap_pdu(self.link.handle, cid, pdu)
        self.stats["sent"] += 1

    def release(self) -> None:
        if self.ready():
            held, self.held = self.held, []
            for cid, pdu in held:
                self._deliver(cid, pdu)

    def linked(self, connection: Connection) -> None:
        self.link = connection
        self.called = self.reaching
        if not self.identity.address:
            # The device called before anyone said where it is: now it is known, and the far half is told.
            self.identity = self.identity.model_copy(
                update={"address": connection.peer_address.to_string(with_type_qualifier=False)}
            )
            self._say_hello()
        self._start(self.stay_awake, connection)
        connection.on("connection_encryption_change", self.release)
        connection.on("disconnection", self.left)
        self.release()

    def left(self, _reason: int) -> None:
        if self.recalling:
            # the bridge hung up a link the device refused, to call it again: what is held is for the next link
            self.link = None
            return
        self.link, self.held = None, []  # (a call that is still being placed ends by itself)
        if not self.closing and self.joined:
            self._send(record(HANGUP))

    async def stay_awake(self, connection: Connection) -> None:
        """Refuse the low-power modes on this link.

        An idle Android asks for sniff mode; the virtual radio answers that badly, and a minute later calls the
        link dead. A link that never sleeps costs a virtual device nothing.
        """
        try:
            await connection.device.host.send_sync_command(
                HCI_Write_Link_Policy_Settings_Command(
                    connection_handle=connection.handle, link_policy_settings=ROLE_SWITCH_ONLY
                )
            )
        except Exception as error:  # noqa: BLE001 - the link works without it, for a while
            self.logger.warning("Bluetooth bridge: could not keep the link to %r awake: %r", self.identity.name, error)

    async def reach(self, device: Device) -> None:
        """Call the device of this radio, and pair with it.

        Only a link the bridge opened is authenticated by the bridge, and only once: a device that called is
        the one that pairs, and the radio's link manager does not survive being asked twice.

        A device that was made to forget its pairings (a factory reset of its Bluetooth, "Forget" in its
        settings) no longer has the key that is kept here, and refuses a link authenticated with it. The key
        is then of no use: it is forgotten here too, the link is hung up, and the device is called once more,
        on a new link, and paired with anew. What the far device said is held meanwhile.
        """
        try:
            if not self.identity.address:
                raise RuntimeError("its address is not known: set it, or let the device call first")
            try:
                await self._call(device)
            except Exception:
                if not await self._forget_key(device):
                    raise
                self.logger.info(
                    "Bluetooth bridge: %r no longer has the key of its pairing: pairing with it anew",
                    self.identity.name,
                )
                await self._call(device)
            self.release()
        except Exception as error:  # noqa: BLE001
            who = self.persona.name if self.persona else "?"
            self.logger.warning("Bluetooth bridge: %r could not reach %r: %r", who, self.identity.name, error)
            # A link that could not be made secure carries nothing: left up, it would only keep the device from
            # being called again. Hung up, the far device is hung up on, and the next thing said calls again.
            if self.link is not None and not self.secure():
                await self._disconnect(self.link)
        finally:
            self.reaching = self.recalling = False

    async def _call(self, device: Device) -> None:
        link = await device.connect(self.identity.address, transport=PhysicalTransport.BR_EDR, timeout=30)
        await link.authenticate()
        await link.encrypt()

    async def _forget_key(self, device: Device) -> bool:
        """Forget the key kept for the device of this radio, and hang up the link that was refused with it.
        False when there was no key: the call failed for another reason."""
        # (a key is kept under the address as the radio gave it, which may say what kind of address it is)
        kept = [name for name, _ in await self.keys.get_all() if name.upper().startswith(self.identity.address)]
        if not kept:
            return False
        for name in kept:
            await self.keys.delete(name)
        self.recalling = True  # (the link that goes is not the end of the conversation: see `left`)
        if self.link is not None:
            await self._disconnect(self.link)
        with anyio.move_on_after(STEP_TIMEOUT):
            while self.link is not None:
                await anyio.sleep(0.05)
        return True

    # ---- the radio -------------------------------------------------------------------------------

    def _wake(self) -> None:
        if self._changed is not None:
            self._changed.set()

    def set_identity(self, identity: BluetoothIdentity) -> None:
        if identity != self.identity:
            self.identity = identity
            self._say_hello()

    def _build(self, transport: Any, persona: BluetoothIdentity) -> Device:
        config = DeviceConfiguration()
        config.name = persona.name
        config.class_of_device = persona.class_of_device
        config.classic_enabled, config.le_enabled = True, False
        # without Secure Connections there is no key derived across transports to disagree about
        config.classic_sc_enabled, config.classic_ssp_enabled = False, True
        device = Device(config=config, host=Host(controller_source=transport.source, controller_sink=transport.sink))
        device.keystore = self.keys
        device.pairing_config_factory = lambda _connection: PairingConfig(
            sc=False, mitm=False, bonding=True, delegate=_Accept()
        )
        # everything above the link is relayed, none of it answered
        device.l2cap_channel_manager.on_pdu = self.heard  # ty: ignore[invalid-assignment]
        device.on("connection", self.linked)
        return device

    async def _put_on_air(self, device: Device) -> None:
        await device.power_on()
        try:
            await device.host.send_sync_command(
                HCI_Write_Default_Link_Policy_Settings_Command(default_link_policy_settings=ROLE_SWITCH_ONLY)
            )
        except Exception as error:  # noqa: BLE001 - each link is asked again when it comes up
            self.logger.warning("Bluetooth bridge: the radio did not take the link policy: %r", error)
        await device.set_discoverable(True)
        await device.set_connectable(True)

    async def _take_off_air(self, device: Device) -> None:
        """Hang up, and power the persona's device off."""
        self.closing = True
        try:
            if self.link is not None:
                if self.joined:
                    self._send(record(HANGUP))  # the other device's conversation is over too
                await self._disconnect(self.link)
            try:
                with anyio.move_on_after(STEP_TIMEOUT):
                    await device.power_off()
            except Exception as error:  # noqa: BLE001 - the radio may be gone already
                self.logger.debug("Bluetooth bridge: power off: %r", error)
        finally:
            self.link, self.held, self.reaching, self.recalling = None, [], False, False
            self.closing = False

    async def _be(self, transport: Any, persona: BluetoothIdentity, lost) -> None:
        """Be the persona in this radio, until it changes or the radio is gone."""
        device = self._build(transport, persona)
        try:
            # nothing the bridge starts here is left behind: what has not finished by the end is stopped, and
            # waited for
            async with create_task_group() as work:
                self._work = work
                try:
                    await self._put_on_air(device)
                    self.device = device
                    self.logger.info("Bluetooth bridge: %r is in the radio of %r", persona.name, self.identity.name)
                    while _on_air(self.persona) == _on_air(persona) and not lost.done():
                        await self._wait()
                finally:
                    self.device, self._work = None, None
                    work.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await self._take_off_air(device)

    async def _serve(self, transport: Any) -> None:
        lost = transport.source.terminated
        lost.add_done_callback(lambda _: self._wake())
        while not lost.done():
            if self.persona is not None:
                await self._be(transport, self.persona, lost)
            else:
                await self._wait()

    async def _wait(self) -> None:
        """Until the persona changes or the radio is gone."""
        assert self._changed is not None
        await self._changed.wait()
        self._changed = anyio.Event()

    async def run(self) -> None:
        """Run the half until cancelled. The radio is looked for until it is there, and again after it went
        away (the device was power cycled)."""
        self._changed = anyio.Event()
        self.running, self.error = True, ""
        self._say_hello()
        try:
            while True:
                try:
                    transport = await self.open_radio()
                except Exception as error:  # noqa: BLE001
                    self.error = f"the radio's HCI port is not there: {error}"
                    await anyio.sleep(self.retry)
                    continue
                self.attached, self.error = True, ""
                try:
                    await self._serve(transport)
                    self.error = "the radio closed its HCI port"
                except Exception as error:  # noqa: BLE001
                    self.error = f"the radio failed: {error!r}"
                    self.logger.warning("Bluetooth bridge: %s", self.error)
                finally:
                    self.attached = False
                    with anyio.CancelScope(shield=True), anyio.move_on_after(STEP_TIMEOUT):
                        await transport.close()
                await anyio.sleep(self.retry)
        finally:
            self.running = self.attached = False
            self._send(record(BYE))

    def status(self) -> dict:
        return {
            "running": self.running,
            "attached": self.attached,
            "peer": self.joined,
            "on_air": self.device is not None,
            "linked": self.link is not None,
            "encrypted": self.secure(),
            "persona": self.persona.name if self.persona else "",
            "held": len(self.held),
            **self.stats,
            "error": self.error,
        }
