"""Pairing through the bridge, as two people at two devices see it.

There are two links and two pairings: each device pairs with the half in its own radio. These tests are about
the halves making that behave as one pairing: both devices ask, both have to agree, and either can refuse.
A device here is a bumble host with a dialog (``Dialog``): what it shows is counted, and a test presses yes
or no.
"""

import anyio
import pytest
from bumble import hci
from bumble.core import PhysicalTransport

from .bluetooth_bridge import CALLING, IDLE, SECURE
from .bluetooth_bridge_test import HEAD_UNIT, IN_HEAD_UNITS_RADIO, Bridged, joined, kept, running, until
from .bluetooth_testing import Air, VirtualController

pytestmark = pytest.mark.anyio


class Attempt:
    """A person picking the other device in a device's Bluetooth settings: it runs until the pairing is made
    or has failed."""

    def __init__(self, tasks, air: Air) -> None:
        self.link = None
        self.error: BaseException | None = None
        self.done = anyio.Event()
        tasks.start_soon(self._run, air)

    async def _run(self, air: Air) -> None:
        try:
            self.link = await air.pair()
        except Exception as error:  # noqa: BLE001 - the test looks at it
            self.error = error
        except anyio.get_cancelled_exc_class() as error:
            self.error = error  # (bumble ends a pairing whose link went away with a cancellation)
        self.done.set()

    async def finished(self, timeout: float = 10):
        with anyio.fail_after(timeout):
            await self.done.wait()
        return self


async def bridged_and_on_air(**half):
    bridged = Bridged(**half)
    await bridged.power_on()
    return bridged, bridged.phone_air, bridged.head_unit_air, bridged.beside_phone, bridged.beside_head_unit


async def hung_up(*airs: Air) -> None:
    await until(lambda: not any(air.device.connections for air in airs))


async def test_when_the_phone_starts_both_devices_ask_and_both_have_to_agree():
    bridged, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    phone.dialog.answer = head_unit.dialog.answer = None  # nobody has pressed anything yet
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = Attempt(tasks, phone)
            # The phone opened a link to the half in its radio. The half in the head unit's radio calls the
            # head unit at once: both devices show their dialog, before anyone has agreed to anything.
            await until(lambda: phone.dialog.shown == 1 and head_unit.dialog.shown == 1)
            assert (beside_phone.opened_by, beside_head_unit.opened_by) == ("device", "bridge")
            assert beside_phone.status()["pairing"] and beside_head_unit.status()["pairing"]
            assert not beside_phone.status()["paired"]

            # The person at the phone agrees. That is not a pairing yet: the head unit has not agreed.
            phone.dialog.press(True)
            await anyio.sleep(0.3)
            assert not attempt.done.is_set() and not phone.keys() and not beside_phone.status()["encrypted"]

            # The person at the head unit agrees: both pairings are made.
            head_unit.dialog.press(True)
            await attempt.finished()
            assert attempt.error is None
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        assert (beside_phone.state, beside_head_unit.state) == (SECURE, SECURE)
        assert phone.keys() == kept(beside_phone) and head_unit.keys() == kept(beside_head_unit)
        assert len(phone.keys()) == 1 and len(head_unit.keys()) == 1
        for half in (beside_phone, beside_head_unit):
            status = half.status()
            assert status["bonded"] and status["paired"] and not status["calling"] and not status["pairing"]
            assert status["last_error"] == "" and not half.fresh
        # ... and what they say above the link crosses.
        await bridged.phone_uses_the_service(attempt.link)


async def test_when_the_head_unit_starts_it_is_the_same_the_other_way_round():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    phone.dialog.answer = head_unit.dialog.answer = None
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = Attempt(tasks, head_unit)
            await until(lambda: phone.dialog.shown == 1 and head_unit.dialog.shown == 1)
            assert (beside_phone.opened_by, beside_head_unit.opened_by) == ("bridge", "device")
            head_unit.dialog.press(True)
            await anyio.sleep(0.3)
            assert not attempt.done.is_set() and not head_unit.keys()
            phone.dialog.press(True)
            await attempt.finished()
            assert attempt.error is None
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        assert phone.keys() == kept(beside_phone) and head_unit.keys() == kept(beside_head_unit)


async def test_when_both_start_at_once_each_pairs_with_its_own_half_and_nobody_waits():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            from_phone, from_head_unit = Attempt(tasks, phone), Attempt(tasks, head_unit)
            await from_phone.finished()
            await from_head_unit.finished()
        assert from_phone.error is None and from_head_unit.error is None
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        # one link in each radio, each opened by its device: neither half called over the other's call
        assert len(phone.device.connections) == 1 and len(head_unit.device.connections) == 1
        assert (beside_phone.opened_by, beside_head_unit.opened_by) == ("device", "device")
        assert (phone.dialog.shown, head_unit.dialog.shown) == (1, 1)
        assert phone.keys() == kept(beside_phone) and head_unit.keys() == kept(beside_head_unit)


async def test_when_the_other_person_refuses_nothing_is_paired_on_either_side():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    head_unit.dialog.answer = False
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = await Attempt(tasks, phone).finished()
        # The phone's own pairing fails, though its person agreed: the head unit's did not.
        assert attempt.error is not None
        await hung_up(phone, head_unit)
        await until(lambda: beside_phone.state == IDLE and beside_head_unit.state == IDLE)
        assert (phone.dialog.shown, head_unit.dialog.shown) == (1, 1)
        assert not phone.keys() and not head_unit.keys() and not kept(beside_phone) and not kept(beside_head_unit)
        assert "'Head Unit' did not pair" in beside_head_unit.status()["last_error"]
        assert "did not pair" in beside_phone.status()["last_error"]
        assert beside_head_unit.status()["call_backoff"] == 0  # (it answered: it may be called again at once)

        # Nothing is left in the way of trying again: not the refusal either, which the far half said last
        # of all (after it had said its link was gone), and which is not an answer to the next try.
        beside_phone.far_pairing = "failed"  # (as on rootcanal, where it is said after the hang-up)
        phone.dialog.answer = head_unit.dialog.answer = None
        async with anyio.create_task_group() as tasks:
            attempt = Attempt(tasks, phone)
            await until(lambda: phone.dialog.shown == 2 and head_unit.dialog.shown == 2)
            head_unit.dialog.press(True)  # (this time the called device's person is the first to agree)
            await until(lambda: beside_phone.far_pairing == "complete")
            phone.dialog.press(True)
            await attempt.finished()
        assert attempt.error is None
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())


async def test_when_the_person_who_started_refuses_the_other_device_is_let_go_and_keeps_no_key():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    phone.dialog.answer = None
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = Attempt(tasks, phone)
            # The head unit's person agrees at once; its pairing with the half is made.
            await until(lambda: beside_head_unit.secure() and kept(beside_head_unit))
            assert beside_head_unit.fresh and not beside_head_unit.paired()
            # Then the person at the phone says no.
            phone.dialog.press(False)
            await attempt.finished()
        assert attempt.error is not None
        # The half-made pairing in the head unit's radio is undone: hung up, and its key forgotten.
        await hung_up(head_unit)
        await until(lambda: not kept(beside_head_unit) and beside_head_unit.state == IDLE)
        assert not phone.keys() and not kept(beside_phone)
        assert "did not pair" in beside_head_unit.status()["last_error"]


async def test_when_the_other_person_does_not_answer_in_time_the_pairing_is_called_off():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air(pair_timeout=0.5)
    head_unit.dialog.answer = None  # nobody is at the head unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = await Attempt(tasks, phone).finished()
            assert attempt.error is not None
            assert "did not agree to pair within" in beside_phone.status()["last_error"]
            # the head unit's dialog goes away with its link
            await hung_up(head_unit)
            head_unit.dialog.press(False)
        assert not phone.keys() and not kept(beside_phone) and not kept(beside_head_unit)


async def test_devices_that_are_paired_reconnect_from_either_side_without_being_asked_again():
    bridged, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        link = await phone.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        keys = (phone.keys(), head_unit.keys())
        await link.disconnect()
        await hung_up(phone, head_unit)

        # The phone connects again: the head unit is called, and both links are authenticated with the keys.
        link = await phone.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        await bridged.phone_uses_the_service(link)
        await link.disconnect()
        await hung_up(phone, head_unit)

        # The head unit connects again.
        link = await head_unit.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        assert (beside_phone.opened_by, beside_head_unit.opened_by) == ("bridge", "device")
        await link.disconnect()
        await hung_up(phone, head_unit)

        assert (phone.dialog.shown, head_unit.dialog.shown) == (1, 1)  # asked once, at the first pairing
        assert (phone.keys(), head_unit.keys()) == keys


@pytest.mark.parametrize("starts", ["the one that forgot", "the other"])
async def test_a_device_that_forgot_the_other_pairs_with_it_anew(starts):
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        link = await phone.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        (old,) = phone.keys()
        await link.disconnect()
        await hung_up(phone, head_unit)

        phone.forget()  # "Forget" on the phone
        link = await (phone if starts == "the one that forgot" else head_unit).pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        # The phone was asked again and has a new key, the same as the half in its radio. The head unit,
        # which had forgotten nothing, was not asked.
        assert (phone.dialog.shown, head_unit.dialog.shown) == (2, 1)
        assert phone.keys() == kept(beside_phone) and phone.keys() != [old]
        assert head_unit.keys() == kept(beside_head_unit)
        await link.disconnect()
        await hung_up(phone, head_unit)


async def test_a_device_whose_bluetooth_is_restarting_does_not_keep_the_other_waiting():
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air(call_timeout=0.3)
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        head_unit.out_of_reach()
        async with anyio.create_task_group() as tasks:
            # The call to the head unit is given up after `call_timeout`, and the phone is told at once.
            attempt = await Attempt(tasks, phone).finished(timeout=5)
        assert attempt.error is not None
        await hung_up(phone)
        status = beside_head_unit.status()
        assert status["state"] == IDLE and not status["calling"] and status["call_backoff"] > 0
        assert "could not reach 'Head Unit'" in status["last_error"]

        # It is not called again straight away. A phone that tries again is told so, and is not left hanging.
        calls = status["calls"]
        async with anyio.create_task_group() as tasks:
            attempt = await Attempt(tasks, phone).finished(timeout=5)
        assert attempt.error is not None
        assert beside_head_unit.status()["calls"] == calls
        await hung_up(phone)

        # Its Bluetooth is back (whoever watches the device says so): the next try goes through.
        head_unit.in_reach()
        beside_head_unit.device_came_up()
        link = await phone.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        await link.disconnect()


async def test_a_device_that_calls_while_the_half_is_calling_has_opened_its_own_link():
    """The half is in the middle of a call that nobody answers (the head unit's Bluetooth restarted, and came
    back under the address it is calling from now) when the head unit calls the half. The link is the head
    unit's own: what the far device says is passed on at once, not held until the bridge has encrypted a link
    it did not open, which it never would."""
    stale = HEAD_UNIT.model_copy(update={"address": "F0:F0:F0:F0:F0:99"})
    bridged = Bridged(head_unit=stale, call_timeout=1)
    await bridged.power_on()
    gone = VirtualController("gone", link=bridged.head_unit_air.link, public_address=stale.address)
    gone.deaf = True  # (where the head unit was: nothing answers there)
    head_unit, beside_phone, beside_head_unit = bridged.head_unit_air, bridged.beside_phone, bridged.beside_head_unit
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        assert beside_head_unit._call("a test")
        await until(lambda: beside_head_unit.state == CALLING)
        link = await head_unit.device.connect(IN_HEAD_UNITS_RADIO, transport=PhysicalTransport.BR_EDR, timeout=5)
        await until(lambda: beside_head_unit.link is not None)
        assert beside_head_unit.reaching  # (the call is still being placed)
        assert beside_head_unit.opened_by == "device" and not beside_head_unit.called
        assert beside_head_unit.ready()
        # The call is given up; the link the head unit opened is not taken down with it.
        await until(lambda: not beside_head_unit.reaching)
        await anyio.sleep(0.2)
        assert beside_head_unit.link is not None and beside_head_unit.state != IDLE
        await link.disconnect()


async def test_a_device_that_takes_the_lead_of_a_link_it_was_called_on_has_not_opened_it():
    """An Android that is called asks to lead the link as it accepts it. The link is still one the bridge
    opened: the bridge pairs it, and the far half, whose device started, waits for that."""
    _, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    linked = beside_head_unit.linked

    def linked_and_led_by_the_device(connection) -> None:
        connection.role = hci.Role.PERIPHERAL  # (as after the role switch)
        linked(connection)

    beside_head_unit.linked = linked_and_led_by_the_device
    phone.dialog.answer = head_unit.dialog.answer = None
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            attempt = Attempt(tasks, phone)
            await until(lambda: phone.dialog.shown == 1 and head_unit.dialog.shown == 1)
            assert (beside_phone.opened_by, beside_head_unit.opened_by) == ("device", "bridge")
            phone.dialog.press(True)
            head_unit.dialog.press(True)
            await attempt.finished()
        assert attempt.error is None
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())


@pytest.mark.parametrize("starts", ["phone", "head unit"])
async def test_a_device_that_pairs_and_hangs_up_as_an_android_does_is_paired_all_the_same(starts):
    bridged, phone, head_unit, beside_phone, beside_head_unit = await bridged_and_on_air()
    first, other = (phone, head_unit) if starts == "phone" else (head_unit, phone)
    phone.dialog.answer = head_unit.dialog.answer = None
    async with running(beside_phone, beside_head_unit), joined(beside_phone, beside_head_unit):
        await until(lambda: beside_phone.device and beside_head_unit.device)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(first.bond)
            await until(lambda: phone.dialog.shown == 1 and head_unit.dialog.shown == 1)
            other.dialog.press(True)
            await anyio.sleep(0.2)
            first.dialog.press(True)
        # It paired and hung up without ever encrypting the link. Both pairings stand: each device and its
        # half keep the same key, and the other device is hung up on, as the conversation is over.
        await hung_up(phone, head_unit)
        await until(lambda: len(kept(beside_phone)) == 1 and len(kept(beside_head_unit)) == 1)
        assert phone.keys() == kept(beside_phone) and head_unit.keys() == kept(beside_head_unit)
        assert beside_phone.status()["bonded"] and beside_head_unit.status()["bonded"]
        assert beside_phone.status()["last_error"] == "" and beside_head_unit.status()["last_error"] == ""

        # It comes back for what it wants: nobody is asked again.
        link = await phone.pair()
        await until(lambda: beside_phone.paired() and beside_head_unit.paired())
        await bridged.phone_uses_the_service(link)
        assert (phone.dialog.shown, head_unit.dialog.shown) == (1, 1)
