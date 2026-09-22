"""Protocol-level (integration) tests for the SNMP power driver.

These tests run a real in-process pysnmp SNMPv3 agent on a loopback UDP port
so the driver performs actual SNMPv3 SET traffic on the wire (USM
authentication, privacy, error-status propagation, and timeouts), instead of
mocking the pysnmp internals as in driver_test.py.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from pysnmp.carrier.asyncio.dgram import udp
from pysnmp.entity import config, engine
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.proto.api import v2c

from jumpstarter_driver_snmp.driver import AuthProtocol, PrivProtocol, SNMPError, SNMPServer

from jumpstarter.common.utils import serve


@pytest.fixture(autouse=True)
def _reset_event_loop():
    """The driver creates, sets and closes its own event loop per operation,
    leaving a closed loop registered on the main thread. Replace it with a
    fresh open loop after each test so tests running later (e.g. the mock
    based driver_test.py) do not hit a closed loop via asyncio.get_event_loop()."""
    yield
    asyncio.set_event_loop(asyncio.new_event_loop())


class _RecordingSetResponder(cmdrsp.SetCommandResponder):
    """SET responder that records received varbinds and answers with a
    configurable SNMP error status."""

    def __init__(self, snmpEngine, snmpContext, agent: SnmpTestAgent):
        super().__init__(snmpEngine, snmpContext)
        self._agent = agent

    def handle_management_operation(self, snmpEngine, stateReference, contextName, PDU):
        varBinds = v2c.apiPDU.get_varbinds(PDU)
        self._agent.record([(tuple(int(component) for component in oid), int(value)) for oid, value in varBinds])
        self.send_varbinds(
            snmpEngine,
            stateReference,
            self._agent.error_status,
            self._agent.error_index,
            varBinds,
        )
        self.release_state_information(stateReference)


class SnmpTestAgent:
    """In-process SNMPv3 agent serving SET requests on a loopback UDP port.

    Runs a dedicated asyncio event loop in a background thread so it can
    answer the driver while the driver runs its own loop.
    """

    def __init__(
        self,
        user: str = "agent-user",
        auth_protocol: Any = config.USM_AUTH_NONE,
        auth_key: str | None = None,
        priv_protocol: Any = config.USM_PRIV_NONE,
        priv_key: str | None = None,
        error_status: int = 0,
        error_index: int = 0,
    ):
        self.user = user
        self.auth_protocol = auth_protocol
        self.auth_key = auth_key
        self.priv_protocol = priv_protocol
        self.priv_key = priv_key
        self.error_status = error_status
        self.error_index = error_index
        self.port: int = 0
        self.requests: list[list[tuple[tuple[int, ...], int]]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._engine: Any = None
        self._responder: _RecordingSetResponder | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> SnmpTestAgent:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, args=(sock,), daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("SNMP test agent did not start in time")
        if self._error is not None:
            raise self._error
        return self

    def record(self, varbinds: list[tuple[tuple[int, ...], int]]):
        self.requests.append(varbinds)

    def _run(self, sock: socket.socket):
        asyncio.set_event_loop(self._loop)
        try:
            try:
                self._loop.run_until_complete(self._setup(sock))
            except BaseException as e:
                self._error = e
                self._ready.set()
                return
            self._ready.set()
            self._loop.run_forever()
        finally:
            self._loop.close()

    async def _setup(self, sock: socket.socket):
        self._engine = engine.SnmpEngine()
        config.add_v3_user(
            self._engine,
            self.user,
            self.auth_protocol,
            self.auth_key,
            self.priv_protocol,
            self.priv_key,
        )
        snmp_context = context.SnmpContext(self._engine)
        self._responder = _RecordingSetResponder(self._engine, snmp_context, self)
        # On first contact the client puts its own engine id on the wire as
        # contextEngineId. Register the responder against the empty
        # contextEngineId too, so the dispatcher's wildcard fallback matches
        # whatever engine id the client sends.
        self._engine.message_dispatcher.register_context_engine_id(
            b"", self._responder.SUPPORTED_PDU_TYPES, self._responder.process_pdu
        )
        transport = udp.UdpAsyncioTransport()
        transport.open_server_mode(sock=sock)
        config.add_transport(self._engine, udp.DOMAIN_NAME, transport)
        self._engine.open_dispatcher()

    def _shutdown(self):
        try:
            if self._responder is not None:
                self._engine.message_dispatcher.unregister_context_engine_id(b"", self._responder.SUPPORTED_PDU_TYPES)
                self._responder.close(self._engine)
            if self._engine is not None:
                self._engine.close_dispatcher()
        finally:
            self._loop.stop()

    def stop(self):
        if self._thread is None:
            return
        if self._error is None:
            self._loop.call_soon_threadsafe(self._shutdown)
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError("SNMP test agent did not stop in time")

    def __enter__(self) -> SnmpTestAgent:
        return self.start()

    def __exit__(self, *exc_info):
        self.stop()


@contextmanager
def _blackhole_port() -> Generator[int, None, None]:
    """Yield a UDP port that is bound but never answers."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


async def test_on_sends_set_varbind_with_one():
    with SnmpTestAgent() as agent:
        server = SNMPServer(host="127.0.0.1", port=agent.port, user="agent-user", plug=1)
        result = await server.on()

    assert "Power ON" in result
    assert agent.requests == [[((1, 3, 6, 1, 4, 1, 13742, 6, 4, 1, 2, 1, 2, 1, 1), 1)]]


async def test_off_sends_set_varbind_with_zero():
    with SnmpTestAgent() as agent:
        server = SNMPServer(host="127.0.0.1", port=agent.port, user="agent-user", plug=1)
        result = await server.off()

    assert "Power OFF" in result
    assert agent.requests == [[((1, 3, 6, 1, 4, 1, 13742, 6, 4, 1, 2, 1, 2, 1, 1), 0)]]


async def test_custom_oid_and_plug_sent_on_wire():
    with SnmpTestAgent() as agent:
        server = SNMPServer(
            host="127.0.0.1",
            port=agent.port,
            user="agent-user",
            plug=7,
            oid="1.3.6.1.2.1.95",
        )
        await server.off()

    assert agent.requests == [[((1, 3, 6, 1, 2, 1, 95, 7), 0)]]


@pytest.mark.parametrize(
    ("driver_auth", "usm_auth"),
    [
        (AuthProtocol.MD5, config.USM_AUTH_HMAC96_MD5),
        (AuthProtocol.SHA, config.USM_AUTH_HMAC96_SHA),
    ],
)
async def test_auth_no_priv(driver_auth, usm_auth):
    with SnmpTestAgent(auth_protocol=usm_auth, auth_key="correct-key") as agent:
        server = SNMPServer(
            host="127.0.0.1",
            port=agent.port,
            user="agent-user",
            plug=1,
            auth_protocol=driver_auth,
            auth_key="correct-key",
        )
        await server.on()

    assert len(agent.requests) == 1


async def test_wrong_auth_key_rejected():
    with SnmpTestAgent(auth_protocol=config.USM_AUTH_HMAC96_MD5, auth_key="correct-key") as agent:
        server = SNMPServer(
            host="127.0.0.1",
            port=agent.port,
            user="agent-user",
            plug=1,
            auth_protocol=AuthProtocol.MD5,
            auth_key="wrong-key",
            timeout=1.0,
        )
        with pytest.raises(SNMPError):
            await server.on()

    assert agent.requests == []


@pytest.mark.parametrize(
    ("driver_priv", "usm_priv"),
    [
        (PrivProtocol.DES, config.USM_PRIV_CBC56_DES),
        (PrivProtocol.AES, config.USM_PRIV_CFB128_AES),
    ],
)
async def test_auth_priv(driver_priv, usm_priv):
    with SnmpTestAgent(
        auth_protocol=config.USM_AUTH_HMAC96_SHA,
        auth_key="auth-key-123",
        priv_protocol=usm_priv,
        priv_key="priv-key-123",
    ) as agent:
        server = SNMPServer(
            host="127.0.0.1",
            port=agent.port,
            user="agent-user",
            plug=1,
            auth_protocol=AuthProtocol.SHA,
            auth_key="auth-key-123",
            priv_protocol=driver_priv,
            priv_key="priv-key-123",
        )
        await server.on()

    assert len(agent.requests) == 1


async def test_agent_error_status_propagates():
    # noSuchName (2) on the first varbind
    with SnmpTestAgent(error_status=2, error_index=1) as agent:
        server = SNMPServer(host="127.0.0.1", port=agent.port, user="agent-user", plug=1)
        with pytest.raises(SNMPError, match="SNMP error: noSuchName at 1.3.6.1.4.1.13742"):
            await server.on()

    assert len(agent.requests) == 1


async def test_no_response_times_out():
    with _blackhole_port() as port:
        server = SNMPServer(host="127.0.0.1", port=port, user="agent-user", plug=1, timeout=1.0)
        start = time.monotonic()
        with pytest.raises(SNMPError, match="timed out"):
            await server.on()
        elapsed = time.monotonic() - start

    assert elapsed >= 0.8


def test_unresolvable_host_raises():
    with pytest.raises(SNMPError, match="Failed to resolve hostname"):
        SNMPServer(host="jumpstarter.invalid", user="agent-user", plug=1)


def test_client_on_off_via_serve():
    with SnmpTestAgent() as agent:
        server = SNMPServer(host="127.0.0.1", port=agent.port, user="agent-user", plug=3)
        with serve(server) as client:
            client.on()
            client.off()

    values = [value for request in agent.requests for _oid, value in request]
    assert values == [1, 0]
