import contextlib
import logging
import socket
import ssl
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import anyio
import grpc
import pytest
from anyio.abc import SocketAttribute
from anyio.streams.tls import TLSListener
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from jumpstarter.common.exceptions import CertificateDiscoveryError, ConnectionError
from jumpstarter.common.grpc import (
    _override_default_grpc_options,
    _ssl_channel_credentials_insecure,
    _try_connect_and_extract_cert,
    is_controller_unavailable,
    translate_grpc_exceptions,
)


def test_default_options_preserve_existing_defaults():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.lb_policy_name"] == "round_robin"
    assert options["grpc.keepalive_time_ms"] == 20000


def test_user_options_override_defaults():
    user_options: dict[str, str | int] = {"grpc.keepalive_time_ms": 50000}
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.keepalive_time_ms"] == 50000


def test_translate_grpc_failed_precondition_preserves_details():
    with pytest.raises(ConnectionError, match="requested exporter is disabled"), translate_grpc_exceptions():
        raise grpc.aio.AioRpcError(
            code=grpc.StatusCode.FAILED_PRECONDITION,
            initial_metadata=None,  # type: ignore[arg-type]
            trailing_metadata=None,  # type: ignore[arg-type]
            details="requested exporter is disabled",
        )


def _addr_info(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


def _patch_resolver(getaddrinfo):
    return patch("jumpstarter.common.grpc.getaddrinfo", getaddrinfo)


@pytest.fixture
async def tls_server(tmp_path):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    (tmp_path / "server.crt").write_bytes(cert_pem)
    (tmp_path / "server.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")

    async def hold_until_client_leaves(stream):
        async with stream:
            with contextlib.suppress(anyio.EndOfStream, anyio.BrokenResourceError):
                await stream.receive()

    async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
        tls_listener = TLSListener(listener, context, standard_compatible=False)
        async with anyio.create_task_group() as tg:
            tg.start_soon(tls_listener.serve, hold_until_client_leaves)
            yield listener.extra(SocketAttribute.local_port), cert_pem
            tg.cancel_scope.cancel()


@pytest.mark.anyio
async def test_extracts_the_unverified_certificate_chain(tls_server):
    port, cert_pem = tls_server
    client_context = ssl.create_default_context()
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE

    chain = await _try_connect_and_extract_cert("127.0.0.1", port, client_context, "localhost", timeout=5)

    assert chain == cert_pem


class TestSslChannelCredentialsInsecure:
    """Resolution and connection are timed separately, so the error a user
    sees points at the step that actually stalled."""

    @pytest.mark.anyio
    async def test_returns_credentials_from_first_reachable_ip(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def connect(ip_address, *_args, **_kwargs):
            if ip_address == "192.0.2.1":
                raise OSError("connection refused")
            return b"-----BEGIN CERTIFICATE-----\n"

        with _patch_resolver(getaddrinfo), patch(
            "jumpstarter.common.grpc._try_connect_and_extract_cert",
            connect,
        ):
            credentials = await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

        assert credentials is not None

    @pytest.mark.anyio
    async def test_a_hanging_ip_does_not_delay_a_reachable_one(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def connect(ip_address, *_args, **_kwargs):
            if ip_address == "192.0.2.1":
                await anyio.sleep_forever()
            return b"-----BEGIN CERTIFICATE-----\n"

        with (
            _patch_resolver(getaddrinfo),
            patch("jumpstarter.common.grpc._try_connect_and_extract_cert", connect),
            anyio.fail_after(1),
        ):
            credentials = await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

        assert credentials is not None

    @pytest.mark.anyio
    async def test_resolution_failure_names_the_host(self):
        async def getaddrinfo(*_args, **_kwargs):
            raise socket.gaierror("Name or service not known")

        with (
            _patch_resolver(getaddrinfo),
            pytest.raises(CertificateDiscoveryError, match="Failed resolving example.com"),
        ):
            await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

    @pytest.mark.anyio
    async def test_slow_resolver_is_reported_as_a_resolution_timeout(self):
        async def getaddrinfo(*_args, **_kwargs):
            await anyio.sleep_forever()

        with (
            _patch_resolver(getaddrinfo),
            pytest.raises(CertificateDiscoveryError, match="Timeout resolving example.com"),
        ):
            await _ssl_channel_credentials_insecure("example.com:443", timeout=0.05)

    @pytest.mark.anyio
    async def test_connect_timeout_reports_the_resolved_ips(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1")

        async def never_connects(*_args, **_kwargs):
            await anyio.sleep_forever()

        with (
            _patch_resolver(getaddrinfo),
            patch("jumpstarter.common.grpc._try_connect_and_extract_cert", never_connects),
            pytest.raises(
                CertificateDiscoveryError,
                match=r"Timeout connecting to example\.com:443.*resolved to 192\.0\.2\.1",
            ),
        ):
            await _ssl_channel_credentials_insecure("example.com:443", timeout=0.05)

    @pytest.mark.anyio
    async def test_all_ips_failing_lists_the_errors(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def refused(*_args, **_kwargs):
            raise OSError("connection refused")

        with (
            _patch_resolver(getaddrinfo),
            patch("jumpstarter.common.grpc._try_connect_and_extract_cert", refused),
            pytest.raises(CertificateDiscoveryError, match="all IPs exhausted"),
        ):
            await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

    @pytest.mark.anyio
    async def test_exporter_retry_can_suppress_per_ip_warnings(self, caplog):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def refused(*_args, **_kwargs):
            raise OSError("connection refused")

        caplog.set_level(logging.DEBUG, logger="jumpstarter.common.grpc")
        with (
            _patch_resolver(getaddrinfo),
            patch("jumpstarter.common.grpc._try_connect_and_extract_cert", refused),
            pytest.raises(CertificateDiscoveryError, match="all IPs exhausted"),
        ):
            await _ssl_channel_credentials_insecure(
                "example.com:443", timeout=5, log_connection_failures=False
            )

        failures = [record for record in caplog.records if "Failed to connect to" in record.message]
        assert len(failures) == 2
        assert all(record.levelno == logging.DEBUG for record in failures)


def test_translated_permission_failure_is_not_retried_as_controller_outage():
    with pytest.raises(ConnectionError) as caught, translate_grpc_exceptions():
        raise grpc.aio.AioRpcError(
            code=grpc.StatusCode.PERMISSION_DENIED,
            initial_metadata=None,  # type: ignore[arg-type]
            trailing_metadata=None,  # type: ignore[arg-type]
            details="permission denied",
        )
    assert not is_controller_unavailable(caught.value)
