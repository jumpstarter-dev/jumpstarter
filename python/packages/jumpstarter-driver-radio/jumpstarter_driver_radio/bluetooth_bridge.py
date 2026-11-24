"""One half of a Bluetooth bridge between two radios that do not share the air.

Two devices that each have a radio of their own (two virtual devices, or a virtual device and a real one beside
a real controller) cannot see each other. A bridge stands in both radios at once: in each it is a device that
carries the *other* device's name and class, and what a device says to it, it says to the other device.

    phone ))) [ "Head Unit" | half ] ══ path ══ [ half | "Phone" ] ((( head unit

Each half runs beside its radio, as a Bluetooth host (bumble) on the radio's HCI port, and the two halves are
joined by one byte stream: a forward of the lease between their ``connects`` ports. Everything with a deadline
stays beside the radio: the link, its pairing and its encryption are each half's own, and are done twice, once
in each radio. What crosses the path is what is above the link: L2CAP, and so SDP, RFCOMM and the profiles.

There are two links and two pairings, and the halves make them behave as one. When a device opens a link to
its half, the far half calls the far device at once and pairs with it, so that both devices ask their users at
about the same time; the half of the device that started does not agree to its pairing until the far device
has agreed to its own, so a refusal or a timeout on either side ends both.

The path's wire format is a stream of records, each a 4-byte big-endian length and that many bytes (the framing
of the Wi-Fi frame port). A record's first byte says what it is:

    1  HELLO    JSON: {"version": 2, "name": ..., "address": ..., "class_of_device": ...}: the device beside
                the sender's radio. Sent when the path opens and when the identity changes.
    2  L2CAP    a 2-byte big-endian channel id, then one L2CAP PDU the sender's device said.
    3  HANGUP   the sender's link to its device is gone.
    4  BYE      the sender is being stopped.
    5  LINK     JSON: {"by": "device" | "bridge"}: the sender has a link to its device, and who opened it.
    6  PAIRING  JSON: {"state": "started" | "complete" | "failed" | "unavailable", "reason": ...}: where the
                sender is with making its link secure. "unavailable": it cannot call its device at all.

A half that says version 1 knows records 1 to 4 only: it is not waited for.
"""

from __future__ import annotations

import json
import logging
import math
import struct
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
from anyio import create_memory_object_stream, create_task_group
from anyio.abc import TaskGroup
from anyio.streams.stapled import StapledObjectStream
from bumble import hci
from bumble.core import PhysicalTransport
from bumble.device import Connection, Device, DeviceConfiguration
from bumble.hci import HCI_Write_Default_Link_Policy_Settings_Command, HCI_Write_Link_Policy_Settings_Command
from bumble.host import Host
from bumble.keys import MemoryKeyStore
from bumble.pairing import PairingConfig, PairingDelegate

from .common import BluetoothIdentity

PROTOCOL = "bt-l2cap-bridge"
VERSION = 2
HELLO, L2CAP, HANGUP, BYE, LINK, PAIRING = 1, 2, 3, 4, 5, 6
MAX_RECORD = 1 << 17
# What may wait: PDUs for a path that is slower than the radio, and PDUs held for a link that is not ready.
MAX_QUEUED = 4096
MAX_HELD = 256
# Link policy: the two devices may swap who leads the link (profiles need that), and no link may sleep.
ROLE_SWITCH_ONLY = 0x0001
STEP_TIMEOUT = 5.0
# How long a call to the device may take. A device that is there answers a page in a second or two; one whose
# Bluetooth is restarting does not answer at all, and the far device must not be kept waiting for it.
CALL_TIMEOUT = 8.0
# How long the device that started a pairing is kept waiting for the far device to agree to its own. A link
# manager gives up on a pairing after 30 seconds.
PAIR_TIMEOUT = 25.0
# After a call that failed the device is not called again at once: 2, 4, 8 ... seconds, at most this.
CALL_BACKOFF_MAX = 30.0
# The answers of a device that no longer has the key of its pairing, or has another.
KEY_REFUSED = (hci.HCI_ErrorCode.PIN_OR_KEY_MISSING_ERROR, hci.HCI_ErrorCode.AUTHENTICATION_FAILURE_ERROR)

# The states of a half's link to its device.
OFF = "off"  # the half is not in the radio
IDLE = "idle"  # in the radio, no link
CALLING = "calling"  # the half is calling the device
LINKED = "linked"  # a link, not secure, nothing in flight
PAIRING_NOW = "pairing"  # the device and the half are pairing, or the half is authenticating the link
SECURE = "secure"  # the link is encrypted


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


def read_hello(payload: bytes) -> tuple[BluetoothIdentity, int]:
    """The far device, and the version its half speaks."""
    said = json.loads(payload)
    version = said.get("version") if isinstance(said, dict) else None
    if not isinstance(version, int) or not 1 <= version <= VERSION:
        raise ValueError(f"the far half speaks version {version if version is not None else '?'}")
    identity = BluetoothIdentity(
        name=said["name"], address=said.get("address", ""), class_of_device=said.get("class_of_device", 0x001F00)
    )
    return identity, version


def said(kind: int, **what) -> bytes:
    return record(kind, json.dumps(what).encode())


def read_said(payload: bytes) -> dict:
    what = json.loads(payload)
    if not isinstance(what, dict):
        raise TypeError("a record that is not an object")
    return what


def _on_air(identity: BluetoothIdentity | None) -> tuple[str, int] | None:
    """What of an identity the bridge can carry in a radio. (Not the address: that is the controller's.)"""
    return (identity.name, identity.class_of_device) if identity is not None else None


async def _unless_gone(what: Awaitable[Any]) -> Any:
    """Wait for something asked of the radio. bumble ends what was asked on a link that then went away with a
    cancellation, which here would be taken for the half being stopped: it is an error like any other."""
    try:
        return await what
    except anyio.get_cancelled_exc_class():
        if anyio.current_effective_deadline() == -math.inf:
            raise  # (it is the half that is being stopped)
        raise RuntimeError("the link went away") from None


class _Agree(PairingDelegate):
    """The bridge says it has a display and a yes/no key, as a phone and a head unit do, so that each device
    asks its own user to confirm. Its own answer is the far device's: see ``BridgeHalf.agree``.

    Each device pairs with the bridge: there are two pairings, and each device shows the code of its own.
    """

    def __init__(self, half: BridgeHalf) -> None:
        super().__init__(io_capability=PairingDelegate.IoCapability.DISPLAY_OUTPUT_AND_YES_NO_INPUT)
        self.half = half

    async def confirm(self, auto: bool = False) -> bool:
        return await self.half.agree()

    async def compare_numbers(self, number: int, digits: int) -> bool:
        return await self.half.agree()

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
        call_timeout: float = CALL_TIMEOUT,
        pair_timeout: float = PAIR_TIMEOUT,
        learn_name: bool = False,
    ) -> None:
        # The radio's HCI port: an awaitable that gives a bumble transport (source, sink, close).
        self.open_radio = open_radio
        # The device this radio belongs to. The far half carries it as its persona.
        self.identity = identity
        self.logger = logger or logging.getLogger(__name__)
        self.retry = retry
        self.call_timeout = call_timeout
        self.pair_timeout = pair_timeout
        # Ask a device that has linked for its name (an HCI remote name request), and take it as the
        # identity's. For a device nothing else can be asked about: a physical one beside a real controller.
        self.learn_name = learn_name
        # What of the identity somebody said outright (`set_identity`): nothing learned replaces it.
        self.pinned: set[str] = set()
        # What is known of the device from elsewhere (an identity source): False while its Bluetooth is off
        # or the device is not there, and it is then not called. None: nothing is known.
        self.device_up: bool | None = None

        self.running = False
        self.attached = False  # the radio's HCI port is open
        self.error = ""
        self.persona: BluetoothIdentity | None = None  # the far device, whose name the bridge carries here
        self.joined = False  # the far half is on the path
        self.device: Device | None = None
        # The keys of the pairings done here live as long as the half: a device that paired with the bridge
        # can still reconnect after the persona or the radio came back.
        self.keys = MemoryKeyStore()

        # The link to the device of this radio, and where it is: see the states above.
        self.state = OFF
        self.link: Connection | None = None
        self.opened_by: str | None = None  # "device" or "bridge": who opened the link
        self._device_asked = False  # the device has asked the radio for a link (it is calling the bridge)
        # The link here went, and the far half has not said of a new one of its own since: what its device
        # still says belongs to the conversation that is over, and is no reason to call the device here.
        self._far_is_behind = False
        self.held: list[tuple[int, bytes]] = []
        self.reaching = False  # the bridge is calling the device
        self.recalling = False  # ... for the second time, after a key the device no longer had
        self.closing = False  # the bridge is being taken away: it hangs up the link itself
        # A key is being made, or was made, on this link, and the far device has not agreed to its own
        # pairing yet. If it never does, the key is half of nothing, and is forgotten.
        self.fresh = False
        self.last_error = ""  # why the last call or pairing failed; empty once one succeeded
        self.stats = {"heard": 0, "sent": 0, "dropped": 0, "calls": 0, "pairings": 0}
        self._told_complete = False
        self._explained = False  # why this link's pairing failed has been said (the first reason is the reason)
        self._call_failures = 0
        self._call_after = 0.0  # (monotonic) not before this is the device called again

        # What the far half last said of its own link.
        self.far_version = VERSION
        self.far_link: str | None = None
        self.far_pairing: str | None = None

        self._to_far: Any = None  # the send side of the current path
        self._changed: anyio.Event | None = None
        self._news: anyio.Event | None = None  # the far half said something of its link or its pairing
        # What the bridge starts for the persona it is (a call, a hang-up): stopped, and waited for, with it.
        self._work: TaskGroup | None = None
        # Called when something went wrong with the device (a call that failed), for whoever watches it.
        self.on_trouble: Callable[[], None] | None = None

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
        """Say who the device here is, and where its link stands: the far half may have just arrived."""
        if not self.running:
            return
        self._send(hello(self.identity))
        if self.link is not None:
            self._send(said(LINK, by=self.opened_by))
            if self._told_complete:
                self._send(said(PAIRING, state="complete"))

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
            persona, self.far_version = read_hello(payload)
            self.joined = True
            before, self.persona = self.persona, persona
            if _on_air(before) != _on_air(persona):
                self.logger.info("Bluetooth bridge: the far device is %r (%s)", persona.name, persona.address or "?")
                self._wake()
            self._tell_news()
        elif kind == L2CAP:
            if len(payload) < 2:
                raise ValueError("an L2CAP record without a channel")
            self.say(struct.unpack_from(">H", payload)[0], payload[2:])
        elif kind == HANGUP:
            self._far_hung_up()
        elif kind == BYE:
            self._far_left()
        elif kind == LINK:
            self._far_linked(str(read_said(payload).get("by")))
        elif kind == PAIRING:
            what = read_said(payload)
            self._far_pairing(str(what.get("state")), str(what.get("reason") or ""))
        # (a kind that is not known is a later version's: it is not for this one)

    def _tell_news(self) -> None:
        if self._news is not None:
            self._news.set()
            self._news = anyio.Event()

    def _far_linked(self, by: str) -> None:
        """The far half has a link to its device. If that device opened it, it is starting something (a
        pairing, a reconnection): the device here is called now, so that both are asked at the same time."""
        self.far_link, self.far_pairing, self._far_is_behind = by, None, False
        self._tell_news()
        if by == "device" and self.link is None and not self.reaching:
            self._call("the far device opened a link")

    def _far_pairing(self, state: str, reason: str) -> None:
        self.far_pairing = state
        self._tell_news()  # (whoever waits in `agree` has its answer)
        if state == "complete":
            self._settle()
        elif state == "failed":
            # The far device refused, did not answer in time, or could not be reached: there is no
            # conversation to have, and a pairing that was being made here is not made.
            self._cancel(f"the far device did not pair: {reason or 'no reason given'}")

    def _far_hung_up(self) -> None:
        """One conversation: when one device hangs up, the other is hung up on.

        If the far device was in the middle of its pairing, it gave up, and so does the bridge here: what was
        made of a key is forgotten. A pairing that is still being made here is otherwise left alone: a phone
        hangs up between pairing and its first use, a person takes longer.
        """
        gave_up, self.far_link, self.far_pairing = self.far_pairing == "started", None, None
        self._tell_news()
        if self.link is None:
            return
        if self.fresh and gave_up:
            self._cancel("the far device hung up before it had paired")
        elif self.secure():
            self.fresh = False  # (the far device never meant to pair: the key made here is this device's own)
            self._hang_up()

    def _far_left(self) -> None:
        """The far half is gone, and with it the other device: nothing said here reaches anyone."""
        self.far_link = self.far_pairing = None
        if self.joined:
            self.joined = False
            self.held = []
            self._tell_news()
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
                await _unless_gone(link.disconnect())
        except Exception:  # noqa: BLE001, S110 - a link the device is already dropping has nothing left to hang up
            pass

    # ---- the state of the link to the device of this radio ----------------------------------------

    def _names(self) -> str:
        return f"{self.identity.name!r} here, {self.persona.name if self.persona else '?'!r} far"

    def _to(self, state: str, why: str) -> None:
        """Every change of state goes through here, and is said."""
        if state != self.state:
            self.logger.info("Bluetooth bridge [%s]: %s -> %s: %s", self._names(), self.state, state, why)
            self.state = state

    def _fail(self, why: str) -> None:
        self.last_error, self._explained = why, True
        self.logger.warning("Bluetooth bridge [%s]: %s", self._names(), why)

    @property
    def called(self) -> bool:
        """The link is one the bridge opened (see `reach`), not one the device opened."""
        return self.opened_by == "bridge"

    def secure(self) -> bool:
        return self.link is not None and self.link.is_encrypted

    def bonded(self) -> bool:
        """A key is kept here for the device of this radio."""
        return bool(self._kept_keys())

    def _kept_keys(self) -> list[str]:
        # (a key is kept under the address as the radio gave it, which may say what kind of address it is)
        address = self.identity.address
        return [name for name in self.keys.all_keys if address and name.upper().startswith(address)]

    def _forget_keys(self) -> None:
        for name in self._kept_keys():
            del self.keys.all_keys[name]

    def _waits_for_nobody(self) -> str:
        """Why the far device's agreement is not waited for, if it is not."""
        if not self.joined:
            return "no far half is on the path"
        if self.far_version < 2:
            return "the far half does not say how its pairing goes"
        if self.opened_by == "device" and self.far_link == "device":
            return "the far device started too: each pairs with its own half"
        if self.far_pairing == "unavailable":
            return "the far half cannot call its device"
        return ""

    def paired(self) -> bool:
        """Both devices have a secure link to the bridge: the conversation is whole."""
        return self.secure() and (self.far_pairing == "complete" or bool(self._waits_for_nobody()))

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
        if self.link is None and self._far_is_behind and self.far_version >= 2:
            # The last words of a conversation that ended when the link here went (the far device had not
            # heard yet). A device that forgot its pairing must not be called, and asked to pair, for those.
            self.stats["dropped"] += 1
            return
        self.held.append((cid, pdu))
        if self.link is None and not self.reaching and not self._call("the far device spoke"):
            self.stats["dropped"] += len(self.held)
            self.held = []

    def _deliver(self, cid: int, pdu: bytes) -> None:
        assert self.device is not None and self.link is not None
        self.device.send_l2cap_pdu(self.link.handle, cid, pdu)
        self.stats["sent"] += 1

    def release(self) -> None:
        if self.ready():
            held, self.held = self.held, []
            for cid, pdu in held:
                self._deliver(cid, pdu)

    # ---- a link comes, becomes secure, goes ---------------------------------------------------------

    def linked(self, connection: Connection) -> None:
        self.link = connection
        # Who opened it is what the radio says, not what the bridge was doing: a device that calls while the
        # bridge is calling it has opened its own link, and pairs it itself. What the radio says is whether
        # the device asked for the link. (Not who leads it: an Android that is called takes the lead of the
        # link as it accepts it, and the link then looks as if the device had opened it.)
        self.opened_by = "device" if self._device_asked else "bridge"
        self.fresh = self._told_complete = self._explained = False
        self._call_failures, self._call_after = 0, 0.0
        if self.far_pairing in ("failed", "unavailable"):
            # That was said of an earlier try (a half says its call failed after it has said the link is gone).
            # What the far half says of this one comes after it has heard of this link.
            self.far_pairing = None
        self._to(LINKED, f"the {self.opened_by} opened a link")
        if not self.identity.address:
            # The device called before anyone said where it is: now it is known, and the far half is told.
            self.identity = self.identity.model_copy(
                update={"address": connection.peer_address.to_string(with_type_qualifier=False)}
            )
            self._send(hello(self.identity))
        self._send(said(LINK, by=self.opened_by))
        self._start(self.stay_awake, connection)
        if self.learn_name:
            self._start(self._ask_name, connection)
        connection.on("connection_encryption_change", self._encryption_changed)
        connection.on("connection_authentication", lambda: self._made_secure("the link is authenticated"))
        # A pairing is made when its key is: an Android that pairs from its settings does only that, and hangs
        # up (it comes back for what it wants of the other device). It does not wait to encrypt the link.
        connection.on("link_key", lambda: self._made_secure("a key was made with the device"))
        connection.on("classic_pairing", lambda: self._made_secure("the device paired with the bridge"))
        connection.on("connection_authentication_failure", self._not_made_secure)
        connection.on("classic_pairing_failure", self._not_made_secure)
        connection.on("disconnection", self.left)
        self.release()

    def _device_calls(self, _address: Any, _class_of_device: int, link_type: int) -> None:
        if link_type == hci.HCI_Connection_Complete_Event.LinkType.ACL:
            self._device_asked = True

    def _encryption_changed(self) -> None:
        if self.secure():
            self._to(SECURE, "the link is encrypted")
            self._made_secure("the link is encrypted")
        self.release()

    def _made_secure(self, why: str) -> None:
        """The device of this radio has done its part: the far half is told, once for a link."""
        if self.link is None or self._told_complete:
            return
        self._told_complete = True
        self.last_error = ""
        self.logger.info("Bluetooth bridge [%s]: this device's link is good: %s", self._names(), why)
        self._send(said(PAIRING, state="complete"))
        self._settle()

    def _settle(self) -> None:
        """Both devices have agreed: a key made on this link is a whole one."""
        if self.fresh and self._told_complete and (self.far_pairing == "complete" or self._waits_for_nobody()):
            self.fresh = False
            self.logger.info("Bluetooth bridge [%s]: both devices have paired", self._names())

    def _not_made_secure(self, reason: Any = None) -> None:
        """The device of this radio refused its pairing, or it failed: the far device's is called off."""
        if self.link is None or self.reaching:
            return  # (a call the bridge is placing reports for itself: see `reach`)
        if not self._explained:
            self._fail(f"the pairing with {self.identity.name!r} failed: {reason!r}")
        self._send(said(PAIRING, state="failed", reason=f"{self.identity.name!r} did not pair"))
        if self.fresh:
            self._forget_keys()
            self.fresh = False
        self._to(LINKED, "the pairing failed")

    def _cancel(self, why: str) -> None:
        """Call off what is being done with the device here, because the far device will not have it."""
        self._fail(why)
        if self.fresh:
            self._forget_keys()  # the half-made key
            self.fresh = False
        self.held = []
        self._hang_up()

    def left(self, _reason: int) -> None:
        if self.recalling:
            # the bridge hung up a link the device refused, to call it again: what is held is for the next link
            self.link = None
            return
        if self.fresh and (not self._told_complete or self.far_pairing == "started"):
            # the device went before both had agreed: what was made of a key is forgotten
            self._forget_keys()
        self.link, self.held, self.opened_by = None, [], None  # (a call that is still being placed ends by itself)
        self._device_asked = False
        self._far_is_behind = self.far_link is not None
        self.fresh = self._told_complete = False
        self._tell_news()
        self._to(CALLING if self.reaching else IDLE, "the link is gone")
        if not self.closing and self.joined:
            self._send(record(HANGUP))

    async def agree(self) -> bool:
        """The half's answer to a pairing the device of this radio shows its user.

        On a link the bridge opened, yes at once: the far device started, and its half is waiting for this
        device to agree. On a link the device opened, the answer is the far device's: yes when it has agreed
        to its own pairing, no when it refused, could not be reached or did not answer in time. So a pairing
        is made on both sides or on neither.
        """
        self.fresh = True
        self.stats["pairings"] += 1
        self._to(PAIRING_NOW, f"{self.identity.name!r} is asked to pair")
        self._send(said(PAIRING, state="started"))
        if self.opened_by != "device":
            return True
        with anyio.move_on_after(self.pair_timeout):
            while True:
                if why := self._waits_for_nobody():
                    self.logger.info("Bluetooth bridge [%s]: agreeing at once: %s", self._names(), why)
                    return True
                if self.far_pairing == "complete":
                    self.logger.info("Bluetooth bridge [%s]: the far device has agreed: agreeing", self._names())
                    return True
                if self.far_pairing == "failed" or self.link is None:
                    return False
                assert self._news is not None
                await self._news.wait()
        self._fail(f"the far device did not agree to pair within {self.pair_timeout:.0f}s")
        return False

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

    async def _ask_name(self, connection: Connection) -> None:
        """Ask the device what it is called, when nothing else can be asked about it."""
        try:
            with anyio.fail_after(STEP_TIMEOUT):
                name = await _unless_gone(connection.device.request_remote_name(connection))
        except Exception as error:  # noqa: BLE001 - the name that was configured stays
            self.logger.debug("Bluetooth bridge: %r did not say its name: %r", self.identity.name, error)
            return
        if name:
            self.learn(name=name)

    # ---- calling the device ---------------------------------------------------------------------------

    def _call(self, why: str) -> bool:
        """Start a call to the device of this radio, if it can be called now. When it cannot, the far half is
        told, so that the far device is not left waiting."""
        if self.device is None or self.reaching:
            return self.reaching
        if not self.identity.address:
            self._fail(f"{self.identity.name!r} cannot be called: its address is not known")
            self._send(said(PAIRING, state="unavailable", reason="the address of the device is not known"))
            return False
        wait = self._call_after - time.monotonic()
        if self.device_up is False or wait > 0:
            reason = "its Bluetooth is off" if self.device_up is False else f"it did not answer; {wait:.0f}s to wait"
            self._fail(f"{self.identity.name!r} is not called now: {reason}")
            self._send(said(PAIRING, state="failed", reason=f"{self.identity.name!r} cannot be reached now"))
            return False
        self.reaching = True
        self.stats["calls"] += 1
        self._to(CALLING, why)
        self._start(self.reach, self.device)
        return True

    def device_came_up(self) -> None:
        """Whoever watches the device says its Bluetooth is on (again): it may be called at once."""
        self._call_failures, self._call_after = 0, 0.0

    async def reach(self, device: Device) -> None:
        """Call the device of this radio, and pair with it.

        Only a link the bridge opened is authenticated by the bridge, and only once: a device that called is
        the one that pairs, and the radio's link manager does not survive being asked twice.

        A device that was made to forget its pairings (a factory reset of its Bluetooth, "Forget" in its
        settings) no longer has the key that is kept here, and refuses a link authenticated with it. The key
        is then of no use: it is forgotten here too, the link is hung up, and the device is called once more,
        on a new link, and paired with anew. What the far device said is held meanwhile. Only that answer of
        the device is taken so: a call that fails otherwise (the device's Bluetooth is restarting) is not
        placed again at once.
        """
        dialed = False
        try:
            if not await self._dial(device):
                return  # the device called meanwhile: the link is its own, and it pairs it itself
            dialed = True
            try:
                await self._make_secure()
            except hci.HCI_Error as error:
                if error.error_code not in KEY_REFUSED or not self._kept_keys():
                    raise
                self.logger.info(
                    "Bluetooth bridge [%s]: %r no longer has the key of its pairing: pairing with it anew",
                    self._names(),
                    self.identity.name,
                )
                await self._hang_up_to_call_again()
                if self.device_up is False:
                    raise RuntimeError("its Bluetooth is off") from None
                if not await self._dial(device):
                    return
                await self._make_secure()
            self.release()
        except Exception as error:  # noqa: BLE001
            await self._call_failed(error, dialed)
        finally:
            self.reaching = self.recalling = False
            if self.state == CALLING:
                self._to(IDLE if self.link is None else LINKED, "the call is over")

    async def _call_failed(self, error: Exception, dialed: bool) -> None:
        if dialed:
            # The device answered, and the link could not be made secure: its person said no, or its radio
            # did. It is there, and may be called again whenever.
            self._fail(f"{self.identity.name!r} did not pair: {error!r}")
            self._send(said(PAIRING, state="failed", reason=f"{self.identity.name!r} did not pair"))
        else:
            self._call_failures += 1
            backoff = min(2.0**self._call_failures, CALL_BACKOFF_MAX)
            self._call_after = time.monotonic() + backoff
            self._fail(f"could not reach {self.identity.name!r}: {error!r} (not called again for {backoff:.0f}s)")
            self._send(said(PAIRING, state="failed", reason=f"{self.identity.name!r} could not be reached"))
        if self.fresh:
            self._forget_keys()
            self.fresh = False
        self.held = []
        # A link that could not be made secure carries nothing: left up, it would only keep the device from
        # being called again. Hung up, the far device is hung up on, and the next thing said calls again.
        if self.link is not None and not self.secure():
            await self._disconnect(self.link)
        if self.on_trouble is not None:
            self.on_trouble()

    async def _dial(self, device: Device) -> bool:
        """Open a link to the device. False when the device opened one itself meanwhile."""
        try:
            with anyio.fail_after(self.call_timeout + STEP_TIMEOUT):  # (bumble cancels its own call first)
                await _unless_gone(
                    device.connect(self.identity.address, transport=PhysicalTransport.BR_EDR, timeout=self.call_timeout)
                )
        except Exception:
            if self.link is None or self.called:
                raise
        return self.link is not None and self.called

    async def _make_secure(self) -> None:
        link = self.link
        if link is None:
            raise RuntimeError("the link went as it came")
        self._to(PAIRING_NOW, f"authenticating the link to {self.identity.name!r}")
        await _unless_gone(link.authenticate())
        await _unless_gone(link.encrypt())

    async def _hang_up_to_call_again(self) -> None:
        """Forget the key kept for the device of this radio, and hang up the link that was refused with it."""
        self._forget_keys()
        self.recalling = True  # (the link that goes is not the end of the conversation: see `left`)
        if self.link is not None:
            await self._disconnect(self.link)
        with anyio.move_on_after(STEP_TIMEOUT):
            while self.link is not None:
                await anyio.sleep(0.05)
        self.recalling = False
        self.fresh = self._told_complete = False

    # ---- who the device here is -----------------------------------------------------------------------

    def _wake(self) -> None:
        if self._changed is not None:
            self._changed.set()

    def _identify(self, identity: BluetoothIdentity) -> None:
        if identity != self.identity:
            self.logger.info(
                "Bluetooth bridge: the device here is %r (%s), class %#08x",
                identity.name,
                identity.address or "?",
                identity.class_of_device,
            )
            self.identity = identity
            if self.running:
                self._send(hello(identity))

    def set_identity(self, identity: BluetoothIdentity, pinned: set[str] | None = None) -> None:
        """Say outright what the device here is. What is said so (`pinned`: all of it, if not given) stays:
        nothing learned replaces it."""
        self.pinned |= {"name", "address", "class_of_device"} if pinned is None else pinned
        self._identify(identity)

    def learn(self, **found) -> None:
        """Take what was found out about the device here (from the device itself), except what was said
        outright and what was not found."""
        changes = {
            name: value
            for name, value in found.items()
            if value is not None and value != "" and name not in self.pinned
        }
        if changes:
            self._identify(BluetoothIdentity.model_validate({**self.identity.model_dump(), **changes}))

    # ---- the radio -------------------------------------------------------------------------------

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
            sc=False, mitm=False, bonding=True, delegate=_Agree(self)
        )
        # everything above the link is relayed, none of it answered
        device.l2cap_channel_manager.on_pdu = self.heard  # ty: ignore[invalid-assignment]
        device.on("connection", self.linked)
        device.host.on("connection_request", self._device_calls)
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

    async def _rename(self, device: Device, persona: BluetoothIdentity) -> None:
        """Carry another name and class from now on, without leaving the radio: the far device was renamed, or
        its real name was found out. A link that is up stays up."""
        device.name, device.class_of_device = persona.name, persona.class_of_device
        device.inquiry_response = None  # (made anew from the name, below)
        await device.host.send_sync_command(hci.HCI_Write_Local_Name_Command(local_name=persona.name.encode("utf8")))
        await device.host.send_sync_command(
            hci.HCI_Write_Class_Of_Device_Command(class_of_device=persona.class_of_device)
        )
        await device.set_discoverable(True)
        self.logger.info("Bluetooth bridge: now %r in the radio of %r", persona.name, self.identity.name)

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
            self.opened_by, self.fresh, self._told_complete, self._device_asked = None, False, False, False
            self.closing = False
            self._to(OFF, "the half left the radio")

    async def _be(self, transport: Any, lost) -> None:
        """Be the far device in this radio, under whatever name it has, until the radio is gone."""
        persona = self.persona
        assert persona is not None
        device = self._build(transport, persona)
        try:
            # nothing the bridge starts here is left behind: what has not finished by the end is stopped, and
            # waited for
            async with create_task_group() as work:
                self._work = work
                try:
                    await self._put_on_air(device)
                    self.device = device
                    self._to(IDLE, f"{persona.name!r} is in the radio of {self.identity.name!r}")
                    while not lost.done():
                        if self.persona is not None and _on_air(self.persona) != _on_air(persona):
                            persona = self.persona
                            await self._rename(device, persona)
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
                await self._be(transport, lost)
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
        self._changed, self._news = anyio.Event(), anyio.Event()
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
            "state": self.state,
            "linked": self.link is not None,
            "opened_by": self.opened_by or "",
            "calling": self.reaching,
            "pairing": self.state == PAIRING_NOW,
            "encrypted": self.secure(),
            "bonded": self.bonded(),
            "paired": self.paired(),
            "far": {"link": self.far_link or "", "pairing": self.far_pairing or ""},
            "persona": self.persona.name if self.persona else "",
            "device_up": self.device_up,
            "call_backoff": round(max(0.0, self._call_after - time.monotonic()), 1),
            "held": len(self.held),
            **self.stats,
            "last_error": self.last_error,
            "error": self.error,
        }
