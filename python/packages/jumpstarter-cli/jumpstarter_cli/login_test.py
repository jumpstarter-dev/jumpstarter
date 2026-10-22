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
    _warn_exporter_client_only_flags,
    fetch_auth_config,
    parse_login_argument,
    relogin_client,
)

from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.user import UserConfigV1Alpha1


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
    with pytest.raises(click.ClickException, match="Use --insecure-tls"):
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
async def test_fetch_auth_config_rejects_http_without_insecure_tls():
    with pytest.raises(click.UsageError, match="--insecure-tls"):
        await fetch_auth_config("http://login.example.com", insecure_tls=False)


@pytest.mark.asyncio
async def test_fetch_auth_config_allows_explicit_http_with_insecure_tls():
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
        result = await fetch_auth_config("http://login.example.com", insecure_tls=True)

    mock_session.get.assert_called_once()
    call_url = mock_session.get.call_args[0][0]
    assert "http://login.example.com" in call_url
    assert result["grpcEndpoint"] == "grpc.example.com"


@pytest.mark.asyncio
async def test_fetch_auth_config_defaults_to_https_with_insecure_tls():
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
        result = await fetch_auth_config("login.example.com", insecure_tls=True)

    mock_session.get.assert_called_once()
    call_url = mock_session.get.call_args[0][0]
    assert call_url.startswith("https://")
    assert result["grpcEndpoint"] == "grpc.example.com"


def test_warn_exporter_client_only_flags_warns_on_allow(capsys) -> None:
    _warn_exporter_client_only_flags("exporter", "some-driver", None)
    captured = capsys.readouterr()
    assert "--allow" in captured.out
    assert "ignored" in captured.out.lower()


def test_warn_exporter_client_only_flags_warns_on_unsafe(capsys) -> None:
    _warn_exporter_client_only_flags("exporter", "", True)
    captured = capsys.readouterr()
    assert "--unsafe" in captured.out
    assert "ignored" in captured.out.lower()


def test_warn_exporter_client_only_flags_warns_on_exporter_config_kind(capsys) -> None:
    _warn_exporter_client_only_flags("exporter_config", "pkg", True)
    captured = capsys.readouterr()
    assert "--allow" in captured.out
    assert "--unsafe" in captured.out


def test_warn_exporter_client_only_flags_silent_for_client(capsys) -> None:
    _warn_exporter_client_only_flags("client", "some-driver", True)
    captured = capsys.readouterr()
    assert captured.out == ""


def test_warn_exporter_client_only_flags_silent_when_no_flags(capsys) -> None:
    _warn_exporter_client_only_flags("exporter", "", None)
    captured = capsys.readouterr()
    assert captured.out == ""


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


def test_login_uses_device_flow_when_flag_is_passed(monkeypatch, tmp_path) -> None:
    """When --device-flow is passed, device_authorization_grant is called instead of authorization_code_grant."""
    auth_config = {
        "grpcEndpoint": "grpc.example.com:443",
        "namespace": "default",
        "oidc": [{"issuer": "https://auth.example.com", "clientId": "test-client"}],
    }

    async def fake_fetch_auth_config(*args, **kwargs):
        return auth_config

    device_flow_called = False
    auth_code_called = False

    class FakeOidcConfig:
        def __init__(self, *args, **kwargs):
            pass

        async def device_authorization_grant(self):
            nonlocal device_flow_called
            device_flow_called = True
            return {"access_token": "test-token"}

        async def authorization_code_grant(self, **kwargs):
            nonlocal auth_code_called
            auth_code_called = True
            return {"access_token": "test-token"}

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)
    monkeypatch.setattr("jumpstarter_cli.login.Config", FakeOidcConfig)

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        [
            "login",
            "test-client@login.example.com",
            "--client-config",
            str(tmp_path / "nonexistent-client.yaml"),
            "--nointeractive",
            "--unsafe",
            "--device-flow",
        ],
    )

    assert result.exit_code == 0, result.output
    assert device_flow_called is True
    assert auth_code_called is False


def test_login_uses_device_flow_when_env_var_is_set(monkeypatch, tmp_path) -> None:
    """When JMP_OIDC_DEVICE_FLOW=1, device_authorization_grant is called automatically."""
    auth_config = {
        "grpcEndpoint": "grpc.example.com:443",
        "namespace": "default",
        "oidc": [{"issuer": "https://auth.example.com", "clientId": "test-client"}],
    }

    async def fake_fetch_auth_config(*args, **kwargs):
        return auth_config

    device_flow_called = False
    auth_code_called = False

    class FakeOidcConfig:
        def __init__(self, *args, **kwargs):
            pass

        async def device_authorization_grant(self):
            nonlocal device_flow_called
            device_flow_called = True
            return {"access_token": "test-token"}

        async def authorization_code_grant(self, **kwargs):
            nonlocal auth_code_called
            auth_code_called = True
            return {"access_token": "test-token"}

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)
    monkeypatch.setattr("jumpstarter_cli.login.Config", FakeOidcConfig)
    monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "1")

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        [
            "login",
            "test-client@login.example.com",
            "--client-config",
            str(tmp_path / "nonexistent-client.yaml"),
            "--nointeractive",
            "--unsafe",
        ],
    )

    assert result.exit_code == 0, result.output
    assert device_flow_called is True
    assert auth_code_called is False


def test_login_uses_auth_code_flow_without_device_flow_signals(monkeypatch, tmp_path) -> None:
    """Without --device-flow or JMP_OIDC_DEVICE_FLOW, authorization_code_grant is used (no regression)."""
    auth_config = {
        "grpcEndpoint": "grpc.example.com:443",
        "namespace": "default",
        "oidc": [{"issuer": "https://auth.example.com", "clientId": "test-client"}],
    }

    async def fake_fetch_auth_config(*args, **kwargs):
        return auth_config

    device_flow_called = False
    auth_code_called = False

    class FakeOidcConfig:
        def __init__(self, *args, **kwargs):
            pass

        async def device_authorization_grant(self):
            nonlocal device_flow_called
            device_flow_called = True
            return {"access_token": "test-token"}

        async def authorization_code_grant(self, **kwargs):
            nonlocal auth_code_called
            auth_code_called = True
            return {"access_token": "test-token"}

    monkeypatch.setattr("jumpstarter_cli.login.fetch_auth_config", fake_fetch_auth_config)
    monkeypatch.setattr("jumpstarter_cli.login.Config", FakeOidcConfig)
    monkeypatch.delenv("JMP_OIDC_DEVICE_FLOW", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        jmp,
        [
            "login",
            "test-client@login.example.com",
            "--client-config",
            str(tmp_path / "nonexistent-client.yaml"),
            "--nointeractive",
            "--unsafe",
        ],
    )

    assert result.exit_code == 0, result.output
    assert auth_code_called is True
    assert device_flow_called is False


def test_env_py_contains_jmp_oidc_device_flow_constant() -> None:
    """The JMP_OIDC_DEVICE_FLOW constant must exist in env.py."""
    from jumpstarter.config.env import JMP_OIDC_DEVICE_FLOW

    assert JMP_OIDC_DEVICE_FLOW == "JMP_OIDC_DEVICE_FLOW"


def _login_config_path(path, config_location, tmp_path, monkeypatch):
    if config_location == "relative_alias_path":
        monkeypatch.chdir(tmp_path)
        return path.relative_to(tmp_path)
    if config_location == "symlink_alias_path":
        linked_clients = tmp_path / "linked-clients"
        linked_clients.symlink_to(path.parent, target_is_directory=True)
        return linked_clients / path.name
    return path


@pytest.mark.parametrize(
    "mode", ["login", "login_without_refresh_token", "auth_refresh", "relogin", "relogin_without_refresh_token"]
)
@pytest.mark.parametrize(
    "config_location",
    ["explicit_path", "explicit_path_alias_collision", "default_path", "relative_alias_path", "symlink_alias_path"],
)
def test_authentication_preserves_client_config_path(tmp_path, monkeypatch, mode, config_location) -> None:
    clients = tmp_path / "clients"
    monkeypatch.setattr(ClientConfigV1Alpha1, "CLIENT_CONFIGS_PATH", clients)
    monkeypatch.setattr(UserConfigV1Alpha1, "BASE_CONFIG_PATH", tmp_path)
    monkeypatch.setattr(UserConfigV1Alpha1, "USER_CONFIG_PATH", tmp_path / "config.yaml")
    alias_path = config_location in {"default_path", "relative_alias_path", "symlink_alias_path"}
    path = (clients if alias_path else tmp_path) / "client.yaml"
    config = ClientConfigV1Alpha1(
        alias="client",
        metadata=ObjectMeta(namespace="test", name="test"),
        endpoint="localhost:1",
        token="old-token",
        refresh_token=None if mode.endswith("without_refresh_token") else "old-refresh-token",
    )
    previous = config.model_copy(update={"alias": "previous", "token": "previous-token"})
    ClientConfigV1Alpha1.save(previous)
    UserConfigV1Alpha1.load_or_create().use_client("previous")
    user_config_before = UserConfigV1Alpha1.USER_CONFIG_PATH.read_bytes()
    if config_location == "explicit_path_alias_collision":
        ClientConfigV1Alpha1.save(config.model_copy(update={"token": "unrelated-token"}))
        unrelated_before = (clients / "client.yaml").read_bytes()
    ClientConfigV1Alpha1.save(config, path)
    path = _login_config_path(path, config_location, tmp_path, monkeypatch)
    tokens = {"access_token": "new-token", "refresh_token": "new-refresh-token"}
    refresh = AsyncMock(return_value=tokens)
    authorize = AsyncMock(return_value=tokens)
    monkeypatch.setattr("jumpstarter_cli.login.decode_jwt_issuer", lambda token: "https://issuer.invalid")
    monkeypatch.setattr("jumpstarter_cli.auth.decode_jwt_issuer", lambda token: "https://issuer.invalid")
    monkeypatch.setattr("jumpstarter_cli.login.Config.refresh_token_grant", refresh)
    monkeypatch.setattr("jumpstarter_cli.login.Config.authorization_code_grant", authorize)
    monkeypatch.setattr("jumpstarter_cli.login.should_use_device_flow", lambda *args, **kwargs: False)

    if mode in {"login", "login_without_refresh_token"}:
        result = CliRunner().invoke(jmp, ["login", "--nointeractive", "--client-config", str(path)])
        assert result.exit_code == 0, result.output
        if mode == "login":
            assert "Refreshed access token" in result.output
    elif mode == "auth_refresh":
        result = CliRunner().invoke(jmp, ["auth", "refresh", "--client-config", str(path)])
        assert result.exit_code == 0, result.output
        assert "Access token refreshed" in result.output
    else:
        relogin_client(ClientConfigV1Alpha1.from_file(path))

    if mode.endswith("without_refresh_token"):
        refresh.assert_not_awaited()
        if mode == "login_without_refresh_token":
            authorize.assert_awaited_once_with(callback_port=None)
        else:
            authorize.assert_awaited_once_with()
    else:
        refresh.assert_awaited_once_with("old-refresh-token")
        authorize.assert_not_awaited()
    reloaded = ClientConfigV1Alpha1.from_file(path)
    assert reloaded.token == "new-token"
    assert reloaded.refresh_token == "new-refresh-token"
    if config_location == "explicit_path":
        assert not (clients / "client.yaml").exists()
    elif config_location == "explicit_path_alias_collision":
        assert (clients / "client.yaml").read_bytes() == unrelated_before
    if mode == "login_without_refresh_token" and alias_path:
        current_client = UserConfigV1Alpha1.load().config.current_client
        assert current_client is not None
        assert current_client.path is not None
        assert current_client.path.resolve() == path.resolve()
    else:
        assert UserConfigV1Alpha1.USER_CONFIG_PATH.read_bytes() == user_config_before
