import datetime
import plistlib
import socket
import ssl
import struct
from contextlib import asynccontextmanager

import pytest
from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    IncompleteRead,
    connect_tcp,
    create_task_group,
    create_tcp_listener,
    fail_after,
    sleep,
)
from anyio.abc import SocketAttribute
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSAttribute, TLSStream
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .trust import ExporterTrustBroker
from .usbmux import UsbMuxError

pytestmark = pytest.mark.anyio
UDID = "test-device"
DEVICE_ID = 42
WIFI_DEVICE_ID = 43
LOCKDOWN = 62078
SERVICE = 54321


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _certificate(name, key, issuer_key, issuer_name, *, ca=False):
    now = datetime.datetime.now(datetime.UTC)
    return (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .sign(issuer_key, hashes.SHA256())
    )


def _ssl_context(directory, cert, key, authority=None, *, server=False):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    if not server:
        context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED if authority else ssl.CERT_NONE
    if authority:
        context.load_verify_locations(cadata=authority.decode())
    path = directory / ("server.pem" if server else "client.pem")
    path.write_bytes(cert + key)
    path.chmod(0o600)
    context.load_cert_chain(path)
    path.unlink()
    return context


@pytest.fixture(scope="module")
def paired_peer(tmp_path_factory):
    directory = tmp_path_factory.mktemp("ios-paired-tls")
    root_key, host_key, device_key, wrong_key = [
        rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(4)
    ]
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test pairing authority")])
    root_cert = _certificate("Test pairing authority", root_key, root_key, root_name, ca=True)
    host_cert = _certificate("Original host", host_key, root_key, root_name)
    device_cert = _certificate("Original device", device_key, root_key, root_name)
    wrong_cert = _certificate("Different device", wrong_key, root_key, root_name)
    pem = serialization.Encoding.PEM

    def key_data(key):
        return key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())

    root = root_cert.public_bytes(pem)
    record = {
        "HostID": "REAL-HOST-IDENTITY",
        "SystemBUID": "REAL-SYSTEM-IDENTITY",
        "HostCertificate": host_cert.public_bytes(pem),
        "HostPrivateKey": key_data(host_key),
        "DeviceCertificate": device_cert.public_bytes(pem),
        "RootCertificate": root,
        "RootPrivateKey": key_data(root_key),
        "EscrowBag": b"REAL-ESCROW-SECRET",
    }
    context = _ssl_context(directory, device_cert.public_bytes(pem), key_data(device_key), root, server=True)
    wrong = _ssl_context(directory, wrong_cert.public_bytes(pem), key_data(wrong_key), root, server=True)
    return record, context, wrong


async def _read_mux(reader):
    length, version, kind, tag = struct.unpack("<IIII", await reader.receive_exactly(16))
    assert version == 1 and kind == 8 and 16 < length < 2**20
    return tag, plistlib.loads(await reader.receive_exactly(length - 16))


async def _write_mux(stream, message, tag=1):
    body = plistlib.dumps(message)
    await stream.send(struct.pack("<IIII", len(body) + 16, 1, 8, tag) + body)


async def _read_lockdown(reader):
    (length,) = struct.unpack(">I", await reader.receive_exactly(4))
    assert 0 < length < 2**20
    return plistlib.loads(await reader.receive_exactly(length))


async def _write_lockdown(stream, message):
    body = plistlib.dumps(message)
    await stream.send(struct.pack(">I", len(body)) + body)


async def _copy(source, destination):
    async for data in source:
        await destination.send(data)


class PairedDevice:
    """A usbmux/lockdown peer with separate framing and TLS implementations."""

    def __init__(self, paired_peer, *, service_tls=True, wrong_certificate=False, wifi_copy=False):
        self.record, context, wrong = paired_peer
        self.context = wrong if wrong_certificate else context
        self.service_tls = service_tls
        self.requests = []
        self.received_service = []
        self.watchers = []
        self.active = 0
        self.device = {
            "DeviceID": DEVICE_ID,
            "Properties": {"DeviceID": DEVICE_ID, "SerialNumber": UDID, "ConnectionType": "USB"},
        }
        # With Wi-Fi sync, usbmuxd also lists the same device over the network.
        self.devices = [self.device]
        if wifi_copy:
            wifi = {"DeviceID": WIFI_DEVICE_ID, "SerialNumber": UDID, "ConnectionType": "Network"}
            self.devices.insert(0, {"DeviceID": WIFI_DEVICE_ID, "Properties": wifi})

    async def handle(self, stream):
        self.active += 1
        try:
            async with stream:
                await self._mux(stream)
        except (
            EndOfStream,
            IncompleteRead,
            BrokenResourceError,
            ClosedResourceError,
            ssl.SSLError,
        ):
            pass
        finally:
            self.active -= 1

    async def _mux(self, stream):
        reader = BufferedByteReceiveStream(stream)
        while True:
            tag, request = await _read_mux(reader)
            kind = request["MessageType"]
            if kind == "ListDevices":
                response = {"DeviceList": self.devices}
            elif kind == "ReadPairRecord":
                response = {"PairRecordData": plistlib.dumps(self.record)}
            elif kind == "Listen":
                await _write_mux(stream, {"MessageType": "Result", "Number": 0}, tag)
                for device in self.devices:
                    await _write_mux(stream, {"MessageType": "Attached", **device}, 0)
                self.watchers.append(stream)
                try:
                    await stream.receive()
                finally:
                    self.watchers.remove(stream)
                return
            elif kind == "Connect":
                assert request["DeviceID"] == DEVICE_ID
                await _write_mux(stream, {"MessageType": "Result", "Number": 0}, tag)
                port = socket.ntohs(request["PortNumber"])
                if port == LOCKDOWN:
                    await self._lockdown(stream)
                elif port == SERVICE:
                    await self._service(stream)
                else:
                    raise AssertionError(f"unadvertised device port {port}")
                return
            else:
                raise AssertionError(f"unsafe usbmux command {kind}")
            await _write_mux(stream, response, tag)

    async def _lockdown(self, stream):
        reader = BufferedByteReceiveStream(stream)
        while True:
            request = await _read_lockdown(reader)
            self.requests.append(request)
            name = request["Request"]
            response = {"Request": name}
            if name == "StartSession":
                assert request["HostID"] == self.record["HostID"]
                assert request["SystemBUID"] == self.record["SystemBUID"]
                response.update(EnableSessionSSL=True, SessionID="REAL-SESSION-IDENTITY")
                await _write_lockdown(stream, response)
                stream = await TLSStream.wrap(
                    stream, server_side=True, ssl_context=self.context, standard_compatible=False
                )
                assert stream.extra(TLSAttribute.peer_certificate_binary) == ssl.PEM_cert_to_DER_cert(
                    self.record["HostCertificate"].decode()
                )
                reader = BufferedByteReceiveStream(stream)
                continue
            if name == "StartService":
                assert request["Service"] == "test.service"
                if "EscrowBag" in request:
                    assert request["EscrowBag"] == self.record["EscrowBag"]
                response.update(Port=SERVICE, EnableServiceSSL=self.service_tls)
            elif name == "GetValue":
                response["Value"] = {
                    "ProductType": "TestPhone",
                    "HostPrivateKey": self.record["HostPrivateKey"],
                    "Nested": {"EscrowBag": self.record["EscrowBag"]},
                }
            elif name == "StopSession":
                assert request["SessionID"] == "REAL-SESSION-IDENTITY"
                await _write_lockdown(stream, response)
                stream, remainder = await stream.unwrap()
                assert not remainder
                reader = BufferedByteReceiveStream(stream)
                continue
            elif name == "Goodbye":
                await _write_lockdown(stream, response)
                return
            else:
                assert name == "QueryType"
                response["Type"] = "com.apple.mobile.lockdown"
            await _write_lockdown(stream, response)

    async def _service(self, stream):
        if self.service_tls:
            stream = await TLSStream.wrap(stream, server_side=True, ssl_context=self.context, standard_compatible=False)
        async for data in stream:
            self.received_service.append(data)
            await stream.send(b"reply:" + data)

    async def detach(self):
        for watcher in list(self.watchers):
            await _write_mux(watcher, {"MessageType": "Detached", "DeviceID": DEVICE_ID}, 0)


@asynccontextmanager
async def _running(paired_peer, *, connection_type=None, **kwargs):
    peer = PairedDevice(paired_peer, **kwargs)
    upstream = await create_tcp_listener(local_host="127.0.0.1")
    host, port = upstream.extra(SocketAttribute.local_address)
    broker = ExporterTrustBroker(
        udid=UDID, device_id=DEVICE_ID, host=host, port=port, timeout=2, connection_type=connection_type
    )
    front = await create_tcp_listener(local_host="127.0.0.1")
    address = front.extra(SocketAttribute.local_address)

    async def serve_front(stream):
        try:
            async with stream, broker.proxy(UDID) as proxied, create_task_group() as pumps:

                async def copy_close(source, destination):
                    try:
                        await _copy(source, destination)
                    except (EndOfStream, BrokenResourceError, ClosedResourceError):
                        pass
                    finally:
                        pumps.cancel_scope.cancel()

                pumps.start_soon(copy_close, stream, proxied)
                pumps.start_soon(copy_close, proxied, stream)
        except (EndOfStream, BrokenResourceError, ClosedResourceError, UsbMuxError):
            pass

    async with upstream, front, create_task_group() as tasks:
        tasks.start_soon(upstream.serve, peer.handle)
        tasks.start_soon(front.serve, serve_front)
        try:
            yield broker, peer, address
        finally:
            await broker.aclose()
            tasks.cancel_scope.cancel()


async def _mux_query(address, request):
    host, port = address
    async with await connect_tcp(host, port) as stream:
        await _write_mux(stream, request)
        _, response = await _read_mux(BufferedByteReceiveStream(stream))
        return response


async def _public_record(address):
    response = await _mux_query(address, {"MessageType": "ReadPairRecord", "PairRecordID": UDID})
    return plistlib.loads(response["PairRecordData"])


async def _connect(address, port):
    host, mux_port = address
    stream = await connect_tcp(host, mux_port)
    await _write_mux(stream, {"MessageType": "Connect", "DeviceID": DEVICE_ID, "PortNumber": socket.htons(port)})
    _, response = await _read_mux(BufferedByteReceiveStream(stream))
    assert response == {"MessageType": "Result", "Number": 0}
    return stream


async def _start_session(address, record, directory, identity="Host"):

    stream = await _connect(address, LOCKDOWN)
    await _write_lockdown(
        stream,
        {"Request": "StartSession", "HostID": record["HostID"], "SystemBUID": record["SystemBUID"]},
    )
    response = await _read_lockdown(BufferedByteReceiveStream(stream))
    assert response["EnableSessionSSL"] is True
    assert response["SessionID"] != "REAL-SESSION-IDENTITY"
    context = _ssl_context(
        directory,
        record[f"{identity}Certificate"],
        record[f"{identity}PrivateKey"],
        record["RootCertificate"],
    )
    secured = await TLSStream.wrap(stream, server_side=False, ssl_context=context, standard_compatible=False)
    assert secured.extra(TLSAttribute.peer_certificate_binary) == ssl.PEM_cert_to_DER_cert(
        record["DeviceCertificate"].decode()
    )
    return secured, response["SessionID"], context


@pytest.mark.parametrize("identity", ["Host", "Root"])
async def test_original_credentials_stay_exporter_only_and_tls_identity_is_pinned(paired_peer, tmp_path, identity):
    async with _running(paired_peer) as (_, peer, address):
        record = await _public_record(address)
        for key in (
            "HostID",
            "SystemBUID",
            "HostPrivateKey",
            "HostCertificate",
            "DeviceCertificate",
            "RootPrivateKey",
            "EscrowBag",
        ):
            assert record[key] != peer.record[key]
        assert "DevicePrivateKey" not in record
        async with (await _start_session(address, record, tmp_path, identity))[0] as stream:
            await _write_lockdown(stream, {"Request": "GetValue"})
            response = await _read_lockdown(BufferedByteReceiveStream(stream))
            assert response["Value"] == {"ProductType": "TestPhone", "Nested": {}}


async def test_usb_broker_ignores_wifi_copy_of_leased_device(paired_peer, tmp_path):
    async with _running(paired_peer, connection_type="USB", wifi_copy=True) as (broker, _, address):
        devices = await _mux_query(address, {"MessageType": "ListDevices"})
        assert [device["DeviceID"] for device in devices["DeviceList"]] == [DEVICE_ID]
        record = await _public_record(address)
        async with (await _start_session(address, record, tmp_path))[0] as stream:
            await _write_lockdown(stream, {"Request": "GetValue"})
            assert (await _read_lockdown(BufferedByteReceiveStream(stream)))["Value"]["ProductType"] == "TestPhone"
        assert broker.is_active and broker.device_id == DEVICE_ID


@pytest.mark.parametrize("service_tls", [False, True])
async def test_service_uses_disposable_client_tls_after_lockdown_closes(paired_peer, tmp_path, service_tls):
    async with _running(paired_peer, service_tls=service_tls) as (_, peer, address):
        record = await _public_record(address)
        stream, _, context = await _start_session(address, record, tmp_path)
        await _write_lockdown(
            stream,
            {
                "Request": "StartService",
                "Service": "test.service",
                "EscrowBag": record["EscrowBag"],
            },
        )
        response = await _read_lockdown(BufferedByteReceiveStream(stream))
        assert response["Port"] != SERVICE
        assert response["EnableServiceSSL"] is True
        await stream.aclose()  # go-ios closes lockdown before opening the service socket.
        raw = await _connect(address, response["Port"])
        async with await TLSStream.wrap(
            raw, server_side=False, ssl_context=context, standard_compatible=False
        ) as service:
            await service.send(b"service-application-bytes")
            assert await service.receive() == b"reply:service-application-bytes"
        assert peer.received_service == [b"service-application-bytes"]
        replay = await _mux_query(
            address,
            {
                "MessageType": "Connect",
                "DeviceID": DEVICE_ID,
                "PortNumber": socket.htons(response["Port"]),
            },
        )
        assert replay["Number"] != 0


async def test_stop_session_downgrades_and_allows_a_new_tls_session(paired_peer, tmp_path):
    async with _running(paired_peer) as (_, _, address):
        record = await _public_record(address)
        secured, session, context = await _start_session(address, record, tmp_path)
        await _write_lockdown(secured, {"Request": "StopSession", "SessionID": session})
        assert (await _read_lockdown(BufferedByteReceiveStream(secured)))["Request"] == "StopSession"
        raw, remainder = await secured.unwrap()
        assert not remainder
        async with raw:
            await _write_lockdown(raw, {"Request": "QueryType"})
            assert (await _read_lockdown(BufferedByteReceiveStream(raw)))["Type"] == "com.apple.mobile.lockdown"
            await _write_lockdown(
                raw,
                {
                    "Request": "StartSession",
                    "HostID": record["HostID"],
                    "SystemBUID": record["SystemBUID"],
                },
            )
            response = await _read_lockdown(BufferedByteReceiveStream(raw))
            assert response["EnableSessionSSL"] is True
            assert response["SessionID"] != session
            async with await TLSStream.wrap(
                raw, server_side=False, ssl_context=context, standard_compatible=False
            ) as secured:
                await _write_lockdown(secured, {"Request": "GetValue"})
                assert (await _read_lockdown(BufferedByteReceiveStream(secured)))["Value"]["ProductType"] == "TestPhone"


async def test_pending_service_is_revoked_on_detach_without_open_lockdown(paired_peer, tmp_path):
    async with _running(paired_peer, service_tls=False) as (_, peer, address):
        record = await _public_record(address)
        stream, _, _ = await _start_session(address, record, tmp_path)
        await _write_lockdown(stream, {"Request": "StartService", "Service": "test.service"})
        response = await _read_lockdown(BufferedByteReceiveStream(stream))
        await stream.aclose()
        await sleep(0.02)
        assert peer.watchers, "No detach monitor remains while a service reservation is pending"
        await peer.detach()
        await sleep(0.02)
        with (
            fail_after(2),
            pytest.raises((EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError)),
        ):
            await _mux_query(
                address,
                {
                    "MessageType": "Connect",
                    "DeviceID": DEVICE_ID,
                    "PortNumber": socket.htons(response["Port"]),
                },
            )
        assert not peer.received_service


async def test_wrong_device_certificate_is_rejected_before_session_response(paired_peer):
    async with _running(paired_peer, wrong_certificate=True) as (_, peer, address):
        record = await _public_record(address)
        async with await _connect(address, LOCKDOWN) as stream:
            await _write_lockdown(
                stream,
                {
                    "Request": "StartSession",
                    "HostID": record["HostID"],
                    "SystemBUID": record["SystemBUID"],
                },
            )
            with (
                fail_after(3),
                pytest.raises((EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError)),
            ):
                await _read_lockdown(BufferedByteReceiveStream(stream))
        assert [request["Request"] for request in peer.requests] == ["StartSession"]


async def test_root_signed_unlisted_client_leaf_is_rejected(paired_peer, tmp_path):
    async with _running(paired_peer) as (_, peer, address):
        record = await _public_record(address)
        root_cert = x509.load_pem_x509_certificate(record["RootCertificate"])
        root_key = serialization.load_pem_private_key(record["RootPrivateKey"], password=None)
        rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        rogue_cert = _certificate("Not the pinned client", rogue_key, root_key, root_cert.subject)
        context = _ssl_context(
            tmp_path,
            rogue_cert.public_bytes(serialization.Encoding.PEM),
            rogue_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            record["RootCertificate"],
        )
        raw = await _connect(address, LOCKDOWN)
        await _write_lockdown(
            raw,
            {
                "Request": "StartSession",
                "HostID": record["HostID"],
                "SystemBUID": record["SystemBUID"],
            },
        )
        assert (await _read_lockdown(BufferedByteReceiveStream(raw)))["EnableSessionSSL"] is True
        async with await TLSStream.wrap(
            raw, server_side=False, ssl_context=context, standard_compatible=False
        ) as secured:
            with (
                fail_after(3),
                pytest.raises((EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError)),
            ):
                await _write_lockdown(secured, {"Request": "GetValue"})
                await _read_lockdown(BufferedByteReceiveStream(secured))
        assert [request["Request"] for request in peer.requests] == ["StartSession"]


async def test_revocation_closes_active_tls_service_before_more_application_bytes(paired_peer, tmp_path):
    async with _running(paired_peer) as (broker, peer, address):
        record = await _public_record(address)
        stream, _, context = await _start_session(address, record, tmp_path)
        await _write_lockdown(stream, {"Request": "StartService", "Service": "test.service"})
        response = await _read_lockdown(BufferedByteReceiveStream(stream))
        await stream.aclose()
        raw = await _connect(address, response["Port"])
        async with await TLSStream.wrap(
            raw, server_side=False, ssl_context=context, standard_compatible=False
        ) as service:
            await service.send(b"before-revocation")
            assert await service.receive() == b"reply:before-revocation"
            broker.invalidate()
            with (
                fail_after(2),
                pytest.raises((EndOfStream, IncompleteRead, BrokenResourceError, ClosedResourceError)),
            ):
                await service.receive()
            with pytest.raises((BrokenResourceError, ClosedResourceError, EndOfStream)):
                await service.send(b"after-revocation")
        assert peer.received_service == [b"before-revocation"]
