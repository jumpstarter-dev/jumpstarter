"""Tests for exporter telemetry setup: _severity_to_level and _setup_telemetry."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from jumpstarter.exporter.exporter import Exporter, _severity_to_level

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "severity,expected",
    [
        ("debug", logging.DEBUG),
        ("DEBUG", logging.DEBUG),
        ("info", logging.INFO),
        ("INFO", logging.INFO),
        ("warning", logging.WARNING),
        ("WARNING", logging.WARNING),
        ("error", logging.ERROR),
        ("critical", logging.CRITICAL),
        ("unknown", logging.INFO),
        ("", logging.INFO),
    ],
)
def test_severity_to_level(severity, expected):
    assert _severity_to_level(severity) == expected


def make_bare_exporter():
    """Minimal Exporter instance for testing _setup_telemetry in isolation."""
    exp = Exporter.__new__(Exporter)
    exp._telemetry_handler = None
    exp._telemetry_channel = None

    tls = MagicMock()
    tls.insecure = False
    exp.tls = tls
    return exp


def make_endpoint(endpoint="telemetry:19093", certificate="", min_severity="info"):
    ep = MagicMock()
    ep.endpoint = endpoint
    ep.certificate = certificate
    ep.min_severity = min_severity
    return ep


def make_controller_stub_ctx(response):
    """Return a context manager that yields a mock controller stub."""
    from contextlib import asynccontextmanager

    stub = AsyncMock()
    stub.GetServiceEndpoints = AsyncMock(return_value=response)

    @asynccontextmanager
    async def _ctx():
        yield stub

    return _ctx


async def test_setup_telemetry_idempotent():
    """Calling _setup_telemetry twice does nothing on the second call."""
    exp = make_bare_exporter()
    exp._telemetry_handler = MagicMock()  # already set

    with patch.object(type(exp), "_controller_stub") as mock_stub:
        await exp._setup_telemetry()
        mock_stub.assert_not_called()


async def test_setup_telemetry_rpc_error_is_silently_ignored():
    """An AioRpcError (e.g. UNIMPLEMENTED on old controller) is swallowed."""
    exp = make_bare_exporter()

    err = grpc.aio.AioRpcError(
        code=grpc.StatusCode.UNIMPLEMENTED,
        initial_metadata=None,
        trailing_metadata=None,
    )

    async def raise_rpc(*_a, **_kw):
        raise err

    stub = AsyncMock()
    stub.GetServiceEndpoints = raise_rpc

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _ctx():
        yield stub

    with patch.object(exp, "_controller_stub", return_value=_ctx()):
        await exp._setup_telemetry()

    assert exp._telemetry_handler is None


async def test_setup_telemetry_no_endpoints_returns_early():
    """Empty telemetry_endpoints means no handler is attached."""
    exp = make_bare_exporter()

    resp = MagicMock()
    resp.telemetry_endpoints = []

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        await exp._setup_telemetry()

    assert exp._telemetry_handler is None


async def test_setup_telemetry_insecure_uses_insecure_channel():
    """When grpc_insecure is true, an insecure channel is opened."""
    exp = make_bare_exporter()
    exp.tls.insecure = True

    ep = make_endpoint()
    resp = MagicMock()
    resp.telemetry_endpoints = [ep]

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        with patch("jumpstarter.exporter.exporter.grpc.aio.insecure_channel") as mock_insecure:
            mock_insecure.return_value = MagicMock()
            await exp._setup_telemetry()

    mock_insecure.assert_called_once_with(ep.endpoint)
    assert exp._telemetry_handler is not None


async def test_setup_telemetry_env_var_insecure(monkeypatch):
    """JMP_GRPC_INSECURE=1 opens a plaintext channel."""
    exp = make_bare_exporter()
    monkeypatch.setenv("JMP_GRPC_INSECURE", "1")

    ep = make_endpoint()
    resp = MagicMock()
    resp.telemetry_endpoints = [ep]

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        with patch("jumpstarter.exporter.exporter.grpc.aio.insecure_channel") as mock_insecure:
            mock_insecure.return_value = MagicMock()
            await exp._setup_telemetry()

    mock_insecure.assert_called_once()


async def test_setup_telemetry_certificate_uses_secure_channel_with_cert():
    """When ep.certificate is set the controller-provided CA is used."""
    exp = make_bare_exporter()

    ep = make_endpoint(certificate="---PEM---")
    resp = MagicMock()
    resp.telemetry_endpoints = [ep]

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        with patch("jumpstarter.exporter.exporter.grpc.aio.secure_channel") as mock_secure:
            with patch("jumpstarter.exporter.exporter.grpc.ssl_channel_credentials") as mock_creds:
                mock_secure.return_value = MagicMock()
                await exp._setup_telemetry()

    mock_secure.assert_called_once()
    mock_creds.assert_called_once_with(root_certificates=b"---PEM---")


async def test_setup_telemetry_no_cert_uses_system_ca():
    """Without a certificate and without insecure mode, the system CA pool is used."""
    exp = make_bare_exporter()

    ep = make_endpoint(certificate="")
    resp = MagicMock()
    resp.telemetry_endpoints = [ep]

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        with patch("jumpstarter.exporter.exporter.grpc.aio.secure_channel") as mock_secure:
            with patch("jumpstarter.exporter.exporter.grpc.ssl_channel_credentials") as mock_creds:
                mock_secure.return_value = MagicMock()
                await exp._setup_telemetry()

    mock_secure.assert_called_once()
    mock_creds.assert_called_once_with()


async def test_setup_telemetry_applies_min_severity():
    """The handler level is set from ep.min_severity."""
    exp = make_bare_exporter()
    exp.tls.insecure = True

    ep = make_endpoint(min_severity="warning")
    resp = MagicMock()
    resp.telemetry_endpoints = [ep]

    with patch.object(exp, "_controller_stub", return_value=make_controller_stub_ctx(resp)()):
        with patch("jumpstarter.exporter.exporter.grpc.aio.insecure_channel") as mock_insecure:
            mock_insecure.return_value = MagicMock()
            await exp._setup_telemetry()

    assert exp._telemetry_handler is not None
    assert exp._telemetry_handler.level == logging.WARNING
