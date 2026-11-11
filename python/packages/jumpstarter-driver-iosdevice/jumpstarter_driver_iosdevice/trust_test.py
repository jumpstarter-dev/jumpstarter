import datetime
import plistlib
import struct
import time
from types import SimpleNamespace

import pytest
from anyio import create_task_group, fail_after
from anyio.streams.buffered import BufferedByteReceiveStream
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from . import trust
from .trust import ExporterTrustBroker, _credentials, _receive_plist, _send_plist
from .usbmux import UsbMuxDaemon, UsbMuxError, UsbMuxResultError, _memory_pair


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def owner_record():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test owner identity")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()
    )
    return {
        "HostID": "OWNER-HOST",
        "SystemBUID": "OWNER-SYSTEM",
        "EscrowBag": b"owner-escrow-secret",
        "HostCertificate": pem,
        "HostPrivateKey": private,
        "RootPrivateKey": private,
        "RootCertificate": pem,
        "DeviceCertificate": pem,
    }


@pytest.fixture(scope="module")
def identity(owner_record):
    return _credentials(owner_record)


def test_disposable_identity_contains_no_owner_material(identity, owner_record):
    assert set(identity.public_record) == {
        "HostID",
        "SystemBUID",
        "EscrowBag",
        "HostCertificate",
        "HostPrivateKey",
        "RootCertificate",
        "RootPrivateKey",
        "DeviceCertificate",
    }
    for key, value in identity.public_record.items():
        assert value != owner_record[key]
    assert identity.host_id == owner_record["HostID"]
    assert identity.escrow_bag == owner_record["EscrowBag"]
    for name in ("HostCertificate", "DeviceCertificate"):
        cert = x509.load_pem_x509_certificate(identity.public_record[name])
        root = x509.load_pem_x509_certificate(identity.public_record["RootCertificate"])
        cert.verify_directly_issued_by(root)
    assert b"owner-escrow-secret" not in plistlib.dumps(identity.public_record)
    assert not hasattr(identity, "host_private_key")


def test_tls_certificate_files_are_private_and_removed(monkeypatch, owner_record, tmp_path):
    real_tempdir = trust.tempfile.TemporaryDirectory
    seen = []

    def temporary_directory(**kwargs):
        directory = real_tempdir(dir=tmp_path, **kwargs)
        seen.append(directory.name)
        return directory

    monkeypatch.setattr(trust.tempfile, "TemporaryDirectory", temporary_directory)
    _credentials(owner_record)
    assert len(seen) == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "options",
    [
        {"udid": ""},
        {"allowed_ports": [62078]},
        {"allowed_ports": [True]},
        {"allowed_ports": [65536]},
        {"device_id": True},
        {"service_ttl": False},
        {"service_ttl": float("inf")},
        {"service_ttl": 0},
        {"max_services": 0},
        {"max_connections": True},
    ],
)
def test_invalid_broker_settings(options):
    settings = {"udid": "device"} | options
    with pytest.raises(ValueError):
        ExporterTrustBroker(udid=settings.pop("udid"), **settings)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "data",
    [
        struct.pack(">I", 0),
        struct.pack(">I", 2**31),
        struct.pack(">I", 3) + b"bad",
        struct.pack(">I", len(plistlib.dumps([]))) + plistlib.dumps([]),
    ],
)
async def test_bounded_lockdown_frames(data):
    client, server = _memory_pair()
    async with client, server:
        await client.send(data)
        with pytest.raises(UsbMuxError):
            await _receive_plist(BufferedByteReceiveStream(server))


@pytest.mark.anyio
async def test_lockdown_frames_preserve_coalesced_following_bytes():
    client, server = _memory_pair()
    message = plistlib.dumps({"Request": "QueryType"})
    async with client, server:
        await client.send(struct.pack(">I", len(message)) + message + b"following")
        reader = BufferedByteReceiveStream(server)
        assert await _receive_plist(reader) == {"Request": "QueryType"}
        assert await reader.receive_exactly(9) == b"following"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload,error",
    [
        ({"Request": "Pair", "PairRecord": {}}, "InvalidRequest"),
        ({"Request": "QueryType", "PairRecord": {}}, "InvalidRequest"),
        ({"Request": "Unpair", "PairRecord": {}}, "InvalidRequest"),
        ({"Request": "ValidatePair", "PairRecord": {}}, "InvalidRequest"),
        ({"Request": "GetValue", "Key": "HostPrivateKey"}, "GetProhibited"),
        ({"Request": "GetValue", "Domain": "com.apple.mobile.wireless_lockdown"}, "GetProhibited"),
        ({"Request": "SetValue", "Key": "DeviceName", "Value": "test"}, "SessionInactive"),
        ({"Request": "StartService", "Service": "com.apple.afc"}, "SessionInactive"),
    ],
)
async def test_unsafe_and_unauthenticated_requests_never_reach_device(payload, error):
    broker = ExporterTrustBroker(udid="device")
    client, broker_client = _memory_pair()
    upstream, device = _memory_pair()
    async with client, broker_client, upstream, device, create_task_group() as tasks:
        tasks.start_soon(broker._lockdown, broker_client, BufferedByteReceiveStream(broker_client), upstream)
        await _send_plist(client, payload)
        response = await _receive_plist(BufferedByteReceiveStream(client))
        assert response["Error"] == error
        assert device.receive_stream.statistics().current_buffer_used == 0
        tasks.cancel_scope.cancel()


def test_response_redaction_covers_nested_pairing_fields():
    broker = ExporterTrustBroker(udid="device")
    assert broker._clean(
        {"Value": {"DeviceName": "test", "HostID": "secret", "Nested": [{"EscrowBag": b"secret", "okay": 1}]}}
    ) == {"Value": {"DeviceName": "test", "Nested": [{"okay": 1}]}}


def test_service_tokens_are_one_generation_bounded_and_not_reused(monkeypatch):
    broker = ExporterTrustBroker(udid="device", device_id=7, allowed_ports=[1024], max_services=1, service_ttl=5)
    now = time.monotonic()
    monkeypatch.setattr(trust.time, "monotonic", lambda: now)
    first = broker._reserve_service(12345, True)
    assert first == 1025
    with pytest.raises(UsbMuxError, match="too many"):
        broker._reserve_service(12345, False)
    monkeypatch.setattr(trust.time, "monotonic", lambda: now + 6)
    second = broker._reserve_service(12345, False)
    assert second > first
    assert first not in broker._services
    assert broker._services[second].device_id == 7
    broker.invalidate()
    assert not broker.is_active and not broker._services


@pytest.mark.anyio
async def test_wrong_client_device_id_does_not_revoke_valid_device(monkeypatch):
    async def devices(self):
        return [{"DeviceID": 7, "Properties": {"SerialNumber": "device"}}]

    monkeypatch.setattr(UsbMuxDaemon, "list_devices", devices)
    broker = ExporterTrustBroker(udid="device", device_id=7)
    with pytest.raises(UsbMuxResultError):
        await broker._device_id("device", 8)
    assert broker.is_active
    assert await broker._device_id("device", 7) == 7


@pytest.mark.anyio
async def test_device_generation_change_revokes_credentials_and_services(monkeypatch, identity):
    async def devices(self):
        return [{"DeviceID": 8, "Properties": {"SerialNumber": "device"}}]

    monkeypatch.setattr(UsbMuxDaemon, "list_devices", devices)
    broker = ExporterTrustBroker(udid="device", device_id=7)
    broker._identity = identity
    broker._reserve_service(12345, True)
    with pytest.raises(UsbMuxError, match="generation"):
        await broker._device_id("device")
    assert not broker.is_active and broker._identity is None and not broker._services


@pytest.mark.anyio
@pytest.mark.parametrize(
    "server,certificate,accepted",
    [
        (False, b"device", True),
        (False, b"wrong", False),
        (True, b"host", True),
        (True, b"root", True),
        (True, b"forged-leaf", False),
    ],
)
async def test_exact_peer_pins_on_both_tls_legs(monkeypatch, server, certificate, accepted):
    broker = ExporterTrustBroker(udid="device")
    identity = SimpleNamespace(
        upstream_context=None,
        downstream_context=None,
        device_certificate=b"device",
        client_certificate=b"host",
        root_certificate=b"root",
    )

    class Stream:
        closed = False

        def extra(self, attribute):
            return certificate

        async def aclose(self):
            self.closed = True

    secured = Stream()

    async def prepare():
        return identity

    async def wrap(*args, **kwargs):
        return secured

    monkeypatch.setattr(broker, "prepare", prepare)
    monkeypatch.setattr(trust.TLSStream, "wrap", wrap)
    if accepted:
        assert await broker._tls(object(), server=server) is secured
        assert not secured.closed
    else:
        with pytest.raises(UsbMuxError, match="peer"):
            await broker._tls(object(), server=server)
        assert secured.closed


@pytest.mark.anyio
async def test_invalidation_cancels_active_stream_scope(identity):
    broker = ExporterTrustBroker(udid="device")
    broker._identity = identity
    async with broker._scope():
        broker.invalidate()
        from anyio import sleep_forever

        with fail_after(1):
            await sleep_forever()
    assert not broker._scopes and broker._identity is None
    await broker.aclose()


@pytest.mark.anyio
async def test_invalidated_broker_cannot_read_or_connect():
    broker = ExporterTrustBroker(udid="device", allowed_ports=[8100])
    broker.invalidate()
    with pytest.raises(UsbMuxError):
        await broker.prepare()
    with pytest.raises(UsbMuxError):
        async with broker.proxy("device"):
            raise AssertionError("revoked stream was exposed")
    with pytest.raises(UsbMuxError):
        async with broker.open_forward(8100):
            raise AssertionError("revoked forward was exposed")
