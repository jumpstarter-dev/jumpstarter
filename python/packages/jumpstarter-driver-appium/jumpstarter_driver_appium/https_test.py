import ssl
import stat
from contextlib import asynccontextmanager

import anyio.lowlevel
import pytest
from anyio import EndOfStream, create_task_group, fail_after, sleep, sleep_forever
from anyio.streams.buffered import BufferedByteReceiveStream
from anyio.streams.tls import TLSAttribute, TLSStream
from cryptography import x509

from .https import HttpsIdentity, _stream_pair, https_endpoint, https_forward


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def identity():
    return HttpsIdentity.generate()


def context(identity):
    return ssl.create_default_context(cadata=identity.certificate)


async def client_tls(stream, ctx):
    return await TLSStream.wrap(stream, hostname="127.0.0.1", ssl_context=ctx, standard_compatible=False)


@pytest.mark.anyio
async def test_https_stream_verifies_and_transfers_large_response(identity):
    payload = bytes(range(256)) * 4096

    async def handler(stream):
        reader = BufferedByteReceiveStream(stream)
        assert await reader.receive_exactly(4) == b"test"
        await stream.send(payload)

    with fail_after(5):
        async with https_endpoint(identity, handler) as raw, await client_tls(raw, context(identity)) as secured:
            assert secured.extra(TLSAttribute.tls_version) in {"TLSv1.2", "TLSv1.3"}
            await secured.send(b"test")
            assert await BufferedByteReceiveStream(secured).receive_exactly(len(payload)) == payload


@pytest.mark.anyio
@pytest.mark.parametrize("trust", ["system", "other-lease"])
async def test_https_rejects_untrusted_authority(identity, trust):
    invoked = []

    async def handler(stream):
        invoked.append(True)

    ctx = ssl.create_default_context() if trust == "system" else context(HttpsIdentity.generate())
    with fail_after(5):
        async with https_endpoint(identity, handler) as raw:
            with pytest.raises(ssl.SSLCertVerificationError):
                await client_tls(raw, ctx)
            await anyio.lowlevel.checkpoint()
    assert not invoked


@pytest.mark.anyio
@pytest.mark.parametrize("payload", [b"GET /status HTTP/1.1\r\n\r\n", None])
async def test_plaintext_and_idle_handshake_never_open_upstream(identity, payload):
    invoked = []

    async def handler(stream):
        invoked.append(True)

    with fail_after(3):
        async with https_endpoint(identity, handler, timeout=0.05) as raw:
            if payload is not None:
                await raw.send(payload)
            # OpenSSL may emit an alert before closing an invalid connection.
            with pytest.raises(EndOfStream):
                while True:
                    await raw.receive()
    assert not invoked


@pytest.mark.anyio
async def test_https_forward_closes_upstream_when_client_context_ends(identity):
    entered, closed = [], []
    producer, receiver = _stream_pair()

    @asynccontextmanager
    async def upstream():
        entered.append(True)
        try:
            async with receiver:
                yield receiver
        finally:
            closed.append(True)

    with fail_after(5):
        async with producer, https_forward(identity, upstream) as raw:
            secured = await client_tls(raw, context(identity))
            await secured.send(b"request")
            assert await producer.receive() == b"request"
            await producer.send(b"response")
            assert await secured.receive() == b"response"
        assert entered and closed


@pytest.mark.anyio
async def test_caller_exception_retains_type_and_cancels_handler(identity):
    ended = []

    async def handler(stream):
        try:
            await stream.receive()
            await sleep_forever()
        finally:
            ended.append(True)

    with fail_after(5), pytest.raises(ValueError, match="caller failed"):
        async with https_endpoint(identity, handler) as raw:
            secured = await client_tls(raw, context(identity))
            await secured.send(b"start")
            await sleep(0.01)
            raise ValueError("caller failed")
    assert ended


@pytest.mark.anyio
async def test_outer_cancellation_closes_idle_handler(identity):
    ended = []

    async def handler(stream):
        try:
            await sleep_forever()
        finally:
            ended.append(True)

    async def run():
        async with https_endpoint(identity, handler) as raw:
            await client_tls(raw, context(identity))
            await sleep_forever()

    with fail_after(5):
        async with create_task_group() as group:
            group.start_soon(run)
            await sleep(0.1)
            group.cancel_scope.cancel()
    assert ended


def test_external_server_files_are_private_and_never_overwrite(tmp_path):
    certfile, keyfile = tmp_path / "server.crt", tmp_path / "server.key"
    identity = HttpsIdentity.generate(certfile=certfile, keyfile=keyfile)
    assert stat.S_IMODE(keyfile.stat().st_mode) == 0o600
    assert stat.S_IMODE(certfile.stat().st_mode) == 0o600
    assert "PRIVATE KEY" not in identity.certificate
    assert "PRIVATE KEY" not in repr(identity)
    cert = x509.load_pem_x509_certificate(identity.certificate.encode())
    assert cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    original_key = keyfile.read_bytes()
    with pytest.raises(FileExistsError):
        HttpsIdentity.generate(certfile=certfile, keyfile=keyfile)
    assert keyfile.read_bytes() == original_key
    certfile.unlink()
    with pytest.raises(FileExistsError):
        HttpsIdentity.generate(certfile=certfile, keyfile=keyfile)
    assert not certfile.exists()
    assert keyfile.read_bytes() == original_key


def test_external_paths_must_be_paired_and_distinct(tmp_path):
    with pytest.raises(ValueError, match="together"):
        HttpsIdentity.generate(certfile=tmp_path / "cert")
    with pytest.raises(ValueError, match="distinct"):
        HttpsIdentity.generate(certfile=tmp_path / "cert", keyfile=tmp_path / "cert")
