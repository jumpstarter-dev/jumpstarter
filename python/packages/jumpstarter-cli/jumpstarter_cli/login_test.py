import click
import pytest
from click.testing import CliRunner

from jumpstarter_cli.jmp import jmp
from jumpstarter_cli.login import (
    _validate_auth_config_payload,
    _validate_login_endpoint_url,
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
    with pytest.raises(click.ClickException, match="Use --insecure-login-http"):
        _validate_login_endpoint_url("http://login.example.com")


def test_validate_login_endpoint_url_allows_http_with_explicit_opt_in() -> None:
    _validate_login_endpoint_url("http://login.example.com", allow_http=True)


def test_validate_auth_config_payload_requires_grpc_endpoint() -> None:
    with pytest.raises(click.ClickException, match="missing required field 'grpcEndpoint'"):
        _validate_auth_config_payload({"namespace": "default"}, "https://login.example.com/v1/auth/config")


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


def test_login_cli_rejects_conflicting_insecure_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(
        jmp,
        [
            "login",
            "login.example.com",
            "--client-config",
            "/tmp/nonexistent-client.yaml",
            "--insecure-login-http",
            "--insecure-login-tls",
        ],
    )

    assert result.exit_code != 0
    assert "--insecure-login-http and --insecure-login-tls cannot be used together" in result.output
