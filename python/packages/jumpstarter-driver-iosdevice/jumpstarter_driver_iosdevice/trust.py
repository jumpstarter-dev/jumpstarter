"""Exporter-owned lockdown trust with disposable client TLS identities.

The client authenticates to this broker using a lease-only identity. The broker
independently authenticates to the device and pins its paired certificate. These
are two TLS connections; original device pairing credentials never leave here.
"""

import asyncio
import datetime
import math
import os
import plistlib
import socket
import ssl
import struct
import tempfile
import time
from collections.abc import Collection
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4
from xml.parsers.expat import ExpatError

from anyio import (
    BrokenResourceError,
    CancelScope,
    ClosedResourceError,
    EndOfStream,
    Event,
    IncompleteRead,
    Lock,
    create_task_group,
    fail_after,
    to_thread,
)
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSAttribute, TLSStream
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from .usbmux import (
    RESULT_BADCOMMAND,
    RESULT_BADDEV,
    RESULT_CONNREFUSED,
    RESULT_OK,
    UsbMuxDaemon,
    UsbMuxError,
    UsbMuxResultError,
    _BufferedDuplexStream,
    _check_result,
    _matches,
    _relay,
    _result,
    _uint,
    encode_message,
    receive_message,
)

if TYPE_CHECKING:
    from .https import HttpsIdentity

LOCKDOWN_PORT = 62078
MAX_LOCKDOWN_MESSAGE = 1024 * 1024
# These services drop TLS after authentication; this broker requires full TLS.
HANDSHAKE_ONLY_SERVICES = frozenset(
    {
        "com.apple.instruments.remoteserver",
        "com.apple.accessibility.axAuditDaemon.remoteserver",
        "com.apple.testmanagerd.lockdown",
        "com.apple.debugserver",
    }
)
_PAIR_FIELDS = frozenset(
    {
        "HostID",
        "SystemBUID",
        "EscrowBag",
        "HostCertificate",
        "HostPrivateKey",
        "RootCertificate",
        "RootPrivateKey",
        "DeviceCertificate",
        "DevicePrivateKey",
        "PairRecord",
    }
)
_WIRE_FIELDS = {
    "QueryType": (),
    "GetValue": ("Domain", "Key"),
    "SetValue": ("Domain", "Key", "Value"),
    "RemoveValue": ("Domain", "Key"),
    "StartSession": ("HostID", "SystemBUID"),
    "StopSession": ("SessionID",),
    "StartService": ("Service", "EscrowBag"),
    "Goodbye": (),
}


async def _receive_plist(reader):
    length = struct.unpack(">I", await reader.receive_exactly(4))[0]
    if not 0 < length <= MAX_LOCKDOWN_MESSAGE:
        raise UsbMuxError("invalid lockdown message length")
    try:
        payload = plistlib.loads(await reader.receive_exactly(length))
    except (plistlib.InvalidFileException, ValueError, OverflowError, ExpatError, RecursionError) as exc:
        raise UsbMuxError("invalid lockdown plist") from exc
    if not isinstance(payload, dict):
        raise UsbMuxError("lockdown plist must be a dictionary")
    return payload


async def _send_plist(stream, payload):
    data = plistlib.dumps(payload)
    if len(data) > MAX_LOCKDOWN_MESSAGE:
        raise UsbMuxError("lockdown message exceeds size limit")
    await stream.send(struct.pack(">I", len(data)) + data)


def _context(certificate, private_key, *, server=False, authority=None):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER if server else ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if server:
        context.verify_mode = ssl.CERT_REQUIRED
        assert authority is not None
        context.load_verify_locations(cadata=authority.decode("ascii"))
    else:
        context.check_hostname = False
        # Pairing certificates are not public-web PKI. Pin the exact device
        # certificate after the handshake, before releasing application bytes.
        context.verify_mode = ssl.CERT_NONE
    with tempfile.TemporaryDirectory(prefix="jumpstarter-ios-tls-") as directory:
        path = Path(directory) / "identity.pem"
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as identity:
            identity.write(certificate + b"\n" + private_key)
        try:
            context.load_cert_chain(path)
        finally:
            path.unlink()
    return context


@dataclass(repr=False)
class _Credentials:
    public_record: dict
    upstream_context: ssl.SSLContext
    downstream_context: ssl.SSLContext
    device_certificate: bytes
    client_certificate: bytes
    root_certificate: bytes
    host_id: str
    system_buid: str
    escrow_bag: bytes | None


def _credentials(record):
    """Build TLS state off the event loop; retain no original private-key bytes."""
    required = ("HostCertificate", "HostPrivateKey", "DeviceCertificate", "HostID", "SystemBUID")
    if any(key not in record for key in required):
        raise UsbMuxError("exporter pairing record is incomplete")
    if any(not isinstance(record[key], bytes) for key in required[:3]):
        raise UsbMuxError("exporter pairing certificates are invalid")
    if any(not isinstance(record[key], str) or not record[key] for key in required[3:]):
        raise UsbMuxError("exporter pairing identity is invalid")
    escrow = record.get("EscrowBag")
    if escrow is not None and not isinstance(escrow, bytes):
        raise UsbMuxError("exporter escrow record is invalid")
    upstream = _context(record["HostCertificate"], record["HostPrivateKey"])
    device_der = ssl.PEM_cert_to_DER_cert(record["DeviceCertificate"].decode("ascii"))

    now = datetime.datetime.now(datetime.UTC)
    root_key, host_key, device_key = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(3)]
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jumpstarter disposable iOS authority")])

    def certificate(key, name, *, ca=False, usage=None):
        subject = root_name if ca else x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(root_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=0 if ca else None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=not ca,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca,
                    crl_sign=ca,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
        )
        if usage is not None:
            builder = builder.add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        return builder.sign(root_key, hashes.SHA256())

    root_cert = certificate(root_key, "", ca=True)
    host_cert = certificate(host_key, "Jumpstarter disposable iOS client", usage=ExtendedKeyUsageOID.CLIENT_AUTH)
    device_cert = certificate(device_key, "Jumpstarter disposable iOS proxy", usage=ExtendedKeyUsageOID.SERVER_AUTH)
    pem = serialization.Encoding.PEM

    def key_pem(key):
        return key.private_bytes(pem, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())

    root_pem = root_cert.public_bytes(pem)
    virtual = {
        "HostID": str(uuid4()).upper(),
        "SystemBUID": str(uuid4()).upper(),
        "EscrowBag": uuid4().bytes,
        "HostCertificate": host_cert.public_bytes(pem),
        "HostPrivateKey": key_pem(host_key),
        "RootCertificate": root_pem,
        "RootPrivateKey": key_pem(root_key),
        "DeviceCertificate": device_cert.public_bytes(pem),
    }
    downstream = _context(
        device_cert.public_bytes(pem) + root_pem, key_pem(device_key), server=True, authority=root_pem
    )
    return _Credentials(
        virtual,
        upstream,
        downstream,
        device_der,
        host_cert.public_bytes(serialization.Encoding.DER),
        root_cert.public_bytes(serialization.Encoding.DER),
        record["HostID"],
        record["SystemBUID"],
        escrow,
    )


@dataclass(frozen=True)
class _Service:
    port: int
    tls: bool
    device_id: int
    expires: float


@dataclass(kw_only=True)
class ExporterTrustBroker(UsbMuxDaemon):
    """A single lease/device generation's virtual usbmux and lockdown endpoint."""

    udid: str
    device_id: int | None = None
    allowed_ports: Collection[int] = ()
    https_ports: Collection[int] = ()
    service_ttl: float = 60.0
    max_services: int = 128
    max_connections: int = 128
    _closed: bool = field(default=False, init=False, repr=False)
    _identity: _Credentials | None = field(default=None, init=False, repr=False)
    _prepare_lock: Lock | None = field(default=None, init=False, repr=False)
    _https_identity: "HttpsIdentity | None" = field(default=None, init=False, repr=False)
    _https_lock: Lock | None = field(default=None, init=False, repr=False)
    _services: dict[int, _Service] = field(default_factory=dict, init=False, repr=False)
    _next_port: int = field(default=1024, init=False, repr=False)
    _scopes: dict[CancelScope, asyncio.AbstractEventLoop | None] = field(default_factory=dict, init=False, repr=False)
    _monitor: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _monitor_ready: Event | None = field(default=None, init=False, repr=False)
    _monitor_lock: Lock | None = field(default=None, init=False, repr=False)
    _loop: asyncio.AbstractEventLoop | None = field(default=None, init=False, repr=False)

    def __post_init__(self):
        super().__post_init__()
        if not isinstance(self.udid, str) or not self.udid:
            raise ValueError("a nonempty UDID is required")
        if self.device_id is not None and not _uint(self.device_id):
            raise ValueError("invalid device ID")
        if any(not _uint(port, 65535) or port == 0 or port == LOCKDOWN_PORT for port in self.allowed_ports):
            raise ValueError("raw forward ports must be valid ports other than lockdown 62078")
        self.allowed_ports = frozenset(self.allowed_ports)
        if any(not _uint(port, 65535) or port == 0 or port == LOCKDOWN_PORT for port in self.https_ports):
            raise ValueError("HTTPS device ports must be valid ports other than lockdown 62078")
        self.https_ports = frozenset(self.https_ports)
        if self.https_ports & self.allowed_ports:
            raise ValueError("HTTPS device ports must not also be exposed as raw forwards")
        if isinstance(self.service_ttl, bool) or not isinstance(self.service_ttl, (int, float)):
            # Keep all invalid configuration values on the same validation API.
            raise ValueError("service TTL must be positive and finite")  # noqa: TRY004
        if not math.isfinite(self.service_ttl) or self.service_ttl <= 0:
            raise ValueError("service TTL must be positive and finite")
        if any(not _uint(limit, 65535) or limit == 0 for limit in (self.max_services, self.max_connections)):
            raise ValueError("broker resource limits must be positive integers")

    @property
    def is_active(self):
        return not self._closed

    def _check(self):
        if self._closed:
            raise UsbMuxError("iOS trust generation is no longer active")

    def invalidate(self):
        """Synchronously revoke this generation; safe after session shutdown."""
        self._closed = True
        self._identity = None
        self._https_identity = None
        self._services.clear()
        if self._monitor is not None and self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._monitor.cancel)
        for scope, loop in list(self._scopes.items()):
            if loop is not None and not loop.is_closed():
                loop.call_soon_threadsafe(scope.cancel)
            else:
                try:
                    scope.cancel()
                except RuntimeError:
                    pass  # Generation remains revoked even outside its backend.

    async def aclose(self):
        self.invalidate()
        if self._monitor is not None:
            with suppress(asyncio.CancelledError):
                await self._monitor
            self._monitor = None

    async def _start_monitor(self):
        self._check()
        if self._monitor_lock is None:
            self._monitor_lock = Lock()
        async with self._monitor_lock:
            if self._monitor is None:
                # Exporter RPCs run on grpc.aio's loop. An independently owned
                # task keeps generation monitoring alive between usbmux RPCs.
                self._loop = asyncio.get_running_loop()
                self._monitor_ready = Event()
                await self._device_id(self.udid)
                self._monitor = self._loop.create_task(self._watch_device())
            assert self._monitor_ready is not None
            try:
                with fail_after(self.timeout):
                    await self._monitor_ready.wait()
                self._check()
            except BaseException:
                self.invalidate()
                raise

    async def _device_id(self, udid, device_id=None):
        self._check()
        if udid != self.udid:
            raise UsbMuxError("device does not belong to this trust generation")
        try:
            current = await super()._device_id(udid)
        except UsbMuxResultError:
            self.invalidate()
            raise
        if self.device_id is None:
            self.device_id = current
        elif current != self.device_id:
            self.invalidate()
            raise UsbMuxError("device generation changed")
        if device_id is not None and device_id != current:
            raise UsbMuxResultError(RESULT_BADDEV)
        self._check()
        return current

    async def prepare(self) -> _Credentials:
        self._check()
        if self._prepare_lock is None:
            self._prepare_lock = Lock()
        async with self._prepare_lock:
            self._check()
            if self._identity is None:
                await self._device_id(self.udid)
                response = await super().query({"MessageType": "ReadPairRecord", "PairRecordID": self.udid})
                data = response.get("PairRecordData")
                if not isinstance(data, bytes):
                    raise UsbMuxError("device must be paired on the exporter before leasing")
                try:
                    record = plistlib.loads(data)
                    if not isinstance(record, dict):
                        # A parsed non-dictionary is an invalid pairing record value.
                        raise ValueError()  # noqa: TRY004
                    identity = await to_thread.run_sync(_credentials, record)
                except (
                    ValueError,
                    TypeError,
                    KeyError,
                    ssl.SSLError,
                    ExpatError,
                    plistlib.InvalidFileException,
                ) as exc:
                    raise UsbMuxError("exporter pairing credentials could not be loaded") from exc
                await self._device_id(self.udid)
                self._check()
                self._identity = identity
            assert self._identity is not None
            return self._identity

    @asynccontextmanager
    async def _scope(self):
        self._check()
        if len(self._scopes) >= self.max_connections:
            raise UsbMuxError("too many iOS trust connections")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        with CancelScope() as scope:
            self._scopes[scope] = loop
            try:
                yield
            finally:
                self._scopes.pop(scope, None)

    @asynccontextmanager
    async def proxy(self, udid):
        if udid != self.udid:
            raise UsbMuxError("device does not belong to this trust generation")
        async with self._scope():
            await self._start_monitor()
            async with super().proxy(udid) as stream:
                yield stream

    @asynccontextmanager
    async def open_forward(self, port):
        if port not in self.allowed_ports or port == LOCKDOWN_PORT:
            raise UsbMuxError("raw device port is not allowed by this trust broker")
        async with self._scope():
            await self._start_monitor()
            async with self.connect_device(self.udid, port, device_id=self.device_id) as stream:
                yield stream

    async def https_certificate(self, port: int) -> str:
        """Prepare this generation's independent, exporter-only HTTPS identity."""
        from .https import HttpsIdentity

        if port not in self.https_ports:
            raise UsbMuxError("HTTPS device port is not configured")
        async with self._scope():
            # Monitoring must outlive this metadata RPC, including the idle
            # period before a client opens its first HTTPS stream.
            await self._start_monitor()
            if self._https_lock is None:
                self._https_lock = Lock()
            async with self._https_lock:
                self._check()
                if self._https_identity is None:
                    identity = await to_thread.run_sync(HttpsIdentity.generate)
                    await self._device_id(self.udid)
                    self._check()
                    self._https_identity = identity
                return self._https_identity.certificate

    @asynccontextmanager
    async def open_https_forward(self, port: int):
        """Carry HTTPS to one configured device port, with no host listener."""
        from .https import https_forward

        if port not in self.https_ports:
            raise UsbMuxError("HTTPS device port is not configured")
        async with self._scope():
            identity = self._https_identity
            if identity is None:
                raise UsbMuxError("request HTTPS connection information before opening HTTPS")
            await self._start_monitor()

            @asynccontextmanager
            async def upstream():
                # TLS completes before USB opens; _scope revokes active connections.
                self._check()
                try:
                    async with self.connect_device(self.udid, port, device_id=self.device_id) as stream:
                        yield stream
                except UsbMuxResultError as error:
                    if error.number != RESULT_CONNREFUSED:
                        raise
                    # Readiness probes can precede the device listener; preserve other protocol errors.
                    raise BrokenResourceError("HTTP device service is not listening") from error

            async with https_forward(identity, upstream, timeout=self.timeout) as stream:
                yield stream

    async def _dispatch(self, stream, reader, udid, tag, request):
        self._check()
        await self._device_id(self.udid)
        message = request.get("MessageType")
        if message == "ReadPairRecord" and request.get("PairRecordID") == self.udid:
            identity = await self.prepare()
            response = {"PairRecordData": plistlib.dumps(identity.public_record)}
        elif message == "ReadBUID":
            identity = await self.prepare()
            response = {"BUID": identity.public_record["SystemBUID"]}
        else:
            return await super()._dispatch(stream, reader, udid, tag, request)
        self._check()
        await stream.send(encode_message(response, tag))
        return False

    async def _tls(self, stream, *, server=False):
        identity = await self.prepare()
        with fail_after(self.timeout):
            secured = await TLSStream.wrap(
                stream,
                server_side=server,
                standard_compatible=False,
                ssl_context=identity.downstream_context if server else identity.upstream_context,
            )
        expected = (
            (identity.client_certificate, identity.root_certificate) if server else (identity.device_certificate,)
        )
        if secured.extra(TLSAttribute.peer_certificate_binary) not in expected:
            await secured.aclose()
            raise UsbMuxError("iOS TLS peer does not match this trust generation")
        self._check()
        return secured

    async def _watch_device(self):
        """An observed detach permanently revokes mappings, even if IDs are reused."""
        assert self._monitor_ready is not None
        try:
            async with self._open() as stream:
                reader = BufferedByteReceiveStream(stream)
                _check_result(await self._request(stream, reader, {"MessageType": "Listen"}))
                while True:
                    _, event = await receive_message(reader)
                    self._check()
                    if event.get("MessageType") == "Detached" and event.get("DeviceID") == self.device_id:
                        break
                    if event.get("MessageType") == "Attached":
                        leased = _matches(event, self.udid, self.connection_type)
                        if event.get("DeviceID") == self.device_id and not leased:
                            break
                        if leased:
                            if event.get("DeviceID") != self.device_id:
                                break
                            self._monitor_ready.set()
        except (
            EndOfStream,
            IncompleteRead,
            BrokenResourceError,
            ClosedResourceError,
            OSError,
            UsbMuxError,
            TimeoutError,
        ):
            pass
        self.invalidate()
        self._monitor_ready.set()

    def _prune_services(self):
        self._check()
        now = time.monotonic()
        self._services = {key: value for key, value in self._services.items() if value.expires > now}
        return now

    def _reserve_service(self, port, tls):
        now = self._prune_services()
        if self.device_id is None:
            raise UsbMuxError("device generation has not been resolved")
        if len(self._services) >= self.max_services:
            raise UsbMuxError("too many pending iOS services")
        while (
            self._next_port in self.allowed_ports
            or self._next_port in self.https_ports
            or self._next_port == LOCKDOWN_PORT
        ):
            self._next_port += 1
        if self._next_port > 65535:
            raise UsbMuxError("iOS virtual service ports exhausted for this lease")
        virtual = self._next_port
        self._next_port += 1  # Never reuse a virtual port within one generation.
        self._services[virtual] = _Service(port, tls, self.device_id, now + self.service_ttl)
        return virtual

    async def _connect_proxy(self, stream, reader, udid, tag, request):
        self._check()
        device_id, wire_port = request.get("DeviceID"), request.get("PortNumber")
        if not _uint(device_id) or not _uint(wire_port, 65535) or not wire_port:
            await stream.send(encode_message(_result(RESULT_BADCOMMAND), tag))
            return False
        port = socket.ntohs(wire_port)
        if port == LOCKDOWN_PORT:
            async with self.connect_device(udid, port, device_id=device_id) as upstream:
                await stream.send(encode_message(_result(RESULT_OK), tag))
                await self._lockdown(stream, reader, upstream)
            return True
        service = self._services.pop(port, None)
        if service is not None:
            if service.expires <= time.monotonic() or service.device_id != device_id:
                await stream.send(encode_message(_result(RESULT_CONNREFUSED), tag))
                return False
            real_port, tls = service.port, service.tls
        elif port in self.allowed_ports:
            real_port, tls = port, False
        else:
            await stream.send(encode_message(_result(RESULT_CONNREFUSED), tag))
            return False
        async with self.connect_device(udid, real_port, device_id=device_id) as upstream:
            if tls:
                upstream = await self._tls(upstream)
            await stream.send(encode_message(_result(RESULT_OK), tag))
            downstream = _BufferedDuplexStream(stream, reader)
            if service is not None:
                downstream = await self._tls(downstream, server=True)
            await self._relay_service(downstream, upstream, service is not None)
        return True

    async def _relay_service(self, downstream, upstream, tls):
        if not tls:
            await _relay(downstream, downstream, upstream)
            return
        # TLSStream.send_eof() is unsupported; EOF must close both directions.
        async with create_task_group() as tasks:

            async def pump(source, destination):
                try:
                    async for data in source:
                        self._check()
                        await destination.send(data)
                except (EndOfStream, BrokenResourceError, ClosedResourceError, OSError):
                    pass
                finally:
                    tasks.cancel_scope.cancel()

            tasks.start_soon(pump, downstream, upstream)
            tasks.start_soon(pump, upstream, downstream)

    @staticmethod
    def _sensitive(request):
        domain, key = request.get("Domain", ""), request.get("Key", "")
        return (
            not isinstance(domain, str)
            or not isinstance(key, str)
            or key in _PAIR_FIELDS
            or "lockdown" in domain.lower()
            or "pairing" in domain.lower()
        )

    def _clean(self, value):
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items() if key not in _PAIR_FIELDS}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    async def _lockdown(self, client, client_reader, upstream):  # noqa: C901
        upstream_reader = BufferedByteReceiveStream(upstream)
        paired = False
        session_id = None
        remote_session = None
        while True:
            self._check()
            request = await _receive_plist(client_reader)
            name = request.get("Request")
            if not isinstance(name, str) or name not in _WIRE_FIELDS:
                await _send_plist(client, {"Request": name if isinstance(name, str) else "", "Error": "InvalidRequest"})
                continue
            error = None
            if any(key in request for key in _PAIR_FIELDS - set(_WIRE_FIELDS[name])):
                error = "InvalidRequest"
            if name in {"GetValue", "SetValue", "RemoveValue"} and self._sensitive(request):
                error = "GetProhibited" if name == "GetValue" else "SetProhibited"
            if name in {"SetValue", "RemoveValue", "StartService", "StopSession"} and not paired:
                error = "SessionInactive"
            identity = None
            if name == "StartSession":
                identity = await self.prepare()
                if paired or any(request.get(key) != identity.public_record[key] for key in ("HostID", "SystemBUID")):
                    error = "InvalidHostID"
            elif name == "StopSession" and request.get("SessionID") != session_id:
                error = "InvalidSessionID"
            elif name == "StartService":
                self._prune_services()
                if len(self._services) >= self.max_services or self._next_port > 65535:
                    error = "ServiceLimit"
                service = request.get("Service")
                if not isinstance(service, str) or not service or service in HANDSHAKE_ONLY_SERVICES:
                    error = "InvalidService"
                if "EscrowBag" in request:
                    identity = await self.prepare()
                    if request["EscrowBag"] != identity.public_record["EscrowBag"] or identity.escrow_bag is None:
                        error = "InvalidPairRecord"
            if error is not None:
                await _send_plist(client, {"Request": name, "Error": error})
                continue
            outgoing = {key: request[key] for key in _WIRE_FIELDS[name] if key in request}
            outgoing.update({"Request": name, "Label": "Jumpstarter"})
            if name == "StartSession":
                assert identity is not None
                outgoing.update({"HostID": identity.host_id, "SystemBUID": identity.system_buid})
            elif name == "StopSession":
                outgoing["SessionID"] = remote_session
            elif name == "StartService" and "EscrowBag" in outgoing:
                assert identity is not None
                outgoing["EscrowBag"] = identity.escrow_bag
            with fail_after(self.timeout):
                await _send_plist(upstream, outgoing)
                response = await _receive_plist(upstream_reader)
            if response.get("Request") != name:
                raise UsbMuxError("unexpected lockdown response")
            if "Error" not in response and name == "StartSession":
                if response.get("EnableSessionSSL") is not True or not isinstance(response.get("SessionID"), str):
                    raise UsbMuxError("device did not establish an authenticated TLS session")
                upstream = await self._tls(_BufferedDuplexStream(upstream, upstream_reader))
                upstream_reader = BufferedByteReceiveStream(upstream)
                session_id, remote_session = str(uuid4()), response["SessionID"]
                response["SessionID"] = session_id
                await _send_plist(client, self._clean(response))
                client = await self._tls(_BufferedDuplexStream(client, client_reader), server=True)
                client_reader = BufferedByteReceiveStream(client)
                paired = True
                continue
            if "Error" not in response and name == "StartService":
                port = response.get("Port")
                tls = response.get("EnableServiceSSL", False)
                if not _uint(port, 65535) or not port or type(tls) is not bool:
                    raise UsbMuxError("invalid lockdown service endpoint")
                response["Port"] = self._reserve_service(port, tls)
                response["EnableServiceSSL"] = True
            await _send_plist(client, self._clean(response))
            if "Error" not in response and name == "StopSession":
                # Services already advertised remain lease-owned: go-ios closes
                # lockdown before opening its independent service connection.
                with fail_after(self.timeout):
                    upstream, remainder = await upstream.unwrap()
                    upstream_reader = BufferedByteReceiveStream(upstream)
                    upstream_reader.feed_data(remainder)
                    client, remainder = await client.unwrap()
                    client_reader = BufferedByteReceiveStream(client)
                    client_reader.feed_data(remainder)
                paired, session_id, remote_session = False, None, None
            if name == "Goodbye":
                return
