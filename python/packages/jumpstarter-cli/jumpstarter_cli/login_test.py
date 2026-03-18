import asyncio
import json
import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from jumpstarter_cli.jmp import jmp
from jumpstarter_cli.login import (
    _validate_auth_config_payload,
    _validate_login_endpoint_url,
    fetch_auth_config,
    parse_login_argument,
)


def test_parse_login_argument_supports_client_and_endpoint() -> None:
    username, endpoint = parse_login_argument("my-client@login.example.com")
    assert username == "my-client"
    assert endpoint == "login.example.com"


def test_parse_login_argument_rejects_empty_target() -> None:
    with pytest.raises(click.ClickException, match="Login target cannot be empty"):
        parse_login_argument("   ")


def test_parse_login_argument_rejects_empty_client_name() -> None:
    with pytest.raises(click.ClickException, match="Client name before '@' cannot be empty"):
        parse_login_argument("@login.example.com")


def test_parse_login_argument_rejects_whitespace_only_endpoint() -> None:
    with pytest.raises(click.ClickException, match="Login endpoint after '@' cannot be empty"):
        parse_login_argument("my-client@   ")


def test_parse_login_argument_trims_client_and_endpoint() -> None:
    username, endpoint = parse_login_argument("  my-client  @  login.example.com  ")
    assert username == "my-client"
    assert endpoint == "login.example.com"


def test_validate_login_endpoint_url_rejects_missing_host() -> None:
    with pytest.raises(click.ClickException, match="missing host"):
        _validate_login_endpoint_url("https:///v1/auth/config")


def test_validate_login_endpoint_url_rejects_unsupported_scheme() -> None:
    with pytest.raises(click.ClickException, match="unsupported URL scheme"):
        _validate_login_endpoint_url("ftp://login.example.com")


def test_validate_login_endpoint_url_rejects_http_without_explicit_opt_in() -> None:
    with pytest.raises(click.ClickException, match="Use --insecure"):
        _validate_login_endpoint_url("http://login.example.com")


def test_validate_login_endpoint_url_allows_http_with_explicit_opt_in() -> None:
    _validate_login_endpoint_url("http://login.example.com", allow_http=True)


def test_validate_auth_config_payload_requires_grpc_endpoint() -> None:
    with pytest.raises(click.ClickException, match="missing required field 'grpcEndpoint'"):
        _validate_auth_config_payload({"namespace": "default"}, "https://login.example.com/v1/auth/config")


def test_fetch_auth_config_maps_timeout_to_click_exception(monkeypatch) -> None:
    class FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            raise TimeoutError("network timeout")

    monkeypatch.setattr("jumpstarter_cli.login.aiohttp.ClientSession", FakeClientSession)

    with pytest.raises(click.ClickException, match="Timed out while connecting"):
        asyncio.run(fetch_auth_config("login.example.com"))


def test_fetch_auth_config_maps_json_decode_error(monkeypatch) -> None:
    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def json(self):
            raise json.JSONDecodeError("Expecting value", "x", 0)

    class FakeClientSession:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setattr("jumpstarter_cli.login.aiohttp.ClientSession", FakeClientSession)

    with pytest.raises(click.ClickException, match="Invalid JSON response received"):
        asyncio.run(fetch_auth_config("login.example.com"))


def test_login_cli_shows_timeout_message(monkeypatch) -> None:
    async def fake_fetch_auth_config(*args, **kwargs):
        raise click.ClickException("Timed out while connecting to login.example.com.")

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        ["login", "login.example.com", "--client-config", "/tmp/nonexistent-client.yaml"],
    )

    assert result.exit_code != 0
    assert "Timed out while connecting to login.example.com." in result.output


def test_login_cli_shows_certificate_message(monkeypatch) -> None:
    async def fake_fetch_auth_config(*args, **kwargs):
        raise click.ClickException("TLS certificate verification failed while connecting to login.example.com.")

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        ["login", "login.example.com", "--client-config", "/tmp/nonexistent-client.yaml"],
    )

    assert result.exit_code != 0
    assert "TLS certificate verification failed" in result.output


@pytest.mark.asyncio
async def test_fetch_auth_config_rejects_http_without_insecure():
    with pytest.raises(click.UsageError, match="--insecure"):
        await fetch_auth_config("http://login.example.com", insecure=False)


@pytest.mark.asyncio
async def test_fetch_auth_config_allows_http_with_insecure():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.json = AsyncMock(return_value={"grpcEndpoint": "grpc.example.com"})

    mock_get_cm = MagicMock()
    mock_get_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_get_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=mock_get_cm)

    mock_client_cm = MagicMock()
    mock_client_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_client_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_client_cm):
        result = await fetch_auth_config("http://login.example.com", insecure=True)

    mock_session.get.assert_called_once()
    call_url = mock_session.get.call_args[0][0]
    assert "http://login.example.com" in call_url
    assert result["grpcEndpoint"] == "grpc.example.com"


def test_login_maps_ssl_cert_error_during_oidc_to_friendly_message(monkeypatch) -> None:
    auth_config = {
        "grpcEndpoint": "grpc.example.com:443",
        "namespace": "default",
        "oidc": [{"issuer": "https://auth.example.com", "clientId": "test-client"}],
    }

    async def fake_fetch_auth_config(*args, **kwargs):
        return auth_config

    class FakeOidcConfig:
        def __init__(self, *args, **kwargs):
            pass

        async def authorization_code_grant(self, **kwargs):
            raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)
    monkeypatch.setattr("jumpstarter_cli.login.Config", FakeOidcConfig)

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        [
            "login",
            "test-client@login.example.com",
            "--client-config",
            "/tmp/nonexistent-client.yaml",
            "--nointeractive",
            "--unsafe",
        ],
    )

    assert result.exit_code != 0
    assert "TLS certificate validation failed" in result.output
    assert "Traceback" not in result.output
