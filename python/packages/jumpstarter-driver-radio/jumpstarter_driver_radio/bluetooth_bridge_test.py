"""Two radios that do not share the air, each with a device and a bridge half in it. Here the two halves are
joined directly; ``forwards_test.py`` joins them through the exporter's forwards."""

from contextlib import asynccontextmanager

import anyio
import pytest
from bumble import l2cap
from bumble.core import PhysicalTransport

from .bluetooth_bridge import (
    BYE,
    HANGUP,
    HELLO,
    L2CAP,
    ROLE_SWITCH_ONLY,
    BridgeHalf,
    hello,
    read_hello,
    record,
    records,
)
from .bluetooth_testing import Air, Inbox
from .common import BluetoothIdentity
from jumpstarter.streams.common import forward_stream

pytestmark = pytest.mark.anyio

PHONE = BluetoothIdentity(name="Phone", address="F0:F0:F0:F0:F0:01", class_of_device=0x5A020C)
HEAD_UNIT = BluetoothIdentity(name="Head Unit", address="F0:F0:F0:F0:F0:02", class_of_device=0x200408)
IN_PHONES_RADIO, IN_HEAD_UNITS_RADIO = "F0:F0:F0:F0:F0:AA", "F0:F0:F0:F0:F0:BB"
PSM = 0x1001


async def until(what, timeout: float = 10):
    with anyio.fail_after(timeout):
        while not what():
            await anyio.sleep(0.02)


class Bridged:
    """A phone and a head unit, each in a radio of its own with a bridge half."""

    def __init__(self, phone: BluetoothIdentity = PHONE, head_unit: BluetoothIdentity = HEAD_UNIT, **half) -> None:
        self.phone_air = Air(PHONE, IN_PHONES_RADIO)
        self.head_unit_air = Air(HEAD_UNIT, IN_HEAD_UNITS_RADIO)
        half.setdefault("retry", 0.05)
        self.beside_phone = BridgeHalf(open_radio=self.phone_air.open_radio, identity=phone, **half)
        self.beside_head_unit = BridgeHalf(open_radio=self.head_unit_air.open_radio, identity=head_unit, **half)
        self.received = Inbox()

    async def power_on(self) -> None:
        await self.phone_air.power_on()
        await self.head_unit_air.power_on()

        def answer(channel) -> None:
            def sink(data: bytes) -> None:
                self.received.put(data)
                channel.send_pdu(data.upper())

            channel.sink = sink

        # the head unit has a service, as a profile would be
        self.head_unit_air.device.create_l2cap_server(l2cap.ClassicChannelSpec(psm=PSM), handler=answer)

    async def phone_calls(self):
        return await self.phone_air.device.connect(IN_PHONES_RADIO, transport=PhysicalTransport.BR_EDR, timeout=5)

    async def phone_uses_the_service(self, link) -> None:
        with anyio.fail_after(10):
            channel = await link.create_l2cap_channel(l2cap.ClassicChannelSpec(psm=PSM))
            answers = Inbox()
            channel.sink = answers.put
            channel.send_pdu(b"hello")
            assert await self.received.get() == b"hello"
            assert await answers.get() == b"HELLO"


def kept(half: BridgeHalf) -> list[bytes]:
    """The link keys a half has kept."""
    return [keys.link_key.value for keys in half.keys.all_keys.values() if keys.link_key is not None]


@asynccontextmanager
async def running(*halves: BridgeHalf):
    async with anyio.create_task_group() as tasks:
        for half in halves:
            tasks.start_soon(half.run)
        try:
            yield tasks
        finally:
            tasks.cancel_scope.cancel()


@asynccontextmanager
async def joined(one: BridgeHalf, other: BridgeHalf):
    """A path between two halves, as a forward of the lease makes it."""

    async def carry(ours, theirs) -> None:
        async with forward_stream(ours, theirs):
            pass

    async with one.path() as ours, other.path() as theirs, anyio.create_task_group() as tasks:
        tasks.start_soon(carry, ours, theirs)
        try:
            yield
        finally:
            tasks.cancel_scope.cancel()


# ---- the path's wire format ----------------------------------------------------------------------


def test_records_are_length_prefixed_and_survive_any_chunking():
    stream = hello(PHONE) + record(L2CAP, b"\x00\x40payload") + record(HANGUP) + record(BYE)
    buffer, found = bytearray(), []
    for byte in stream:
        buffer.append(byte)
        found += list(records(buffer))
    assert [kind for kind, _ in found] == [HELLO, L2CAP, HANGUP, BYE]
    assert read_hello(found[0][1]) == (PHONE, 2)
    assert found[1][1] == b"\x00\x40payload"
    assert not buffer


def test_a_record_that_cannot_be_one_is_refused():
    with pytest.raises(ValueError, match="not one of a Bluetooth bridge"):
        list(records(bytearray(b"\xff\xff\xff\xff\x01")))
    with pytest.raises(ValueError, match="version"):
        read_hello(b'{"version": 99, "name": "x"}')


def test_an_identity_checks_its_address_and_class():
    assert BluetoothIdentity(name="x", address="aa:bb:cc:dd:ee:ff").address == "AA:BB:CC:DD:EE:FF"
    with pytest.raises(ValueError):
        BluetoothIdentity(name="x", address="not an address")
    with pytest.raises(ValueError):
        BluetoothIdentity(name="x", class_of_device=1 << 24)


# ---- two halves ----------------------------------------------------------------------------------


async def test_each_half_is_in_its_radio_under_the_other_devices_name():
    bridged = Bridged()
    await bridged.power_on()
    async with running(bridged.beside_phone, bridged.beside_head_unit):
        await until(lambda: bridged.beside_phone.attached and bridged.beside_head_unit.attached)
        # Without a path there is nobody to be: the halves have their radios, and are not on the air.
        assert bridged.beside_phone.status()["on_air"] is False
        assert bridged.beside_phone.status()["peer"] is False

        async with joined(bridged.beside_phone, bridged.beside_head_unit):
            await until(lambda: bridged.beside_phone.device and bridged.beside_head_unit.device)
            assert bridged.beside_phone.persona == HEAD_UNIT and bridged.beside_head_unit.persona == PHONE
            in_phones_radio = bridged.phone_air.bridge_controller
            in_head_units_radio = bridged.head_unit_air.bridge_controller
            assert in_phones_radio.local_name == "Head Unit"
            assert in_phones_radio.class_of_device == HEAD_UNIT.class_of_device
            assert in_head_units_radio.local_name == "Phone"
            assert in_head_units_radio.class_of_device == PHONE.class_of_device
            # no link may sleep, and the devices may swap who leads it
            assert in_phones_radio.default_link_policy == ROLE_SWITCH_ONLY
            status = bridged.beside_phone.status()
            assert status["peer"] and status["on_air"] and status["persona"] == "Head Unit"


async def test_what_one_device_says_above_the_link_the_other_hears():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)

        # The phone finds "Head Unit" in its radio and calls it. That is the half beside the phone.
        link = await bridged.phone_calls()
        await until(lambda: beside_phone.link is not None)
        assert not beside_phone.called

        # It opens a channel to a service. The half beside the head unit calls the head unit for it, pairs
        # with it and encrypts the link, and only then passes on what the phone said.
        await bridged.phone_uses_the_service(link)
        assert beside_head_unit.called and beside_head_unit.secure()
        (head_units_link,) = bridged.head_unit_air.device.connections.values()
        assert head_units_link.peer_address.to_string(False) == IN_HEAD_UNITS_RADIO
        assert head_units_link.is_encrypted

        # The links are kept awake, each beside its own radio.
        await until(lambda: bridged.head_unit_air.bridge_controller.link_policies)
        assert set(bridged.head_unit_air.bridge_controller.link_policies.values()) == {ROLE_SWITCH_ONLY}
        assert set(bridged.phone_air.bridge_controller.link_policies.values()) == {ROLE_SWITCH_ONLY}

        for half in (beside_phone, beside_head_unit):
            status = half.status()
            assert status["linked"] and status["heard"] > 0 and status["sent"] > 0 and status["dropped"] == 0

        # One conversation: when the phone hangs up, the head unit is hung up on.
        await link.disconnect()
        await until(lambda: not bridged.head_unit_air.device.connections)
        assert beside_phone.link is None and beside_head_unit.link is None


async def head_unit_uses_a_service_of_the_phone(bridged: Bridged) -> None:
    """The head unit calls "Phone" in its radio and opens a channel: the half beside the phone calls the
    phone for it, and what the head unit says is held until that link is secure."""
    link = await bridged.head_unit_air.device.connect(
        IN_HEAD_UNITS_RADIO, transport=PhysicalTransport.BR_EDR, timeout=5
    )
    with anyio.fail_after(10):
        channel = await link.create_l2cap_channel(l2cap.ClassicChannelSpec(psm=PSM + 2))
        answers = Inbox()
        channel.sink = answers.put
        channel.send_pdu(b"hello")
        assert await answers.get() == b"HELLO"
    await link.disconnect()
    await until(lambda: not bridged.phone_air.device.connections and not bridged.head_unit_air.device.connections)


async def test_a_device_that_forgot_its_pairing_is_paired_with_anew():
    bridged = Bridged()
    await bridged.power_on()

    def answer(channel) -> None:
        channel.sink = lambda data: channel.send_pdu(data.upper())

    bridged.phone_air.device.create_l2cap_server(l2cap.ClassicChannelSpec(psm=PSM + 2), handler=answer)
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)

        # The head unit starts: the half beside the phone calls the phone and pairs with it. Both keep a key.
        await head_unit_uses_a_service_of_the_phone(bridged)
        (first,) = kept(beside_phone)
        assert bridged.phone_air.keys() == [first]

        # Called again, the phone is authenticated with that key: nobody pairs twice.
        await head_unit_uses_a_service_of_the_phone(bridged)
        assert kept(beside_phone) == [first]

        # The phone is made to forget its pairings (a factory reset of its Bluetooth). The half still has
        # the key, and the phone refuses a link authenticated with it. The half forgets the key too, calls
        # again and pairs anew; what the head unit said meanwhile is not lost, and the head unit is not hung
        # up on.
        bridged.phone_air.forget()
        await head_unit_uses_a_service_of_the_phone(bridged)
        (second,) = kept(beside_phone)
        assert second != first
        assert bridged.phone_air.keys() == [second]
        assert beside_phone.status()["dropped"] == 0 and not beside_phone.recalling


async def test_a_link_that_cannot_be_made_secure_is_hung_up():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit

    async def refuse(_connection) -> None:
        raise RuntimeError("the phone's radio refuses")

    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        assert beside_phone.device is not None
        beside_phone.device.authenticate = refuse  # ty: ignore[invalid-assignment]
        called = []
        bridged.phone_air.device.on("connection", called.append)
        link = await bridged.head_unit_air.device.connect(
            IN_HEAD_UNITS_RADIO, transport=PhysicalTransport.BR_EDR, timeout=5
        )
        link.send_l2cap_pdu(0x0040, b"anything")
        # The half beside the phone calls the phone and cannot authenticate the link. It does not stay
        # there, linked and holding what was said: the phone is hung up on, and can be called again.
        await until(lambda: called)
        await until(lambda: not bridged.phone_air.device.connections)
        await until(lambda: beside_phone.link is None and not beside_phone.reaching)
        assert beside_phone.status()["held"] == 0


async def test_a_device_that_calls_first_is_where_the_half_calls_it_later():
    # Nobody said what the phone's address is: the half beside it cannot call it, until the phone has called.
    bridged = Bridged(phone=PHONE.model_copy(update={"address": ""}))
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        assert beside_head_unit.persona == PHONE.model_copy(update={"address": ""})
        await bridged.phone_calls()
        await until(lambda: beside_head_unit.persona == PHONE)
        assert beside_phone.identity == PHONE
        # (learning the address is not a new persona: the half beside the head unit stayed on the air)
        assert bridged.head_unit_air.ports[-1].closed is False and len(bridged.head_unit_air.ports) == 1


async def test_when_the_far_half_is_stopped_the_device_here_is_hung_up_on():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone), joined(beside_phone, beside_head_unit):
        async with running(beside_head_unit):
            await until(lambda: beside_phone.device and beside_head_unit.device)
            link = await bridged.phone_calls()
            await bridged.phone_uses_the_service(link)
        # The half beside the head unit was stopped: it hung up its link and said so.
        await until(lambda: not bridged.head_unit_air.device.connections)
        await until(lambda: not bridged.phone_air.device.connections)
        assert beside_phone.status()["peer"] is False
        assert beside_head_unit.status()["running"] is False
        # ... and it is said why there is no far end: the far half is there, stopped. (The path is open.)
        assert beside_phone.status()["far"]["stopped"] is True
        # The half beside the phone stays in the radio under the name it had: the phone still knows it.
        assert beside_phone.device is not None

        # Started again, the far half says who it is, and the bridge is whole again.
        async with running(beside_head_unit):
            await until(lambda: beside_phone.joined and beside_head_unit.device)
            assert beside_phone.status()["far"]["stopped"] is False
            link = await bridged.phone_calls()
            await bridged.phone_uses_the_service(link)


async def test_when_the_path_is_cut_both_devices_are_hung_up_on():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit):
        async with joined(beside_phone, beside_head_unit):
            await until(lambda: beside_phone.device and beside_head_unit.device)
            link = await bridged.phone_calls()
            await bridged.phone_uses_the_service(link)
        await until(lambda: not bridged.phone_air.device.connections)
        await until(lambda: not bridged.head_unit_air.device.connections)
        assert not beside_phone.joined and not beside_head_unit.joined


async def test_a_radio_that_went_away_is_looked_for_again():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        first = bridged.phone_air.ports[-1]
        first.lose()  # the phone is power cycled
        await until(lambda: len(bridged.phone_air.ports) == 2 and beside_phone.device is not None)
        assert first.closed
        link = await bridged.phone_calls()
        await bridged.phone_uses_the_service(link)


async def test_a_radio_that_is_not_there_is_an_error_to_read_not_a_failure():
    async def no_radio():
        raise OSError("connection refused")

    half = BridgeHalf(open_radio=no_radio, identity=PHONE, retry=0.05)
    async with running(half):
        await until(lambda: half.error)
        status = half.status()
        assert status["running"] and not status["attached"]
        assert "connection refused" in status["error"]


async def test_what_is_not_a_bridge_at_the_far_end_closes_the_path():
    half = BridgeHalf(open_radio=Air(PHONE, IN_PHONES_RADIO).open_radio, identity=PHONE, retry=0.05)
    async with running(half), half.path() as path:
        assert await path.receive() == hello(PHONE)
        await path.send(b"GET / HTTP/1.1\r\n\r\n")
        with anyio.fail_after(5), pytest.raises(anyio.EndOfStream):
            await path.receive()
        assert "not understood" in half.status()["error"]


async def test_a_device_that_is_renamed_is_carried_under_its_new_name_without_leaving_the_radio():
    bridged = Bridged()
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    in_head_units_radio = bridged.head_unit_air.bridge_controller
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        link = await bridged.phone_calls()
        await bridged.phone_uses_the_service(link)
        assert in_head_units_radio.local_name == "Phone"

        # The phone is found to be called something else (its exporter asked it, or somebody renamed it).
        beside_phone.learn(name="Kirk's Pixel", class_of_device=0x5A0204)
        await until(lambda: in_head_units_radio.local_name == "Kirk's Pixel")
        assert in_head_units_radio.class_of_device == 0x5A0204
        assert beside_head_unit.status()["persona"] == "Kirk's Pixel"
        # The half in the head unit's radio is the same device as before: its link to the head unit is up.
        assert len(bridged.head_unit_air.ports) == 1 and beside_head_unit.secure()
        await bridged.phone_uses_the_service(link)

        # What was said outright is not replaced by what is learned.
        beside_phone.set_identity(beside_phone.identity.model_copy(update={"name": "Phone"}), pinned={"name"})
        await until(lambda: in_head_units_radio.local_name == "Phone")
        beside_phone.learn(name="Something else", address="")
        assert beside_phone.identity.name == "Phone"


async def test_a_device_nothing_can_be_asked_about_is_asked_its_name_over_the_radio():
    # Beside a physical device there is a controller and nothing else: the configured name is a guess.
    bridged = Bridged(phone=PHONE.model_copy(update={"name": "Bench tablet", "address": ""}), learn_name=True)
    await bridged.power_on()
    beside_phone, beside_head_unit = bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        assert bridged.head_unit_air.bridge_controller.local_name == "Bench tablet"
        await bridged.phone_calls()
        # It called: its address is known, and it says what it is called.
        await until(lambda: beside_phone.identity == PHONE)
        await until(lambda: bridged.head_unit_air.bridge_controller.local_name == "Phone")
