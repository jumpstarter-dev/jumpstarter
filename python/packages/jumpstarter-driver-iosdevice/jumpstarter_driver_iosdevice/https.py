"""Lease-owned HTTPS over Jumpstarter streams, without exporter TCP listeners."""

import ipaddress
import os
import ssl
import tempfile
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from anyio import (
    BrokenResourceError,
    ClosedResourceError,
    EndOfStream,
    create_memory_object_stream,
    create_task_group,
    fail_after,
    move_on_after,
)
from anyio.abc import ByteStream
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.stapled import StapledObjectStream
from anyio.streams.tls import TLSStream
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CHUNK_SIZE = 65536


@dataclass(frozen=True)
class HttpsIdentity:
    """Public trust anchor and loaded server context; no retained private PEM.

    This authority is independent of the disposable usbmux identity, whose
    private key is intentionally given to tools. HTTPS keys stay on the exporter.
    An identity expires after seven days; lease reset issues a fresh identity.
    """

    certificate: str
    context: ssl.SSLContext = field(repr=False)

    @classmethod
    def generate(cls, *, certfile: Path | None = None, keyfile: Path | None = None):
        """Create an identity, optionally retaining files for an external server.

        When paths are supplied, both must be new files inside a caller-owned
        private directory. The caller removes them after its server loads them.
        No existing file is overwritten; partial writes are removed on failure.
        """
        if (certfile is None) != (keyfile is None):
            raise ValueError("certfile and keyfile must be provided together")
        if certfile is not None and Path(certfile) == Path(keyfile):
            raise ValueError("certificate and private key must have distinct paths")
        authority_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.now(UTC)
        authority_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jumpstarter lease HTTPS authority")])
        authority = (
            x509.CertificateBuilder()
            .subject_name(authority_name)
            .issuer_name(authority_name)
            .public_key(authority_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(authority_key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False
            )
            .sign(authority_key, hashes.SHA256())
        )
        server = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jumpstarter lease HTTPS")]))
            .issuer_name(authority_name)
            .public_key(server_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False
            )
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
                ),
                critical=False,
            )
            .sign(authority_key, hashes.SHA256())
        )
        pem = serialization.Encoding.PEM
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])

        def load(cert_path, key_path):
            created = []
            try:
                for path, data in (
                    (cert_path, server.public_bytes(pem) + authority.public_bytes(pem)),
                    (
                        key_path,
                        server_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
                    ),
                ):
                    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    created.append(path)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(data)
                context.load_cert_chain(cert_path, key_path)
            except BaseException:
                for path in created:
                    path.unlink(missing_ok=True)
                raise

        # OpenSSL requires files to load the identity. By default both are
        # removed immediately; an external server may instead own their lifetime.
        if certfile is None:
            with tempfile.TemporaryDirectory(prefix="js-https-") as directory:
                load(Path(directory) / "server.crt", Path(directory) / "server.key")
        else:
            load(Path(certfile), Path(keyfile))
        return cls(authority.public_bytes(pem).decode("ascii"), context)


class _MemoryByteStream(ByteStream):
    def __init__(self, stream):
        self.stream = stream
        self.reader = BufferedByteReceiveStream(stream)

    async def receive(self, max_bytes=CHUNK_SIZE):
        return await self.reader.receive(max_bytes)

    async def send(self, item):
        for offset in range(0, len(item), CHUNK_SIZE):
            await self.stream.send(item[offset : offset + CHUNK_SIZE])

    async def send_eof(self):
        await self.stream.send_stream.aclose()

    async def aclose(self):
        await self.stream.aclose()


def _stream_pair():
    a_tx, a_rx = create_memory_object_stream[bytes](16)  # ty: ignore[call-non-callable]
    b_tx, b_rx = create_memory_object_stream[bytes](16)  # ty: ignore[call-non-callable]
    return _MemoryByteStream(StapledObjectStream(a_tx, b_rx)), _MemoryByteStream(StapledObjectStream(b_tx, a_rx))


@asynccontextmanager
async def https_endpoint(identity, handler, *, timeout=10):
    """Yield encrypted bytes; run handler only after the TLS handshake succeeds.

    The caller owns the exported stream. Ending it cancels its handler, including
    an idle handshake or blocked upstream. Normal TLS closure also ends both
    directions; TLS does not support a TCP-style half-close.
    """
    client, server = _stream_pair()
    caller_error = None

    async def serve():
        secured = None
        try:
            with fail_after(timeout):
                secured = await TLSStream.wrap(
                    server, server_side=True, ssl_context=identity.context, standard_compatible=False
                )
            await handler(secured)
        except (ssl.SSLError, TimeoutError, EndOfStream, BrokenResourceError, ClosedResourceError, OSError):
            pass
        finally:
            with move_on_after(2, shield=True):
                if secured is not None:
                    await secured.aclose()
                await server.aclose()

    async with client, server, create_task_group() as tasks:
        tasks.start_soon(serve)
        try:
            yield client
        except BaseException as exc:  # noqa: BLE001 - re-raised after task-group cleanup
            caller_error = exc
        finally:
            tasks.cancel_scope.cancel()
    if caller_error is not None:
        raise caller_error


@asynccontextmanager
async def https_forward(identity, upstream_factory, *, timeout=10):
    """Forward verified HTTPS to one exporter-selected upstream byte stream."""

    async def connected(secured):
        async with upstream_factory() as upstream, create_task_group() as tasks:

            async def pump(source, destination):
                try:
                    while True:
                        data = await source.receive(CHUNK_SIZE)
                        if not data:
                            break
                        await destination.send(data)
                except (EndOfStream, BrokenResourceError, ClosedResourceError, OSError):
                    pass
                finally:
                    tasks.cancel_scope.cancel()

            tasks.start_soon(pump, secured, upstream)
            tasks.start_soon(pump, upstream, secured)

    async with https_endpoint(identity, connected, timeout=timeout) as stream:
        yield stream
