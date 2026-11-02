import base64
import json
import ssl
import struct
from contextlib import asynccontextmanager
from unittest.mock import patch

import anyio
import pytest
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSStream

from . import devicehub
from .devicehub import (
    CDTUNNEL_MAGIC,
    CORE_DEVICE_PROXY,
    DeviceHubBridge,
    _cdtunnel,
    _read_cdtunnel,
    serve_connection,
    served,
)
from .remotepairing import ControlChannel, DeviceIdentity, RemotePairingError
from .remotepairing_test import Host, logged
from .usbmux import _memory_pair

UDID = "00008110-001234567890001E"
GENERAL = {
    "DeviceName": "Test iPhone",
    "ProductType": "iPhone17,1",
    "ProductVersion": "27.0.1",
    "UniqueChipID": 1234567,
    "SerialNumber": "not shown",
}
TUNNEL_RESPONSE = {
    "type": "serverHandshakeResponse",
    "clientParameters": {"address": "fd00::2"},
    "serverRSDPort": 58783,
}
# The cipher suites a Mac offers for its tunnel, all TLS 1.2 PSK.
MAC_TUNNEL_CIPHERS = "PSK-AES128-GCM-SHA256:PSK-AES256-GCM-SHA384:PSK-AES256-CBC-SHA384:PSK-AES128-CBC-SHA256"
STDLIB_CONTEXT = ssl.SSLContext
# The ssl module's own setters look SSLContext up by name, so a replacement must bypass them.
STDLIB_SETTERS = super(STDLIB_CONTEXT, STDLIB_CONTEXT)


class TruststoreContext(STDLIB_CONTEXT):
    """Like truststore 0.10.5's SSLContext, which jmp puts in place of ssl.SSLContext."""

    def __init__(self, protocol):
        self._ctx = STDLIB_CONTEXT(protocol)

    def set_ciphers(self, ciphers):
        self._ctx.set_ciphers(ciphers)

    def wrap_bio(self, *args, **kwargs):
        return self._ctx.wrap_bio(*args, **kwargs)

    @property
    def minimum_version(self):
        return self._ctx.minimum_version

    @minimum_version.setter
    def minimum_version(self, value):
        STDLIB_SETTERS.minimum_version.__set__(self._ctx, value)  # ty: ignore[unresolved-attribute]

    @property
    def maximum_version(self):
        return self._ctx.maximum_version

    @maximum_version.setter
    def maximum_version(self, value):
        STDLIB_SETTERS.maximum_version.__set__(self._ctx, value)  # ty: ignore[unresolved-attribute]


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeBroker:
    """Lockdown values and a CoreDevice tunnel that echoes once its handshake is done."""

    timeout = 5

    def __init__(self, general=GENERAL):
        self.general = general
        self.requests = []
        self.services = []

    async def lockdown_values(self, domains):
        assert domains == [None, "com.apple.security.mac.amfi"]
        return {"NULL": dict(self.general), "com.apple.security.mac.amfi": {"DeveloperModeStatus": True}}

    @asynccontextmanager
    async def open_service(self, service):
        self.services.append(service)
        exporter_end, device_end = _memory_pair()

        async def device():
            reader = BufferedByteReceiveStream(device_end)
            self.requests.append(await _read_cdtunnel(reader))
            await device_end.send(_cdtunnel(TUNNEL_RESPONSE))
            while True:
                await device_end.send(await reader.receive())

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(device)
            yield exporter_end
            tasks.cancel_scope.cancel()


async def _prepared(tmp_path=None, **options) -> DeviceHubBridge:
    bridge = DeviceHubBridge(FakeBroker(), udid=UDID, state=tmp_path / "state.json" if tmp_path else None, **options)
    await bridge.prepare(49152)
    return bridge


@pytest.mark.anyio
async def test_prepare_creates_and_keeps_a_stand_in_identity(tmp_path):
    state = tmp_path / "devicehub" / "state.json"
    bridge = DeviceHubBridge(FakeBroker(), udid=UDID, state=state)
    info = await bridge.prepare(49152)
    assert info["name"] == "Test iPhone" and info["model"] == "iPhone17,1"
    assert info["paired_hosts"] == 0
    assert state.stat().st_mode & 0o777 == 0o600
    identity = bridge.identity
    assert identity is not None and info["identifier"] == identity.identifier
    assert identity.ecid == GENERAL["UniqueChipID"]
    assert identity.kvs["NULL"]["ProductVersion"] == "27.0.1"
    assert "SerialNumber" not in identity.kvs["NULL"]
    assert identity.kvs["NULL"]["UserAssignedDeviceName"] == "Test iPhone"
    assert identity.kvs["com.apple.security.mac.amfi"] == {"DeveloperModeStatus": True}

    again = DeviceHubBridge(FakeBroker(), udid=UDID, state=state)
    assert (await again.prepare(49152))["identifier"] == info["identifier"]


@pytest.mark.anyio
async def test_existing_state_file_is_made_private(tmp_path):
    state = tmp_path / "state.json"
    state.write_text("{}")
    state.chmod(0o644)
    bridge = DeviceHubBridge(FakeBroker(), udid=UDID, state=state)
    bridge.identity = DeviceIdentity.generate(name="iPhone", model="iPhone17,1", udid=UDID)
    bridge._save()
    assert state.stat().st_mode & 0o777 == 0o600
    assert json.loads(state.read_text())["udid"] == UDID


@pytest.mark.anyio
async def test_stand_in_under_another_udid_has_its_own_identity(tmp_path):
    state = tmp_path / "state.json"
    await _prepared(tmp_path)
    original = json.loads(state.read_text())["identifier"]

    bridge = DeviceHubBridge(FakeBroker(), udid=UDID, state=state, presented_udid="STAND-IN")
    info = await bridge.prepare(49152)
    assert info["identifier"] != original
    assert bridge.identity is not None and bridge.identity.udid == "STAND-IN"
    assert bridge.identity.ecid not in (0, GENERAL["UniqueChipID"])


@pytest.mark.anyio
async def test_prepare_has_defaults_for_missing_values():
    bridge = DeviceHubBridge(FakeBroker(general={}), udid=UDID)
    info = await bridge.prepare(49152)
    assert (info["name"], info["model"]) == ("iOS device", "iPhone")


@pytest.mark.anyio
async def test_only_tcp_listeners_are_offered():
    bridge = await _prepared()
    assert await bridge._request("ping", {}) is None
    with pytest.raises(RemotePairingError, match="only TCP"):
        await bridge._request("createListener", {"transportProtocolType": "udp"})
    key = base64.b64encode(b"k" * 32).decode()
    assert await bridge._request("createListener", {"transportProtocolType": "tcp", "key": key}) == {"port": 49152}
    assert bridge._tunnel_keys[-1] == b"k" * 32


@pytest.mark.anyio
async def test_streams_need_a_prepared_identity():
    bridge = DeviceHubBridge(FakeBroker(), udid=UDID)
    with pytest.raises(RemotePairingError, match="before connecting"):
        await bridge.serve_control(_memory_pair()[0])
    with pytest.raises(RemotePairingError, match="before pairing"):
        await bridge.serve_pairing(_memory_pair()[0])
    with pytest.raises(RemotePairingError, match="no tunnel"):
        bridge._tunnel_context()


@pytest.mark.anyio
async def test_mac_control_connection_requests_a_tunnel_and_unpairs(tmp_path):
    bridge = await _prepared(tmp_path)
    identity = bridge.identity
    assert identity is not None
    mac = DeviceIdentity.generate(name="Mac", model="Mac", udid="MAC")
    identity.hosts[mac.identifier] = mac.public_key

    async with served(lambda stream: serve_connection(bridge, stream)) as stream:
        host = Host(stream, mac)
        await host.verify(identity.public_key)
        key = base64.b64encode(b"t" * 32).decode()
        reply = await host.request("createListener", {"transportProtocolType": "tcp", "key": key})
        assert reply["_1"] == {"createListener": {"port": 49152}}
        await host.request("unpair")
    assert identity.hosts == {}
    assert json.loads((tmp_path / "state.json").read_text())["hosts"] == {}


@pytest.mark.anyio
async def test_unexpected_first_byte_ends_the_connection():
    bridge = await _prepared()
    with patch.object(devicehub, "logger") as logger:
        async with served(lambda stream: serve_connection(bridge, stream)) as stream:
            await stream.send(b"GET / HTTP/1.1\r\n")
            with pytest.raises((anyio.EndOfStream, anyio.ClosedResourceError)):
                await stream.receive()
    assert logged(logger.warning) == ["Device Hub stream failed: unexpected first byte b'G' from the Mac"]


@pytest.mark.anyio
async def test_unexpected_handler_errors_are_logged_not_raised():
    async def handler(stream):
        raise ValueError("malformed handshake")

    with patch.object(devicehub, "logger") as logger:
        async with served(handler) as stream:
            with pytest.raises((anyio.EndOfStream, anyio.ClosedResourceError)):
                await stream.receive()
    assert logged(logger.warning) == ["Device Hub stream failed: malformed handshake"]


@pytest.mark.anyio
async def test_served_keeps_caller_errors():
    async def handler(stream):
        await anyio.sleep_forever()

    with pytest.raises(KeyError):
        async with served(handler):
            raise KeyError("from the test")


def _mac_context(key: bytes) -> ssl.SSLContext:
    context = STDLIB_CONTEXT(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers(MAC_TUNNEL_CIPHERS)
    context.set_psk_client_callback(lambda _hint: (None, key))
    return context


@pytest.mark.skipif(not hasattr(ssl.SSLContext, "set_psk_client_callback"), reason="TLS-PSK needs Python 3.13")
@pytest.mark.anyio
async def test_tunnel_is_spliced_onto_the_device_tunnel():
    bridge = await _prepared()
    key = b"p" * 32
    bridge._tunnel_keys.append(key)
    context = _mac_context(key)

    async with served(lambda stream: serve_connection(bridge, stream)) as stream:
        tunnel = await TLSStream.wrap(stream, server_side=False, ssl_context=context, standard_compatible=False)
        await tunnel.send(_cdtunnel({"type": "clientHandshakeRequest", "mtu": 16000}))
        assert await _read_cdtunnel(BufferedByteReceiveStream(tunnel)) == TUNNEL_RESPONSE
        await tunnel.send(b"ipv6 packet")
        assert await tunnel.receive() == b"ipv6 packet"
    assert bridge.broker.services == [CORE_DEVICE_PROXY]
    assert bridge.broker.requests == [{"type": "clientHandshakeRequest", "mtu": 16000}]


@pytest.mark.skipif(not hasattr(ssl.SSLContext, "set_psk_client_callback"), reason="TLS-PSK needs Python 3.13")
@pytest.mark.anyio
async def test_tunnel_keeps_its_psk_when_ssl_context_is_replaced():
    bridge = await _prepared()
    key = b"p" * 32
    bridge._tunnel_keys.append(key)
    context = _mac_context(key)

    with patch.object(ssl, "SSLContext", TruststoreContext):
        async with served(lambda stream: serve_connection(bridge, stream)) as stream:
            tunnel = await TLSStream.wrap(stream, server_side=False, ssl_context=context, standard_compatible=False)
            await tunnel.send(_cdtunnel({"type": "clientHandshakeRequest", "mtu": 16000}))
            assert await _read_cdtunnel(BufferedByteReceiveStream(tunnel)) == TUNNEL_RESPONSE


@pytest.mark.anyio
async def test_stand_in_pairs_with_a_mac_using_the_code_it_shows(tmp_path):
    bridge = await _prepared(tmp_path)
    identity = bridge.identity
    assert identity is not None
    mac = DeviceIdentity.generate(name="Mac", model="Mac", udid="MAC")

    async def no_requests(_name, _body):
        return None

    bridge.set_pairing_code("000000")
    async with served(bridge.serve_pairing) as stream:
        channel = ControlChannel(stream, mac, on_request=no_requests, allow_pair_setup=True)
        # The stand-in closes the connection once pairing is done.
        with anyio.fail_after(10), pytest.raises(anyio.IncompleteRead):
            await channel.serve()
    assert channel.encryption_key is not None
    assert identity.hosts == {mac.identifier: mac.public_key}
    assert mac.identifier in json.loads((tmp_path / "state.json").read_text())["hosts"]
    assert not bridge._code_ready.is_set()


@pytest.mark.anyio
async def test_tunnel_handshakes_are_framed():
    with pytest.raises(RemotePairingError, match="too large"):
        _cdtunnel({"blob": "x" * 70000})

    stream, other = _memory_pair()
    await other.send(b"NotATunn")
    with pytest.raises(RemotePairingError, match="not a CoreDevice tunnel"):
        await _read_cdtunnel(BufferedByteReceiveStream(stream))

    stream, other = _memory_pair()
    await other.send(CDTUNNEL_MAGIC + struct.pack(">H", 2) + b"[]")
    with pytest.raises(RemotePairingError, match="invalid CoreDevice tunnel"):
        await _read_cdtunnel(BufferedByteReceiveStream(stream))
