"""Forward ends of an exporter (JEP-0016).

A forward of a lease connects a port of this exporter to a port of a different
exporter of the same lease. The controller sends the instructions on the
``Listen`` stream of the lease. All the data goes through the router, with one
router stream for each connection.

- ``requires`` port: the exporter opens the ``listen`` address. For each local
  connection it calls ``DialPeer`` and connects the socket to the router stream.
- ``provides`` port: on each connect instruction, the exporter opens the router
  stream and connects it to the local service of the port.
- ``connects`` port: the initiator does the steps of the ``requires`` side and
  the other end those of the ``provides`` side. Each end uses its local mode,
  ``listen`` or ``connect``. A path has one connection at a time.

The exporter reports each change of the state of an end with ``ReportForward``.
"""

from __future__ import annotations

import inspect
import logging
import os
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

import grpc
from anyio import (
    TASK_STATUS_IGNORED,
    CancelScope,
    EndOfStream,
    Event,
    Lock,
    create_memory_object_stream,
    create_task_group,
    fail_after,
    sleep,
    sleep_forever,
)
from anyio.abc import TaskGroup, TaskStatus
from jumpstarter_protocol import jumpstarter_pb2

from jumpstarter.common import TemporaryTcpListener
from jumpstarter.common.ports import Port, PortDirection
from jumpstarter.streams.common import forward_stream

if TYPE_CHECKING:
    from jumpstarter.driver import Driver

logger = logging.getLogger(__name__)

FORWARD_PEER_JOIN_TIMEOUT = 30.0
"""Seconds that one end waits for its peer to join the router stream of a connection."""

_RPC_TIMEOUT = 30
_REPORT_BACKOFF_BASE = 1.0
_REPORT_BACKOFF_CAP = 30.0
_PATH_BACKOFF_BASE = 0.5
_PATH_BACKOFF_CAP = 30.0

_FINAL_REPORT_CODES = frozenset({
    grpc.StatusCode.UNIMPLEMENTED,
    grpc.StatusCode.PERMISSION_DENIED,
    grpc.StatusCode.INVALID_ARGUMENT,
})

_READY = jumpstarter_pb2.FORWARD_END_STATE_READY
_CONNECTED = jumpstarter_pb2.FORWARD_END_STATE_CONNECTED
_FAILED = jumpstarter_pb2.FORWARD_END_STATE_FAILED

ControllerFactory = Callable[[], AbstractAsyncContextManager[Any]]
"""Opens a ``ControllerServiceStub`` for one call."""

RouterConnect = Callable[[str, str], AbstractAsyncContextManager[Any]]
"""Opens the router stream of an endpoint and a token, and yields the byte stream."""


class ForwardSequence:
    """Sequence numbers of the reports of one exporter process.

    The first number is the start time of the process in milliseconds. Thus the
    numbers of a process that started later are larger, and the controller can
    ignore a report that is older than the one it accepted.
    """

    def __init__(self, start: int | None = None):
        self._next = int(time.time() * 1000) if start is None else start

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


PROCESS_SEQUENCE = ForwardSequence()
"""One sequence for the process: a new lease session does not start it again."""


@dataclass(eq=False, kw_only=True)
class ForwardEnd:
    """The end of one forward on this exporter."""

    lease_uid: str
    name: str
    member: str
    direction: PortDirection | None
    initiator: bool
    port_name: str
    port: Port | None = None
    driver: Driver | None = None
    state: int = jumpstarter_pb2.FORWARD_END_STATE_UNSPECIFIED
    message: str = ""
    connections: int = 0
    tg: TaskGroup | None = None
    stopped: Event = field(default_factory=Event)
    # A path has one connection at a time.
    path_lock: Lock = field(default_factory=Lock)
    # Local clients of a path end that listens and does not open the path.
    local_tx: Any = None
    local_rx: Any = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.lease_uid, self.name)


def _os_error_cause(error: OSError) -> str:
    # str(error) can contain the local address, which must stay in the exporter.
    return os.strerror(error.errno) if error.errno else type(error).__name__


@asynccontextmanager
async def _opened(stream) -> AsyncGenerator[Any, None]:
    yield stream


@dataclass(eq=False, kw_only=True)
class ForwardManager:
    """The forward ends of one lease on this exporter."""

    ports: Mapping[str, tuple[Driver, Port]]
    """The ports of the lease session, by name."""
    controller: ControllerFactory
    router_connect: RouterConnect
    tg: TaskGroup
    """Runs the listeners and the connections. aclose() stops and waits for them."""
    lease_name: str = ""
    peer_join_timeout: float = FORWARD_PEER_JOIN_TIMEOUT
    report_backoff: float = _REPORT_BACKOFF_BASE
    path_backoff: float = _PATH_BACKOFF_BASE
    sequence: ForwardSequence = field(default_factory=lambda: PROCESS_SEQUENCE)

    _ends: dict[tuple[str, str], ForwardEnd] = field(init=False, default_factory=dict)
    _pending: dict[tuple[str, str], jumpstarter_pb2.ReportForwardRequest] = field(init=False, default_factory=dict)
    _wake: Event = field(init=False, default_factory=Event)
    _closed: bool = field(init=False, default=False)
    _reporter_scope: CancelScope | None = field(init=False, default=None)
    _reporter_stopped: Event = field(init=False, default_factory=Event)

    # Instructions from the Listen stream

    async def handle(self, response: jumpstarter_pb2.ListenResponse) -> bool:
        """Apply the forward instruction of a Listen response.

        Returns False when the response contains no forward instruction. An
        instruction does not wait for a connection: this returns when the end
        is recorded and its tasks started.
        """
        match response.WhichOneof("forward"):
            case "forward_setup":
                await self.setup(response.forward_setup)
            case "forward_connect":
                self.connect(response.forward_connect)
            case "forward_teardown":
                await self.teardown(response.forward_teardown)
            case _:
                return False
        return True

    async def setup(self, setup: jumpstarter_pb2.ForwardSetup) -> None:
        if self._closed:
            return
        key = (setup.lease_uid, setup.forward_name)
        current = self._ends.get(key)
        if current is not None and current.state != _FAILED:
            # The setup was applied before. Only report the current state again.
            logger.debug("Forward %s: repeated setup, reporting the current state", setup.forward_name)
            self._queue_report(current)
            return
        if current is not None:
            # The setup failed before, for example on an address in use. Try it again.
            await self._stop_end(current)

        end = self._new_end(setup)
        self._ends[key] = end
        if end.state == _FAILED:
            logger.warning("Forward %s: setup rejected: %s", end.name, end.message)
            end.stopped.set()
            self._queue_report(end)
            return
        logger.info(
            "Forward %s: setting up the %s end on port %s (member %s)",
            end.name,
            end.direction,
            end.port_name,
            end.member,
        )
        await self.tg.start(self._run_end, end)

    def connect(self, connect: jumpstarter_pb2.ForwardConnect) -> None:
        """Open one connection of a ``provides`` end, or of the second end of a path."""
        end = self._ends.get((connect.lease_uid, connect.forward_name))
        if self._closed or end is None or end.tg is None or end.state == _FAILED:
            logger.warning(
                "Forward %s: connect instruction %s ignored: this exporter has no ready end of that forward",
                connect.forward_name,
                connect.connection_id,
            )
            return
        if end.direction == PortDirection.REQUIRES or end.initiator:
            logger.warning(
                "Forward %s: connect instruction %s ignored: this end opens the connections itself",
                connect.forward_name,
                connect.connection_id,
            )
            return
        logger.info(
            "Forward %s: connection %s from member %s",
            end.name,
            connect.connection_id,
            connect.peer_member_name,
        )
        end.tg.start_soon(self._accept_connection, end, connect.router_endpoint, connect.router_token)

    async def teardown(self, teardown: jumpstarter_pb2.ForwardTeardown) -> None:
        end = self._ends.pop((teardown.lease_uid, teardown.forward_name), None)
        if end is None:
            return
        logger.info("Forward %s: teardown", end.name)
        self._pending.pop(end.key, None)
        await self._stop_end(end)

    async def aclose(self) -> None:
        """Close all the listeners and streams. Later instructions do nothing."""
        self._closed = True
        ends = list(self._ends.values())
        self._ends.clear()
        self._pending.clear()
        for end in ends:
            if end.tg is not None:
                end.tg.cancel_scope.cancel()
        if self._reporter_scope is not None:
            self._reporter_scope.cancel()
            await self._reporter_stopped.wait()
        for end in ends:
            await end.stopped.wait()

    def state(self, forward_name: str) -> tuple[int, str] | None:
        """The state and the message of the end of a forward, for diagnostics and tests."""
        for end in self._ends.values():
            if end.name == forward_name:
                return end.state, end.message
        return None

    # Setup

    def _new_end(self, setup: jumpstarter_pb2.ForwardSetup) -> ForwardEnd:
        try:
            direction = PortDirection.from_proto(setup.direction)
        except ValueError:
            direction = None
        end = ForwardEnd(
            lease_uid=setup.lease_uid,
            name=setup.forward_name,
            member=setup.member_name,
            direction=direction,
            initiator=setup.initiator,
            port_name=setup.local_port_name,
        )
        if (failure := self._bind_port(end)) is not None:
            end.state, end.message = _FAILED, failure
        return end

    def _bind_port(self, end: ForwardEnd) -> str | None:
        """Find the local port of an end. Returns the cause when the end cannot operate."""
        found = self.ports.get(end.port_name)
        if found is None:
            return f"port '{end.port_name}' is not declared on this exporter"
        end.driver, end.port = found
        port = end.port
        if end.direction is None:
            return f"the setup of port '{port.name}' has no direction"
        if port.direction != end.direction:
            return f"port '{port.name}' is a {port.direction} port, not a {end.direction} port"
        if not port.forwardable:
            return f"port '{port.name}' is not a socket port with the stream transport"
        if port.direction == PortDirection.REQUIRES and port.listen is None:
            return f"port '{port.name}' has no listen address"
        return None

    async def _stop_end(self, end: ForwardEnd) -> None:
        if end.tg is not None:
            end.tg.cancel_scope.cancel()
        await end.stopped.wait()

    async def _run_end(self, end: ForwardEnd, *, task_status: TaskStatus[None] = TASK_STATUS_IGNORED) -> None:
        """Own the tasks of one end until its teardown or the end of the lease."""
        assert end.port is not None
        try:
            async with create_task_group() as tg:
                end.tg = tg
                task_status.started()
                listens = end.port.listen is not None
                match end.direction:
                    case PortDirection.REQUIRES:
                        tg.start_soon(self._serve_listener, end, self._dial_and_bridge)
                    case PortDirection.PROVIDES:
                        self._set_state(end, _READY)
                    case PortDirection.CONNECTS if end.initiator and listens:
                        tg.start_soon(self._serve_listener, end, self._dial_and_bridge_path)
                    case PortDirection.CONNECTS if end.initiator:
                        self._set_state(end, _READY)
                        tg.start_soon(self._initiate_path, end)
                    case PortDirection.CONNECTS if listens:
                        end.local_tx, end.local_rx = create_memory_object_stream[Any](0)
                        tg.start_soon(self._serve_listener, end, self._offer_local)
                    case PortDirection.CONNECTS:
                        self._set_state(end, _READY)
                await sleep_forever()
        except Exception:
            logger.exception("Forward %s: the end stopped on an error", end.name)
            if self._ends.get(end.key) is end:
                self._set_state(end, _FAILED, "internal error in the exporter")
        finally:
            end.tg = None
            end.stopped.set()

    async def _serve_listener(self, end: ForwardEnd, handler) -> None:
        assert end.port is not None
        address = end.port.listen_address
        assert address is not None
        host, port = address
        try:
            # Without SO_REUSEPORT: an address that is in use is a failure.
            async with TemporaryTcpListener(
                partial(self._guard_local, end, handler),
                local_host=host,
                local_port=port,
                reuse_port=False,
            ):
                logger.info("Forward %s: listening on %s for port %s", end.name, end.port.listen, end.port.name)
                self._set_state(end, _READY)
                await sleep_forever()
        except OSError as error:
            logger.error("Forward %s: cannot listen on %s: %s", end.name, end.port.listen, error)
            self._set_state(
                end, _FAILED, f"port '{end.port.name}': cannot open the listener: {_os_error_cause(error)}"
            )

    async def _guard_local(self, end: ForwardEnd, handler, local) -> None:
        """Run the handler of one local connection. Its failure does not stop the listener."""
        try:
            async with local:
                await handler(end, local)
        except Exception as error:  # noqa: BLE001
            logger.warning("Forward %s: connection failed: %s", end.name, error)

    # Connections

    async def _dial_peer(self, end: ForwardEnd) -> jumpstarter_pb2.DialPeerResponse:
        async with self.controller() as controller:
            return await controller.DialPeer(
                jumpstarter_pb2.DialPeerRequest(lease_uid=end.lease_uid, forward_name=end.name),
                timeout=_RPC_TIMEOUT,
            )

    async def _dial_and_bridge(self, end: ForwardEnd, local) -> None:
        """One local connection of a ``requires`` end: its own router stream from DialPeer."""
        try:
            response = await self._dial_peer(end)
        except grpc.aio.AioRpcError as error:
            # For example UNAVAILABLE: the peer exporter has no Listen stream.
            logger.warning(
                "Forward %s: DialPeer failed (%s: %s), closing the local connection",
                end.name,
                error.code(),
                error.details(),
            )
            return
        await self._bridge(end, response.router_endpoint, response.router_token, _opened(local))

    async def _dial_and_bridge_path(self, end: ForwardEnd, local) -> None:
        async with end.path_lock:
            await self._dial_and_bridge(end, local)

    async def _offer_local(self, end: ForwardEnd, local) -> None:
        """A local client of a path end that waits for the initiator."""
        done = Event()
        await end.local_tx.send((local, done))
        await done.wait()

    @asynccontextmanager
    async def _take_local(self, end: ForwardEnd) -> AsyncGenerator[Any, None]:
        with fail_after(self.peer_join_timeout):
            local, done = await end.local_rx.receive()
        try:
            yield local
        finally:
            done.set()

    async def _accept_connection(self, end: ForwardEnd, endpoint: str, token: str) -> None:
        """One connection that the peer opened: join its router stream and open the local side."""
        assert end.port is not None and end.driver is not None
        if end.local_rx is not None:
            open_local = self._take_local(end)
        else:
            open_local = end.driver.connect_port(end.port)
        try:
            if end.direction == PortDirection.CONNECTS:
                async with end.path_lock:
                    await self._bridge(end, endpoint, token, open_local)
            else:
                await self._bridge(end, endpoint, token, open_local)
        except Exception as error:  # noqa: BLE001
            logger.warning("Forward %s: connection failed: %s", end.name, error)

    async def _initiate_path(self, end: ForwardEnd) -> None:
        """The initiator of a path with a local service: open the path, and open it again after a cut."""
        assert end.port is not None and end.driver is not None
        delay = self.path_backoff
        while True:
            opened = False
            try:
                async with end.driver.connect_port(end.port) as local:
                    # Keep the one local connection while the peer is not ready.
                    # A connect and close for each try can disturb the service.
                    while True:
                        try:
                            response = await self._dial_peer(end)
                            break
                        except grpc.aio.AioRpcError as error:
                            logger.debug("Forward %s: DialPeer failed (%s), trying again", end.name, error.code())
                            await sleep(delay)
                            delay = min(delay * 2, _PATH_BACKOFF_CAP)
                    opened = await self._bridge(
                        end, response.router_endpoint, response.router_token, _opened(local)
                    )
            except Exception as error:  # noqa: BLE001
                logger.debug("Forward %s: path not open: %s", end.name, error)
            if opened:
                delay = self.path_backoff
            await sleep(delay)
            delay = min(delay * 2, _PATH_BACKOFF_CAP)

    async def _bridge(self, end: ForwardEnd, endpoint: str, token: str, open_local) -> bool:
        """Connect one local connection to one router stream until one of them closes.

        Returns True if the peer joined and the data path was open.
        """
        assert end.port is not None and end.driver is not None
        opened = False
        async with self.router_connect(endpoint, token) as remote:
            # The router gives no signal when the peer joins. Thus each end sends
            # one empty frame when it opens the stream, and the first frame from
            # the peer shows that it joined.
            await remote.send(b"")
            try:
                with fail_after(self.peer_join_timeout):
                    first = await remote.receive()
            except TimeoutError:
                logger.warning(
                    "Forward %s: the peer did not join the stream in %.0fs, closing the connection",
                    end.name,
                    self.peer_join_timeout,
                )
                return False
            except EndOfStream:
                return False
            async with open_local as local:
                if first:
                    await local.send(first)
                opened = True
                self._connection_opened(end)
                try:
                    async with forward_stream(remote, local, metrics_driver_type=end.driver.driver_type):
                        pass
                except Exception as error:  # noqa: BLE001
                    logger.debug("Forward %s: stream ended on an error: %s", end.name, error)
                finally:
                    self._connection_closed(end)
        await self._notify_driver(end)
        return opened

    def _connection_opened(self, end: ForwardEnd) -> None:
        end.connections += 1
        if end.connections == 1:
            self._set_state(end, _CONNECTED)

    def _connection_closed(self, end: ForwardEnd) -> None:
        end.connections -= 1
        logger.info("Forward %s: a stream closed, %d connection(s) open", end.name, end.connections)
        if end.connections == 0 and end.state == _CONNECTED:
            self._set_state(end, _READY)

    async def _notify_driver(self, end: ForwardEnd) -> None:
        assert end.port is not None and end.driver is not None
        try:
            result = end.driver.forward_stream_closed(end.port)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Forward %s: forward_stream_closed() of port %s failed", end.name, end.port.name)

    # Reports

    def _set_state(self, end: ForwardEnd, state: int, message: str = "") -> None:
        if self._closed or self._ends.get(end.key) is not end:
            return
        end.state, end.message = state, message
        self._queue_report(end)

    def _queue_report(self, end: ForwardEnd) -> None:
        """Keep only the latest state of an end: it replaces a report that was not accepted."""
        if self._closed or end.state == jumpstarter_pb2.FORWARD_END_STATE_UNSPECIFIED:
            return
        self._pending[end.key] = jumpstarter_pb2.ReportForwardRequest(
            lease_uid=end.lease_uid,
            forward_name=end.name,
            state=end.state,  # ty: ignore[invalid-argument-type]
            message=end.message or None,
            sequence=self.sequence.next(),
        )
        if self._reporter_scope is None:
            self._reporter_scope = CancelScope()
            self.tg.start_soon(self._report_loop, self._reporter_scope)
        self._wake.set()

    async def _send_report(self, request: jumpstarter_pb2.ReportForwardRequest) -> None:
        async with self.controller() as controller:
            await controller.ReportForward(request, timeout=_RPC_TIMEOUT)

    async def _report_loop(self, scope: CancelScope) -> None:
        """Send each pending report again, with a delay, until the controller accepts it."""
        try:
            with scope:
                delay = self.report_backoff
                while True:
                    await self._wake.wait()
                    self._wake = Event()
                    while self._pending:
                        if await self._send_pending():
                            delay = self.report_backoff
                        else:
                            await sleep(delay)
                            delay = min(delay * 2, _REPORT_BACKOFF_CAP)
        finally:
            self._reporter_stopped.set()

    async def _send_pending(self) -> bool:
        """Send the pending reports one time. Returns False if the controller did not accept one."""
        accepted = True
        for key in list(self._pending):
            request = self._pending.get(key)
            if request is None:
                continue
            try:
                await self._send_report(request)
            except Exception as error:  # noqa: BLE001
                if isinstance(error, grpc.aio.AioRpcError) and error.code() in _FINAL_REPORT_CODES:
                    # The controller can never accept this report: it has no such RPC, the
                    # lease ended, or this exporter is not a bound end of the forward.
                    logger.warning(
                        "Forward %s: report rejected (%s: %s), not sent again", key[1], error.code(), error.details()
                    )
                    if self._pending.get(key) is request:
                        del self._pending[key]
                    continue
                logger.warning("Forward %s: report not accepted (%s), trying again", key[1], error)
                accepted = False
                continue
            logger.info(
                "Forward %s: reported %s (sequence %d)",
                request.forward_name,
                jumpstarter_pb2.ForwardEndState.Name(request.state),
                request.sequence,
            )
            # A newer state can replace the report during the call. Keep that one.
            if self._pending.get(key) is request:
                del self._pending[key]
        return accepted


__all__ = [
    "FORWARD_PEER_JOIN_TIMEOUT",
    "PROCESS_SEQUENCE",
    "ForwardEnd",
    "ForwardManager",
    "ForwardSequence",
]
