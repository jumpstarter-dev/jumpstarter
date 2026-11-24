"""Tests for the forward ends of an exporter (JEP-0016).

The manager runs against a fake controller stub and a fake router. The local
sides are real TCP sockets on the loopback interface.
"""

import logging
import socket
import time
from contextlib import asynccontextmanager

import anyio
import grpc
import pytest
from anyio import Event, connect_tcp, create_memory_object_stream, create_task_group, fail_after
from jumpstarter_protocol import jumpstarter_pb2, router_pb2, router_pb2_grpc
from jumpstarter_protocol.jumpstarter.v1 import common_pb2

from jumpstarter.common import TemporaryTcpListener
from jumpstarter.common.ports import Port
from jumpstarter.driver import Driver
from jumpstarter.exporter.forwards import (
    FORWARD_LOCAL_CLIENT_TIMEOUT,
    FORWARD_PEER_JOIN_TIMEOUT,
    JOIN_FRAME,
    PROCESS_SEQUENCE,
    ForwardManager,
    ForwardSequence,
)
from jumpstarter.streams.common import create_memory_stream
from jumpstarter.streams.router import RouterStream

pytestmark = pytest.mark.anyio

LEASE_UID = "3f6c1c2e-lease-uid"
READY = jumpstarter_pb2.FORWARD_END_STATE_READY
CONNECTED = jumpstarter_pb2.FORWARD_END_STATE_CONNECTED
FAILED = jumpstarter_pb2.FORWARD_END_STATE_FAILED

PROVIDES = common_pb2.PORT_DIRECTION_PROVIDES
REQUIRES = common_pb2.PORT_DIRECTION_REQUIRES
CONNECTS = common_pb2.PORT_DIRECTION_CONNECTS


def rpc_error(code: grpc.StatusCode, details: str = "") -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details)


def free_address() -> str:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"127.0.0.1:{s.getsockname()[1]}"


async def wait_for(predicate, timeout: float = 5.0):
    with fail_after(timeout):
        while not predicate():
            await anyio.sleep(0.01)


async def read_exactly(stream, size: int) -> bytes:
    data = b""
    with fail_after(5):
        while len(data) < size:
            data += await stream.receive()
    return data


async def expect_closed(stream) -> None:
    """The peer closed the connection: a read ends with EOF or with an error."""
    with fail_after(5):
        with pytest.raises((anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError)):
            while True:
                await stream.receive()


class PortDriver(Driver):
    """A driver that only has ports."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"

    def __post_init__(self):
        super().__post_init__()
        self.closed_ports: list[str] = []

    async def forward_stream_closed(self, port: Port) -> None:
        self.closed_ports.append(port.name)


class FakeRouter:
    """Connects the two callers that open a stream with the same token, as RouterService does."""

    def __init__(self):
        self.pending: dict[str, object] = {}
        self.streams: dict[str, list] = {}
        self.opened: list[str] = []

    @asynccontextmanager
    async def connect(self, endpoint: str, token: str):
        assert endpoint == "router.test:443"
        self.opened.append(token)
        if token in self.pending:
            mine = self.pending.pop(token)
        else:
            mine, other = create_memory_stream()
            self.pending[token] = other
            self.streams[token] = [mine, other]
        async with mine:  # ty: ignore[invalid-context-manager]
            yield mine

    async def cut(self, token: str) -> None:
        """The router goes away: the stream ends for the two peers."""
        for stream in self.streams[token]:
            await stream.aclose()


class FakeController:
    """The controller calls of one exporter: ReportForward and DialPeer."""

    def __init__(self):
        self.reports: list[jumpstarter_pb2.ReportForwardRequest] = []
        self.attempts: list[jumpstarter_pb2.ReportForwardRequest] = []
        self.fail_reports = 0
        self.report_gate: Event | None = None
        self.dials: list[jumpstarter_pb2.DialPeerRequest] = []
        self.dial_error: grpc.aio.AioRpcError | None = None
        # The manager of the exporter that gets the connect instruction.
        self.peer: ForwardManager | None = None
        self.member = "caller"
        self.endpoint = "router.test:443"

    @asynccontextmanager
    async def stub(self):
        yield self

    async def ReportForward(self, request, timeout=None):
        self.attempts.append(request)
        if self.report_gate is not None:
            await self.report_gate.wait()
        if self.fail_reports > 0:
            self.fail_reports -= 1
            raise rpc_error(grpc.StatusCode.UNAVAILABLE, "controller restarting")
        self.reports.append(request)
        return jumpstarter_pb2.ReportForwardResponse()

    async def DialPeer(self, request, timeout=None):
        self.dials.append(request)
        if self.dial_error is not None:
            raise self.dial_error
        connection_id = f"stream-{id(self)}-{len(self.dials)}"
        if self.peer is not None:
            if self.peer.state(request.forward_name) is None:
                raise rpc_error(grpc.StatusCode.FAILED_PRECONDITION, "the forward is not Listening")
            self.peer.connect(
                jumpstarter_pb2.ForwardConnect(
                    lease_uid=request.lease_uid,
                    forward_name=request.forward_name,
                    connection_id=connection_id,
                    peer_member_name=self.member,
                    router_endpoint=self.endpoint,
                    router_token=connection_id,
                )
            )
        return jumpstarter_pb2.DialPeerResponse(
            router_endpoint=self.endpoint, router_token=connection_id, connection_id=connection_id
        )

    def states(self, forward: str = "bt") -> list[int]:
        return [r.state for r in self.reports if r.forward_name == forward]


class End:
    """One exporter: its manager, its controller calls and its driver."""

    def __init__(self, tg, router, ports: list[Port], **kwargs):
        self.controller = FakeController()
        self.driver = PortDriver(ports=ports)
        kwargs.setdefault("report_backoff", 0.01)
        kwargs.setdefault("path_backoff", 0.01)
        self.manager = ForwardManager(
            ports={port.name: (self.driver, port) for port in self.driver.ports},
            controller=self.controller.stub,
            router_connect=router.connect,
            tg=tg,
            **kwargs,
        )

    async def setup(self, port: str, direction, *, forward="bt", member="member", initiator=False):
        await self.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_setup=jumpstarter_pb2.ForwardSetup(
                    lease_uid=LEASE_UID,
                    forward_name=forward,
                    member_name=member,
                    direction=direction,
                    local_port_name=port,
                    initiator=initiator,
                )
            )
        )

    async def teardown(self, forward="bt"):
        await self.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_teardown=jumpstarter_pb2.ForwardTeardown(lease_uid=LEASE_UID, forward_name=forward)
            )
        )

    async def wait_state(self, state, forward="bt"):
        await wait_for(lambda: self.controller.states(forward)[-1:] == [state])


@asynccontextmanager
async def running():
    """A task group for the managers, and the fake router."""
    async with create_task_group() as tg:
        yield tg, FakeRouter()
        tg.cancel_scope.cancel()


@asynccontextmanager
async def local_service(banner: bytes = b"hci:"):
    """A local TCP service: sends a banner, then returns each chunk in upper case."""
    connections = []

    async def handler(stream):
        connections.append(stream)
        async with stream:
            await stream.send(banner)
            async for chunk in stream:
                await stream.send(chunk.upper())

    async with TemporaryTcpListener(handler) as address:
        yield f"{address[0]}:{address[1]}", connections


@asynccontextmanager
async def accepting_service():
    """A local TCP service that hands each accepted connection to the test."""
    tx, rx = create_memory_object_stream[object](8)

    async def handler(stream):
        done = Event()
        async with stream:
            await tx.send((stream, done))
            await done.wait()

    async with TemporaryTcpListener(handler) as address:
        yield f"{address[0]}:{address[1]}", rx


async def connect_local(address: str):
    host, port = address.rsplit(":", 1)
    return await connect_tcp(host, int(port))


# Sequence numbers


def test_sequence_starts_at_the_start_time_in_milliseconds_and_increases():
    before = int(time.time() * 1000)
    sequence = ForwardSequence()
    first = sequence.next()
    assert before <= first <= int(time.time() * 1000)
    assert [sequence.next(), sequence.next()] == [first + 1, first + 2]


async def test_manager_uses_the_sequence_of_the_process():
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="svc", direction="provides", connect="127.0.0.1:9")])
        assert end.manager.sequence is PROCESS_SEQUENCE
        # The first number of the process is not later than now, in milliseconds.
        assert PROCESS_SEQUENCE.next() <= int(time.time() * 1000) + 1000


# The requires side


async def test_requires_setup_opens_the_listener_and_reports_ready():
    listen = free_address()
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        await end.setup("controller", REQUIRES)
        await end.wait_state(READY)

        report = end.controller.reports[0]
        assert (report.lease_uid, report.forward_name) == (LEASE_UID, "bt")
        assert not report.HasField("message")
        assert end.controller.dials == []
        # The listener is open.
        async with await connect_local(listen):
            pass


async def test_connection_dials_the_peer_and_bytes_flow_both_ways():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)
        await provides.wait_state(READY)

        async with await connect_local(listen) as client:
            # The service speaks first: provides -> requires.
            assert await read_exactly(client, 4) == b"hci:"
            # requires -> provides -> requires.
            await client.send(b"reset")
            assert await read_exactly(client, 5) == b"RESET"
            await requires.wait_state(CONNECTED)
            await provides.wait_state(CONNECTED)

            # One DialPeer for the connection, by the requires side only.
            assert [(d.lease_uid, d.forward_name) for d in requires.controller.dials] == [(LEASE_UID, "bt")]
            assert provides.controller.dials == []
            # The two exporters opened the same router stream: the data path is the router.
            assert len(router.opened) == 2 and len(set(router.opened)) == 1

        # The connection closed: the two ends go back to READY.
        await requires.wait_state(READY)
        await provides.wait_state(READY)
        await wait_for(lambda: provides.driver.closed_ports == ["rootcanal"])
        assert requires.driver.closed_ports == ["controller"]


async def test_two_connections_of_one_forward_get_their_own_streams():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as first, await connect_local(listen) as second:
            assert await read_exactly(first, 4) == b"hci:"
            assert await read_exactly(second, 4) == b"hci:"
            await first.send(b"one")
            await second.send(b"second")
            assert await read_exactly(second, 6) == b"SECOND"
            assert await read_exactly(first, 3) == b"ONE"
            assert len(requires.controller.dials) == 2
            assert len(set(router.opened)) == 2
            # One CONNECTED report for the end, not one for each connection.
            assert requires.controller.states().count(CONNECTED) == 1

            await first.aclose()
            await wait_for(lambda: requires.manager._ends[(LEASE_UID, "bt")].connections == 1)
            # A connection is open: the end stays CONNECTED.
            assert requires.controller.states()[-1] == CONNECTED
        await requires.wait_state(READY)


async def test_one_provides_port_serves_two_requires_ends():
    first_listen, second_listen = free_address(), free_address()
    async with running() as (tg, router), local_service() as (service, connections):
        provides = End(tg, router, [Port(name="console", direction="provides", connect=service)])
        logger = End(tg, router, [Port(name="console", direction="requires", listen=first_listen)])
        debugger = End(tg, router, [Port(name="console", direction="requires", listen=second_listen)])
        logger.controller.peer = debugger.controller.peer = provides.manager
        await provides.setup("console", PROVIDES)
        await logger.setup("console", REQUIRES)
        await debugger.setup("console", REQUIRES)
        await logger.wait_state(READY)
        await debugger.wait_state(READY)

        async with await connect_local(first_listen) as a, await connect_local(second_listen) as b:
            assert await read_exactly(a, 4) == b"hci:"
            assert await read_exactly(b, 4) == b"hci:"
            await a.send(b"a")
            await b.send(b"b")
            assert await read_exactly(a, 1) == b"A"
            assert await read_exactly(b, 1) == b"B"
            assert len(connections) == 2


async def test_failed_dial_closes_the_local_connection_and_keeps_the_listener():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        # The peer exporter has no Listen stream.
        requires.controller.dial_error = rpc_error(grpc.StatusCode.UNAVAILABLE, "no Listen stream")
        async with await connect_local(listen) as client:
            await expect_closed(client)
        assert router.opened == []
        assert requires.controller.states() == [READY]

        # The listener is open. A later connection gets a new stream.
        requires.controller.dial_error = None
        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"


async def test_failed_local_service_closes_the_connection_and_keeps_the_forward():
    listen = free_address()
    async with running() as (tg, router):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        # No service listens on the connect address.
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=free_address())])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            await expect_closed(client)
        await requires.wait_state(READY)
        assert FAILED not in provides.controller.states()
        assert FAILED not in requires.controller.states()
        # The local side of the provides end did not open: it sent no join frame,
        # and no end reported a connection that never carried data.
        assert CONNECTED not in provides.controller.states()
        assert CONNECTED not in requires.controller.states()
        assert requires.manager._ends[(LEASE_UID, "bt")].connections == 0
        async with await connect_local(listen):
            pass


async def test_cut_stream_closes_the_local_connection_and_a_later_one_gets_a_new_stream():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            await requires.wait_state(CONNECTED)
            await router.cut(router.opened[0])
            await expect_closed(client)
        await requires.wait_state(READY)
        await provides.wait_state(READY)

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
        assert len(set(router.opened)) == 2


async def test_peer_that_does_not_join_closes_the_local_connection():
    listen = free_address()
    async with running() as (tg, router):
        # DialPeer returns a token, but no peer opens the stream.
        requires = End(
            tg,
            router,
            [Port(name="controller", direction="requires", listen=listen)],
            peer_join_timeout=0.1,
        )
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            await expect_closed(client)
        assert len(router.opened) == 1
        assert CONNECTED not in requires.controller.states()


async def test_address_in_use_reports_failed_with_the_cause():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        listen = f"127.0.0.1:{taken.getsockname()[1]}"
        async with running() as (tg, router):
            end = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
            await end.setup("controller", REQUIRES)
            await end.wait_state(FAILED)

            report = end.controller.reports[-1]
            assert "port 'controller'" in report.message
            assert "in use" in report.message.lower()
            # The local address stays in the exporter.
            assert listen not in report.message and str(taken.getsockname()[1]) not in report.message

            # A repeated setup tries a failed setup again: the address is free now.
            taken.close()
            await end.setup("controller", REQUIRES)
            await end.wait_state(READY)
            assert end.controller.reports[-1].sequence > report.sequence


async def test_setup_for_an_unknown_port_or_a_wrong_direction_reports_failed():
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="rootcanal", direction="provides", connect="127.0.0.1:9")])
        await end.setup("no-such-port", PROVIDES, forward="a")
        await end.setup("rootcanal", REQUIRES, forward="b")
        await end.wait_state(FAILED, "a")
        await end.wait_state(FAILED, "b")
        messages = {r.forward_name: r.message for r in end.controller.reports}
        assert "not declared" in messages["a"]
        assert "provides port, not a requires port" in messages["b"]


async def test_setup_for_a_port_that_is_not_a_stream_socket_reports_failed():
    async with running() as (tg, router):
        end = End(
            tg,
            router,
            [Port(name="someip-sd", direction="connects", transport="datagram")],
        )
        await end.setup("someip-sd", CONNECTS)
        await end.wait_state(FAILED)
        assert "stream transport" in end.controller.reports[-1].message


async def test_log_records_have_the_port_name_and_no_local_address(caplog):
    # A client of the lease can read the log stream of the session.
    caplog.set_level("DEBUG")
    listen, dead = free_address(), free_address()
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        in_use = f"127.0.0.1:{taken.getsockname()[1]}"
        async with running() as (tg, router), local_service() as (service, _):
            requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
            provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
            provides.driver.logger.setLevel("DEBUG")
            broken = End(tg, router, [Port(name="broken", direction="requires", listen=in_use)])
            # The service of this end does not answer.
            gone = End(tg, router, [Port(name="gone", direction="provides", connect=dead)])
            requires.controller.peer = provides.manager
            await provides.setup("rootcanal", PROVIDES)
            await requires.setup("controller", REQUIRES)
            await broken.setup("broken", REQUIRES, forward="broken")
            await gone.setup("gone", PROVIDES, forward="gone")
            await requires.wait_state(READY)
            await broken.wait_state(FAILED, "broken")
            async with await connect_local(listen) as client:
                assert await read_exactly(client, 4) == b"hci:"

            await gone.manager.handle(
                jumpstarter_pb2.ListenResponse(
                    forward_connect=jumpstarter_pb2.ForwardConnect(
                        lease_uid=LEASE_UID, forward_name="gone", router_endpoint="router.test:443", router_token="t"
                    )
                )
            )
            await wait_for(lambda: "Forward gone: connection failed" in caplog.text)

    assert "Forward bt: listening for port controller" in caplog.text
    assert "Forward broken: port 'broken': cannot open the listener" in caplog.text
    assert "Connecting port rootcanal to its local service" in caplog.text
    assert "Forward gone: connection failed: Connection refused" in caplog.text
    for address in (listen, in_use, dead, service):
        assert address not in caplog.text
        assert ":" + address.rsplit(":", 1)[1] not in caplog.text


# The provides side


async def test_provides_side_opens_the_stream_and_the_local_service_on_connect():
    async with running() as (tg, router), local_service() as (service, connections):
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        await provides.setup("rootcanal", PROVIDES)
        await provides.wait_state(READY)
        # The setup only records the forward: nothing connects to the service.
        assert connections == [] and router.opened == []

        await provides.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_connect=jumpstarter_pb2.ForwardConnect(
                    lease_uid=LEASE_UID,
                    forward_name="bt",
                    connection_id="stream-1",
                    peer_member_name="peer",
                    router_endpoint="router.test:443",
                    router_token="token-1",
                )
            )
        )
        await wait_for(lambda: router.opened == ["token-1"])
        # The test is the other caller of the router stream.
        async with router.connect("router.test:443", "token-1") as remote:
            await remote.send(b"")
            data = b""
            with fail_after(5):
                while len(data) < 4:
                    data += await remote.receive()
            assert data == b"hci:"
            await remote.send(b"ping")
            with fail_after(5):
                assert await remote.receive() == b"PING"
            await provides.wait_state(CONNECTED)
        await provides.wait_state(READY)
        assert provides.controller.dials == []


async def test_connect_instruction_without_a_setup_is_ignored():
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="rootcanal", direction="provides", connect="127.0.0.1:9")])
        await end.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_connect=jumpstarter_pb2.ForwardConnect(
                    lease_uid=LEASE_UID, forward_name="bt", router_endpoint="router.test:443", router_token="t"
                )
            )
        )
        await anyio.sleep(0.05)
        assert router.opened == []
        assert end.controller.reports == []


async def test_a_client_connection_is_not_a_forward_instruction():
    async with running() as (tg, router):
        end = End(tg, router, [])
        handled = await end.manager.handle(
            jumpstarter_pb2.ListenResponse(router_endpoint="router.test:443", router_token="token")
        )
        assert handled is False
        assert end.manager._reporter_scope is None


async def provides_with_connect(router, service: str, tg, token: str = "token-1"):
    """A provides end that got the connect instruction of one connection."""
    provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
    await provides.setup("rootcanal", PROVIDES)
    await provides.wait_state(READY)
    await provides.manager.handle(
        jumpstarter_pb2.ListenResponse(
            forward_connect=jumpstarter_pb2.ForwardConnect(
                lease_uid=LEASE_UID,
                forward_name="bt",
                connection_id="stream-1",
                peer_member_name="peer",
                router_endpoint="router.test:443",
                router_token=token,
            )
        )
    )
    await wait_for(lambda: router.opened == [token])
    return provides


async def test_end_sends_its_join_frame_only_after_its_local_side_is_open():
    hold = Event()

    class SlowDriver(PortDriver):
        @asynccontextmanager
        async def connect_port(self, port):
            await hold.wait()
            async with Driver.connect_port(self, port) as stream:
                yield stream

    async with running() as (tg, router), local_service() as (service, connections):
        provides = End(tg, router, [])
        provides.driver = SlowDriver(ports=[Port(name="rootcanal", direction="provides", connect=service)])
        provides.manager.ports = {port.name: (provides.driver, port) for port in provides.driver.ports}
        await provides.setup("rootcanal", PROVIDES)
        await provides.wait_state(READY)
        await provides.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_connect=jumpstarter_pb2.ForwardConnect(
                    lease_uid=LEASE_UID, forward_name="bt", router_endpoint="router.test:443", router_token="token-1"
                )
            )
        )
        await wait_for(lambda: router.opened == ["token-1"])
        async with router.connect("router.test:443", "token-1") as remote:
            await remote.send(JOIN_FRAME)
            # The local side is not open: no frame, and the end is not CONNECTED.
            with anyio.move_on_after(0.2) as scope:
                await remote.receive()
            assert scope.cancelled_caught
            assert provides.controller.states() == [READY]

            hold.set()
            # The join frame comes first, then the data of the service.
            with fail_after(5):
                assert await remote.receive() == JOIN_FRAME
            assert await read_exactly(remote, 4) == b"hci:"
            await provides.wait_state(CONNECTED)
        assert len(connections) == 1


async def test_first_frame_with_data_is_the_signal_and_the_data():
    async with running() as (tg, router), local_service() as (service, _):
        provides = await provides_with_connect(router, service, tg)
        async with router.connect("router.test:443", "token-1") as remote:
            # A peer that sends data in its first frame, and no empty frame.
            await remote.send(b"ping")
            with fail_after(5):
                assert await remote.receive() == JOIN_FRAME
            # No byte of the first frame is lost.
            assert await read_exactly(remote, 8) == b"hci:PING"
            await provides.wait_state(CONNECTED)
        await provides.wait_state(READY)


async def test_end_is_not_connected_before_the_first_frame_of_the_peer():
    async with running() as (tg, router), local_service() as (service, connections):
        provides = End(
            tg,
            router,
            [Port(name="rootcanal", direction="provides", connect=service)],
            peer_join_timeout=0.3,
        )
        await provides.setup("rootcanal", PROVIDES)
        await provides.wait_state(READY)
        await provides.manager.handle(
            jumpstarter_pb2.ListenResponse(
                forward_connect=jumpstarter_pb2.ForwardConnect(
                    lease_uid=LEASE_UID, forward_name="bt", router_endpoint="router.test:443", router_token="token-1"
                )
            )
        )
        async with router.connect("router.test:443", "token-1") as remote:
            # The end opened its local side and sent its frame. The peer sends none.
            with fail_after(5):
                assert await remote.receive() == JOIN_FRAME
            assert len(connections) == 1
            # After the time limit the end closes the stream.
            await expect_closed(remote)
        assert provides.controller.states() == [READY]
        await wait_for(lambda: provides.manager._ends[(LEASE_UID, "bt")].connections == 0)


def test_the_time_limits_of_a_connection():
    # The time limit of the wire rule, and the shorter one of a path end that waits for a local client.
    assert FORWARD_PEER_JOIN_TIMEOUT == 30.0
    assert FORWARD_LOCAL_CLIENT_TIMEOUT < FORWARD_PEER_JOIN_TIMEOUT
    assert JOIN_FRAME == b""


# Reports


async def test_repeated_setup_reports_again_and_does_not_open_the_listener_again():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)
        end = requires.manager._ends[(LEASE_UID, "bt")]

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            await requires.wait_state(CONNECTED)

            # The exporter opened Listen again: the controller sends the setup again.
            await requires.setup("controller", REQUIRES)
            await wait_for(lambda: requires.controller.states() == [READY, CONNECTED, CONNECTED])
            assert requires.manager._ends[(LEASE_UID, "bt")] is end
            sequences = [r.sequence for r in requires.controller.reports]
            assert sequences == sorted(set(sequences))
            assert FAILED not in requires.controller.states()

            # The open connection was not closed.
            await client.send(b"still")
            assert await read_exactly(client, 5) == b"STILL"


async def test_report_is_sent_again_after_a_failed_rpc():
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="rootcanal", direction="provides", connect="127.0.0.1:9")])
        end.controller.fail_reports = 3
        await end.setup("rootcanal", PROVIDES)
        await end.wait_state(READY)

        assert len(end.controller.attempts) == 4
        # The same state is sent again with its sequence number.
        assert len({a.sequence for a in end.controller.attempts}) == 1
        assert end.controller.states() == [READY]


async def test_an_old_state_is_never_sent_after_a_newer_state():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)

        # The controller does not accept a report.
        gate = requires.controller.report_gate = Event()
        requires.controller.fail_reports = 1
        await requires.setup("controller", REQUIRES)
        await wait_for(lambda: len(requires.controller.attempts) == 1)

        # The state changes while the READY report is not accepted.
        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            await wait_for(lambda: requires.manager.state("bt") == (CONNECTED, ""))
            gate.set()
            requires.controller.report_gate = None
            await requires.wait_state(CONNECTED)

        await requires.wait_state(READY)
        # READY (not accepted), then only newer states: READY is not sent again before CONNECTED.
        attempts = [(a.state, a.sequence) for a in requires.controller.attempts]
        assert [state for state, _ in attempts] == [READY, CONNECTED, READY]
        assert [sequence for _, sequence in attempts] == sorted(sequence for _, sequence in attempts)
        assert requires.controller.states() == [CONNECTED, READY]


@pytest.mark.parametrize(
    "code",
    [grpc.StatusCode.UNIMPLEMENTED, grpc.StatusCode.PERMISSION_DENIED, grpc.StatusCode.INVALID_ARGUMENT],
)
async def test_report_with_a_final_error_is_not_sent_again(code):
    async with running() as (tg, router):
        end = End(tg, router, [Port(name="rootcanal", direction="provides", connect="127.0.0.1:9")])

        async def unimplemented(request, timeout=None):
            end.controller.attempts.append(request)
            raise rpc_error(code)

        end.controller.ReportForward = unimplemented  # ty: ignore[invalid-assignment]
        await end.setup("rootcanal", PROVIDES)
        await wait_for(lambda: len(end.controller.attempts) == 1)
        await anyio.sleep(0.1)
        assert len(end.controller.attempts) == 1


# The end of a forward


async def test_teardown_closes_the_listener_and_the_streams():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            await requires.teardown()
            await expect_closed(client)

        with pytest.raises(OSError):
            await connect_local(listen)
        assert requires.manager.state("bt") is None
        # The stream ended for the peer too.
        await provides.wait_state(READY)

        # A new setup of the forward opens the listener again.
        await requires.setup("controller", REQUIRES)
        await wait_for(lambda: requires.manager.state("bt") == (READY, ""))
        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"


async def test_lease_end_closes_the_listeners_and_the_streams_without_a_teardown():
    listen = free_address()
    async with running() as (tg, router), local_service() as (service, _):
        requires = End(tg, router, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, router, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            await provides.wait_state(CONNECTED)

            with fail_after(5):
                await requires.manager.aclose()
                await provides.manager.aclose()
            await expect_closed(client)

        with pytest.raises(OSError):
            await connect_local(listen)
        reports = len(requires.controller.attempts), len(provides.controller.attempts)

        # Later instructions do nothing.
        await requires.setup("controller", REQUIRES)
        await provides.setup("rootcanal", PROVIDES, forward="other")
        await anyio.sleep(0.05)
        with pytest.raises(OSError):
            await connect_local(listen)
        assert (len(requires.controller.attempts), len(provides.controller.attempts)) == reports


async def test_aclose_without_a_forward_starts_and_waits_for_nothing():
    async with running() as (tg, router):
        end = End(tg, router, [])
        with fail_after(1):
            await end.manager.aclose()
        assert end.controller.attempts == []


# Paths between two connects ports


@pytest.mark.parametrize(
    ("initiator_mode", "responder_mode"),
    [("listen", "listen"), ("listen", "connect"), ("connect", "listen"), ("connect", "connect")],
)
async def test_path_between_two_connects_ports(initiator_mode, responder_mode):
    async with running() as (tg, router), accepting_service() as (a_service, a_accepted), accepting_service() as (
        b_service,
        b_accepted,
    ):
        a_address = free_address() if initiator_mode == "listen" else a_service
        b_address = free_address() if responder_mode == "listen" else b_service
        initiator = End(tg, router, [Port(name="uart", direction="connects", **{initiator_mode: a_address})])
        responder = End(tg, router, [Port(name="uart", direction="connects", **{responder_mode: b_address})])
        initiator.controller.peer = responder.manager
        responder.controller.peer = initiator.manager

        await responder.setup("uart", CONNECTS, forward="uart", member="dut")
        await initiator.setup("uart", CONNECTS, forward="uart", member="companion", initiator=True)
        # An end that listens is READY when its listener is open.
        if initiator_mode == "listen":
            await initiator.wait_state(READY, "uart")
        if responder_mode == "listen":
            await wait_for(lambda: responder.manager.state("uart") == (READY, ""))

        async def local_side(mode, address, accepted):
            if mode == "listen":
                return await connect_local(address), None
            with fail_after(5):
                return await accepted.receive()

        a, a_done = await local_side(initiator_mode, a_address, a_accepted)
        b, b_done = await local_side(responder_mode, b_address, b_accepted)
        try:
            await a.send(b"from-a")
            assert await read_exactly(b, 6) == b"from-a"
            await b.send(b"from-b")
            assert await read_exactly(a, 6) == b"from-b"
            await initiator.wait_state(CONNECTED, "uart")
            await responder.wait_state(CONNECTED, "uart")
        finally:
            await a.aclose()
            await b.aclose()
            for done in (a_done, b_done):
                if done is not None:
                    done.set()

        # Only the initiator calls DialPeer.
        assert len(initiator.controller.dials) >= 1
        assert responder.controller.dials == []
        assert len(set(router.opened)) == len(initiator.controller.dials)


async def test_path_opens_again_after_a_cut():
    async with running() as (tg, router), accepting_service() as (a_service, a_accepted), accepting_service() as (
        b_service,
        b_accepted,
    ):
        initiator = End(tg, router, [Port(name="uart", direction="connects", connect=a_service)])
        responder = End(tg, router, [Port(name="uart", direction="connects", connect=b_service)])
        initiator.controller.peer = responder.manager

        # The initiator starts first: DialPeer fails until the other end is set up.
        await initiator.setup("uart", CONNECTS, forward="uart", initiator=True)
        with fail_after(5):
            a, a_done = await a_accepted.receive()
        await wait_for(lambda: len(initiator.controller.dials) >= 2)
        assert router.opened == []
        await responder.setup("uart", CONNECTS, forward="uart")

        with fail_after(5):
            b, b_done = await b_accepted.receive()
        await a.send(b"one")
        assert await read_exactly(b, 3) == b"one"
        await initiator.wait_state(CONNECTED, "uart")
        first_token = router.opened[0]

        # The stream is cut: the two ends close their local sides.
        await router.cut(first_token)
        await expect_closed(a)
        await expect_closed(b)
        a_done.set()
        b_done.set()

        # The initiator opens the path again, with a new stream.
        with fail_after(5):
            a, a_done = await a_accepted.receive()
            b, b_done = await b_accepted.receive()
        await b.send(b"two")
        assert await read_exactly(a, 3) == b"two"
        assert len(set(router.opened)) == 2
        assert responder.controller.dials == []
        a_done.set()
        b_done.set()


async def test_path_end_that_listens_waits_for_its_local_client_for_the_time_limit():
    listen = free_address()
    initiator_listen = free_address()
    async with running() as (tg, router):
        initiator = End(tg, router, [Port(name="uart", direction="connects", listen=initiator_listen)])
        responder = End(
            tg,
            router,
            [Port(name="uart", direction="connects", listen=listen)],
            local_client_timeout=0.2,
        )
        initiator.controller.peer = responder.manager
        await responder.setup("uart", CONNECTS, forward="uart")
        await initiator.setup("uart", CONNECTS, forward="uart", initiator=True)
        await initiator.wait_state(READY, "uart")
        await responder.wait_state(READY, "uart")

        # The responder has no local client: it closes the stream after the time limit.
        async with await connect_local(initiator_listen) as client:
            await expect_closed(client)
        assert CONNECTED not in responder.controller.states("uart")
        # It sent no join frame: the initiator did not report a connection.
        assert CONNECTED not in initiator.controller.states("uart")


async def test_path_to_a_dead_service_is_not_connected_and_backs_off():
    async with running() as (tg, router), accepting_service() as (a_service, a_accepted):
        initiator = End(tg, router, [Port(name="uart", direction="connects", connect=a_service)], path_backoff=0.02)
        # No service listens on the connect address of the other end.
        responder = End(tg, router, [Port(name="uart", direction="connects", connect=free_address())])
        initiator.controller.peer = responder.manager
        await responder.setup("uart", CONNECTS, forward="uart")
        await initiator.setup("uart", CONNECTS, forward="uart", initiator=True)

        async def drain():
            # The initiator opens its local service again for each try.
            async for _stream, done in a_accepted:
                done.set()

        tg.start_soon(drain)
        # Delays of 20, 40, 80, 160, 320 and 640 ms: 6 or 7 tries in one second.
        # Without the growing delay there are about 50.
        await anyio.sleep(1.0)
        assert 3 <= len(initiator.controller.dials) <= 8
        assert CONNECTED not in initiator.controller.states("uart")
        assert CONNECTED not in responder.controller.states("uart")


async def test_path_that_stayed_open_is_opened_again_without_a_longer_delay():
    async with running() as (tg, router), accepting_service() as (a_service, a_accepted), accepting_service() as (
        b_service,
        b_accepted,
    ):
        initiator = End(
            tg,
            router,
            [Port(name="uart", direction="connects", connect=a_service)],
            path_backoff=0.02,
            path_stable_after=0.1,
        )
        responder = End(tg, router, [Port(name="uart", direction="connects", connect=b_service)])
        initiator.controller.peer = responder.manager
        await responder.setup("uart", CONNECTS, forward="uart")
        await initiator.setup("uart", CONNECTS, forward="uart", initiator=True)

        for round_ in range(4):
            with fail_after(5):
                _a, a_done = await a_accepted.receive()
                _b, b_done = await b_accepted.receive()
            await initiator.wait_state(CONNECTED, "uart")
            # The path stays open for longer than path_stable_after.
            await anyio.sleep(0.15)
            started = anyio.current_time()
            await router.cut(router.opened[-1])
            a_done.set()
            b_done.set()
            await wait_for(lambda round_=round_: len(initiator.controller.dials) == round_ + 2)
            # Each time the delay is the first one, 20 ms. A delay that grows is 320 ms in the last round.
            assert anyio.current_time() - started < 0.25


# The frames of a real RouterService stream


class GrpcRouter(router_pb2_grpc.RouterServiceServicer):
    """RouterService as the controller serves it: it connects the two streams of one token."""

    def __init__(self):
        self.pending: dict[str, tuple] = {}
        self.streams = 0

    async def Stream(self, request_iterator, context):
        token = dict(context.invocation_metadata())["authorization"]
        self.streams += 1
        if token not in self.pending:
            done = Event()
            self.pending[token] = (context, done)
            await done.wait()
            return
        first, done = self.pending.pop(token)

        async def pipe(src, dst):
            while (frame := await src.read()) != grpc.aio.EOF:
                await dst.write(router_pb2.StreamResponse(payload=frame.payload, frame_type=frame.frame_type))

        try:
            async with create_task_group() as tg:
                tg.start_soon(pipe, context, first)
                tg.start_soon(pipe, first, context)
        finally:
            done.set()
        # An async generator servicer: this function yields nothing.
        return
        yield


@asynccontextmanager
async def grpc_router():
    """A RouterService on a port of its own, and what an exporter connects to it with."""
    server = grpc.aio.server()
    router = GrpcRouter()
    router_pb2_grpc.add_RouterServiceServicer_to_server(router, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    @asynccontextmanager
    async def router_connect(endpoint: str, token: str):
        async with grpc.aio.insecure_channel(endpoint) as channel:
            call = router_pb2_grpc.RouterServiceStub(channel).Stream(metadata=(("authorization", token),))
            async with RouterStream(context=call) as stream:
                yield stream

    class Connect:
        connect = staticmethod(router_connect)

    try:
        yield router, Connect(), port
    finally:
        await server.stop(grace=0)


async def test_peer_that_does_not_join_a_router_stream_keeps_the_listener(caplog):
    # grpc.aio answers the read that the time limit cancels with a cancellation of its own. It must
    # not end the listener: a peer whose service is not up yet is tried again, and again.
    listen = free_address()
    async with grpc_router() as (router, connect, port), running() as (tg, _):
        requires = End(
            tg, connect, [Port(name="controller", direction="requires", listen=listen)], peer_join_timeout=0.2
        )
        requires.controller.endpoint = f"127.0.0.1:{port}"
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        with caplog.at_level(logging.WARNING, logger="jumpstarter.exporter.forwards"):
            for _ in range(2):
                async with await connect_local(listen) as client:
                    await expect_closed(client)
        assert [record.getMessage() for record in caplog.records] == [
            "Forward bt: no frame from the peer in 0s, closing the connection"
        ] * 2
        assert router.streams == 2
        assert CONNECTED not in requires.controller.states()


async def test_forward_through_router_stream_frames():
    listen = free_address()
    async with (
        grpc_router() as (router, connect, port),
        running() as (tg, _),
        local_service() as (service, _connections),
    ):
        requires = End(tg, connect, [Port(name="controller", direction="requires", listen=listen)])
        provides = End(tg, connect, [Port(name="rootcanal", direction="provides", connect=service)])
        requires.controller.peer = provides.manager
        requires.controller.endpoint = provides.controller.endpoint = f"127.0.0.1:{port}"
        await provides.setup("rootcanal", PROVIDES)
        await requires.setup("controller", REQUIRES)
        await requires.wait_state(READY)

        async with await connect_local(listen) as client:
            assert await read_exactly(client, 4) == b"hci:"
            payload = bytes(range(256)) * 64
            await client.send(payload)
            assert await read_exactly(client, len(payload)) == payload.upper()
            await requires.wait_state(CONNECTED)
            await provides.wait_state(CONNECTED)
        # The local client closed: the stream and the connection to the service close.
        await requires.wait_state(READY)
        await provides.wait_state(READY)
        assert router.streams == 2
