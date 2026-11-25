"""Wi-Fi joined by the forwards of a lease (JEP-0016), in one process.

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
from jumpstarter_protocol import jumpstarter_pb2
from jumpstarter_protocol.jumpstarter.v1 import common_pb2

from .medium import WifiMedium
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


PHONE_STATION = bytes.fromhex("7a1610e5e475")  # (a phone's randomized address)
HEAD_UNIT_STATION = bytes.fromhex("aa744a492992")  # (a head unit's access point)


async def test_a_path_between_two_wifi_bridges_puts_two_media_on_one_air(media, monkeypatch):
    monkeypatch.delenv("JUMPSTARTER_LAUNCHER_SOCKET", raising=False)
    phones_medium, head_units_medium = media(), media()
    phone = WifiMedium(api_socket=phones_medium.path, port=free_port(), retry=0.05)
    head_unit = WifiMedium(api_socket=head_units_medium.path, port=free_port(), retry=0.05)
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
