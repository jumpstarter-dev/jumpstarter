"""Radios joined by the forwards of a lease (JEP-0016), in one process.

Each member is an exporter session with the radio driver and the exporter's real forward manager. The
controller and the router are stand-ins, as in the core's own forward tests: the controller passes a DialPeer
on to the other member as a connect instruction, and the router joins the two streams opened with one token.
Nothing here is a client: once the forward is set up, the bytes cross between the two exporters by themselves.
"""

import socket
from contextlib import AsyncExitStack, asynccontextmanager

import anyio
import pytest
from anyio import to_thread
from bumble import l2cap
from bumble.core import PhysicalTransport
from jumpstarter_protocol import jumpstarter_pb2
from jumpstarter_protocol.jumpstarter.v1 import common_pb2

from .bluetooth import RootcanalBluetooth
from .bluetooth_testing import Air, Inbox
from .common import BluetoothIdentity
from .driver import MockRadio
from .identity import MARK
from .wmediumd import WmediumdWifi
from .wmediumd_agent_test import BROADCAST, frame
from jumpstarter.exporter import Session
from jumpstarter.exporter.forwards import ForwardManager
from jumpstarter.streams.common import create_memory_stream

pytestmark = pytest.mark.anyio

LEASE_UID = "lease-uid"
READY = jumpstarter_pb2.FORWARD_END_STATE_READY
CONNECTED = jumpstarter_pb2.FORWARD_END_STATE_CONNECTED
DIRECTIONS = {
    "provides": common_pb2.PORT_DIRECTION_PROVIDES,
    "requires": common_pb2.PORT_DIRECTION_REQUIRES,
    "connects": common_pb2.PORT_DIRECTION_CONNECTS,
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def until(what, timeout: float = 10):
    with anyio.fail_after(timeout):
        while not what():
            await anyio.sleep(0.02)


class Router:
    """Joins the two callers that open a stream with the same token, as the router service does."""

    def __init__(self) -> None:
        self.pending: dict[str, object] = {}

    @asynccontextmanager
    async def connect(self, endpoint: str, token: str):
        if token in self.pending:
            mine = self.pending.pop(token)
        else:
            mine, self.pending[token] = create_memory_stream()
        async with mine:  # ty: ignore[invalid-context-manager]
            yield mine


class Controller:
    """The controller's part for one member: it takes reports, and passes a DialPeer on to the other member."""

    def __init__(self) -> None:
        self.states: dict[str, int] = {}
        self.peer: ForwardManager | None = None
        self.dials = 0

    @asynccontextmanager
    async def stub(self):
        yield self

    async def ReportForward(self, request, timeout=None):
        self.states[request.forward_name] = request.state
        return jumpstarter_pb2.ReportForwardResponse()

    async def DialPeer(self, request, timeout=None):
        assert self.peer is not None
        self.dials += 1
        token = f"{id(self)}-{self.dials}"
        self.peer.connect(
            jumpstarter_pb2.ForwardConnect(
                lease_uid=request.lease_uid,
                forward_name=request.forward_name,
                connection_id=token,
                peer_member_name="other",
                router_endpoint="router.test:443",
                router_token=token,
            )
        )
        return jumpstarter_pb2.DialPeerResponse(
            router_endpoint="router.test:443", router_token=token, connection_id=token
        )


class Member:
    """One member of the lease: an exporter session of one radio driver, and the forward ends of that exporter."""

    def __init__(self, name: str, driver, session: Session, tasks, router: Router) -> None:
        self.name, self.driver = name, driver
        self.controller = Controller()
        self.manager = ForwardManager(
            ports=session.ports,  # (what the session found on its drivers: the ports they declare)
            controller=self.controller.stub,
            router_connect=router.connect,
            tg=tasks,
            report_backoff=0.01,
            path_backoff=0.05,
            peer_join_timeout=5,
        )

    async def setup(self, forward: str, *, initiator: bool = False) -> None:
        (port,) = self.driver.ports
        await self.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_setup=jumpstarter_pb2.ForwardSetup(
                    lease_uid=LEASE_UID,
                    forward_name=forward,
                    member_name=self.name,
                    direction=DIRECTIONS[port.direction],
                    local_port_name=port.name,
                    initiator=initiator,
                )
            )
        )


@asynccontextmanager
async def lease(first, second):
    """A lease with two members, one radio driver each."""
    router = Router()
    async with AsyncExitStack() as stack:
        sessions = []
        for driver in (first, second):
            session = stack.enter_context(Session(root_device=driver))
            await stack.enter_async_context(session.serve_unix_async())
            sessions.append(session)
        tasks = await stack.enter_async_context(anyio.create_task_group())
        one = Member("a", first, sessions[0], tasks, router)
        two = Member("b", second, sessions[1], tasks, router)
        one.controller.peer, two.controller.peer = two.manager, one.manager
        try:
            yield one, two
        finally:
            await one.manager.aclose()
            await two.manager.aclose()
            tasks.cancel_scope.cancel()


async def forward(name: str, one: Member, two: Member) -> None:
    """Set up a forward between the ports of the two members, as the controller does once the lease is bound.
    For a path the member whose name sorts first opens it."""
    await two.setup(name)
    await one.setup(name, initiator=one.driver.ports[0].direction == "connects")


# ---- mock radios ---------------------------------------------------------------------------------


async def test_a_path_between_two_mock_radios_carries_bytes_both_ways():
    async with lease(MockRadio(), MockRadio()) as (one, two):
        await one.driver.start()
        await two.driver.start()
        await forward("air", one, two)
        with anyio.fail_after(10):
            # one says it, two gives it back: the bytes crossed the path twice
            assert await one.driver.exchange("hello over the path") == "hello over the path"
            assert await two.driver.exchange("and the other way") == "and the other way"
        await until(lambda: one.controller.states.get("air") == CONNECTED)
        await until(lambda: two.controller.states.get("air") == CONNECTED)
        assert (await one.driver.status())["peer"] and (await two.driver.status())["peer"]
        assert (await two.driver.status())["heard"] == len("hello over the path") + len("and the other way")
        # only the member that opens the path dialed
        assert (one.controller.dials, two.controller.dials) == (1, 0)


async def test_a_mock_radio_that_requires_reaches_one_that_provides():
    listen = f"127.0.0.1:{free_port()}"
    client, service = MockRadio(direction="requires", listen=listen), MockRadio(direction="provides")
    async with lease(client, service) as (requires, provides):
        await client.start()
        await service.start()
        await forward("echo", requires, provides)
        await until(lambda: requires.controller.states.get("echo") == READY)
        with anyio.fail_after(10):
            assert await client.exchange("is anyone there") == "is anyone there"
            assert await client.exchange("again") == "again"  # (each connection gets a stream of its own)
        assert requires.controller.dials == 2
        assert (await service.status())["heard"] == len("is anyone there") + len("again")


# ---- Wi-Fi ---------------------------------------------------------------------------------------

PHONE_STATION = bytes.fromhex("7a1610e5e475")  # (a phone's randomized address)
HEAD_UNIT_STATION = bytes.fromhex("aa744a492992")  # (a head unit's access point)


async def test_a_path_between_two_wifi_bridges_puts_two_media_on_one_air(media, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    phones_medium, head_units_medium = media(), media()
    phone = WmediumdWifi(api_socket=phones_medium.path, port=free_port(), retry=0.05)
    head_unit = WmediumdWifi(api_socket=head_units_medium.path, port=free_port(), retry=0.05)
    async with lease(phone, head_unit) as (one, two):
        # Nobody starts anything: the bridges came up with their sessions, and the forward joins them.
        await forward("wifi", one, two)
        await until(lambda: phone.status().get("peer") and head_unit.status().get("peer"))
        assert phone.status()["attached"] and head_unit.status()["attached"]

        # The head unit's access point sends a beacon into its medium: the phone's medium hears it, from the
        # bridge's station, on the same channel.
        beacon = frame(BROADCAST, HEAD_UNIT_STATION, b"beacon")
        assert await to_thread.run_sync(head_units_medium.deliver, beacon, 5180)
        assert await to_thread.run_sync(phones_medium.wait_for, lambda: phones_medium.transmitted)
        heard = phones_medium.transmitted[0]
        assert (heard["frame"], heard["freq"]) == (beacon, 5180)
        # ... and frames to that access point now come back to the bridge
        assert HEAD_UNIT_STATION in phones_medium.claimed

        # The phone answers it: the answer crosses the other way.
        probe = frame(HEAD_UNIT_STATION, PHONE_STATION, b"probe")
        assert await to_thread.run_sync(phones_medium.deliver, probe, 5180)
        assert await to_thread.run_sync(head_units_medium.wait_for, lambda: head_units_medium.transmitted)
        assert head_units_medium.transmitted[0]["frame"] == probe

        assert one.controller.states["wifi"] == CONNECTED and two.controller.states["wifi"] == CONNECTED


# ---- Bluetooth -----------------------------------------------------------------------------------

PHONE = BluetoothIdentity(name="Phone", address="F0:F0:F0:F0:F0:01", class_of_device=0x5A020C)
HEAD_UNIT = BluetoothIdentity(name="Head Unit", address="F0:F0:F0:F0:F0:02", class_of_device=0x200408)
PSM = 0x1001


class VirtualRadio(RootcanalBluetooth):
    """The driver, on bumble's in-memory air instead of a rootcanal HCI port."""

    air: Air

    async def _open_radio(self):
        return await self.air.open_radio()


def bluetooth_of(identity: BluetoothIdentity, bridge_address: str, **config) -> VirtualRadio:
    radio = VirtualRadio(
        name=identity.name, address=identity.address, class_of_device=identity.class_of_device, **config
    )
    radio.air = Air(identity, bridge_address)
    return radio


async def test_a_path_between_two_bluetooth_halves_relays_l2cap_between_two_radios():
    phones = bluetooth_of(PHONE, "F0:F0:F0:F0:F0:AA")
    head_units = bluetooth_of(HEAD_UNIT, "F0:F0:F0:F0:F0:BB")
    phone, head_unit = phones.air.device, head_units.air.device
    await phones.air.power_on()
    await head_units.air.power_on()

    received = Inbox()

    def serve(channel) -> None:
        def sink(data: bytes) -> None:
            received.put(data)
            channel.send_pdu(b"head unit heard: " + data)

        channel.sink = sink

    head_unit.create_l2cap_server(l2cap.ClassicChannelSpec(psm=PSM), handler=serve)

    async with lease(phones, head_units) as (one, two):
        # Nobody starts anything or says who is who: each half came up with its session, with the identity
        # of its exporter's configuration.
        await forward("bt", one, two)
        # Over the path each half learns whose name to carry, and goes on the air under it.
        await until(lambda: phones._half.device is not None and head_units._half.device is not None)
        assert await phones.peer() == HEAD_UNIT.model_dump()
        assert await head_units.peer() == PHONE.model_dump()
        assert phones.air.bridge_controller.local_name == "Head Unit"
        assert head_units.air.bridge_controller.local_name == "Phone"
        status = await phones.status()
        assert status["attached"] and status["peer"] and status["on_air"]

        # The phone calls "Head Unit" in its own radio and uses a service of it. The request crosses the
        # path; the half in the head unit's radio calls the head unit as "Phone", pairs, and passes it on.
        with anyio.fail_after(15):
            link = await phone.connect("F0:F0:F0:F0:F0:AA", transport=PhysicalTransport.BR_EDR, timeout=5)
            channel = await link.create_l2cap_channel(l2cap.ClassicChannelSpec(psm=PSM))
            answers = Inbox()
            channel.sink = answers.put
            channel.send_pdu(b"hello")
            assert await received.get() == b"hello"
            assert await answers.get() == b"head unit heard: hello"

        (head_units_link,) = head_unit.connections.values()
        assert head_units_link.is_encrypted
        for radio in (phones, head_units):
            status = await radio.status()
            assert status["linked"] and status["heard"] > 0 and status["sent"] > 0 and status["dropped"] == 0
        assert (await head_units.status())["encrypted"]
        assert one.controller.states["bt"] == CONNECTED and two.controller.states["bt"] == CONNECTED

        # Stopping one radio's half ends the conversation in both radios.
        await head_units.stop()
        await until(lambda: not head_unit.connections and not phone.connections)
        assert (await phones.status())["peer"] is False


async def test_without_autostart_the_path_is_open_but_idle_until_the_radios_are_started():
    phones = bluetooth_of(PHONE, "F0:F0:F0:F0:F0:AA", autostart=False)
    head_units = bluetooth_of(HEAD_UNIT, "F0:F0:F0:F0:F0:BB", autostart=False)
    async with lease(phones, head_units) as (one, two):
        await forward("bt", one, two)
        await until(lambda: one.controller.states.get("bt") == CONNECTED)
        status = await phones.status()
        assert not status["running"] and not status["peer"] and not status["on_air"]
        await phones.start()
        await head_units.start()
        await until(lambda: phones._half.device is not None and head_units._half.device is not None)
        assert (await phones.status())["peer"]


async def test_with_an_identity_source_each_device_sees_the_other_under_its_real_name_and_either_can_pair():
    """The lab's two exporters: each asks its own Android device who it is. No test says anything."""

    def exporter_of(identity: BluetoothIdentity, configured: str, bridge_address: str) -> VirtualRadio:
        radio = VirtualRadio(name=configured, identity_from={"adb": "127.0.0.1:6520", "interval": 0.05})
        radio.air = Air(identity, bridge_address)
        device_class = ",".join(str(byte) for byte in identity.class_of_device.to_bytes(3, "big"))

        async def adb(_arguments: list[str]) -> str:
            manager = f"  state: ON\n  Name: {identity.name}\n"
            return f"{identity.address}\n{MARK}\n{device_class}\n{MARK}\n{identity.name}\n{MARK}\n{manager}"

        assert radio._source is not None
        radio._source._run = adb
        return radio

    phones = exporter_of(PHONE, "Cuttlefish phone", "F0:F0:F0:F0:F0:AA")
    head_units = exporter_of(HEAD_UNIT, "Cuttlefish head unit", "F0:F0:F0:F0:F0:BB")
    await phones.air.power_on()
    await head_units.air.power_on()
    async with lease(phones, head_units) as (one, two):
        await forward("bt", one, two)
        # In each radio the half carries the other device's own name and class, not the configured one.
        await until(lambda: phones.air.bridge_controller.local_name == "Head Unit")
        await until(lambda: head_units.air.bridge_controller.local_name == "Phone")
        assert phones.air.bridge_controller.class_of_device == HEAD_UNIT.class_of_device
        assert await phones.peer() == HEAD_UNIT.model_dump()
        assert await head_units.peer() == PHONE.model_dump()

        # A person pairs from the head unit: the phone is called (its address came from the phone itself),
        # both are asked, and both pairings are made.
        with anyio.fail_after(15):
            link = await head_units.air.pair()
        await until(lambda: phones._half.paired() and head_units._half.paired())
        assert (phones.air.dialog.shown, head_units.air.dialog.shown) == (1, 1)
        status = await phones.status()
        assert status["paired"] and status["bonded"] and status["state"] == "secure"
        assert status["opened_by"] == "bridge" and status["far"] == {"link": "device", "pairing": "complete"}
        await link.disconnect()
