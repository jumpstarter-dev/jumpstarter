import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from jumpstarter_cli_common.oidc import (
    DEVICE_FLOW_GRANT_TYPE,
    DEVICE_FLOW_POLL_INTERVAL,
    Config,
    _get_ssl_context,
    should_use_device_flow,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestConfigInsecureTls:
    def test_insecure_tls_defaults_to_false(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        assert config.insecure_tls is False

    def test_client_disables_ssl_verification_when_insecure_tls_is_set(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test", insecure_tls=True)
        client = config.client()
        assert client.verify is False

    def test_client_enables_ssl_verification_when_insecure_tls_is_not_set(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        client = config.client()
        assert client.verify is not False


class TestGetSslContext:
    def test_returns_ssl_context(self) -> None:
        ctx = _get_ssl_context()
        assert isinstance(ctx, ssl.SSLContext)


class TestShouldUseDeviceFlow:
    def test_explicit_true_flag(self) -> None:
        assert should_use_device_flow(True) is True

    def test_explicit_false_flag(self) -> None:
        assert should_use_device_flow(False) is False

    def test_env_var_jmp_oidc_device_flow(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "1")
        assert should_use_device_flow(None) is True

    def test_env_var_vscode_injection(self, monkeypatch) -> None:
        monkeypatch.setenv("VSCODE_INJECTION", "1")
        assert should_use_device_flow(None) is True

    def test_no_signals_returns_false(self, monkeypatch) -> None:
        monkeypatch.delenv("JMP_OIDC_DEVICE_FLOW", raising=False)
        monkeypatch.delenv("VSCODE_INJECTION", raising=False)
        assert should_use_device_flow(None) is False

    def test_explicit_flag_overrides_env(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "1")
        assert should_use_device_flow(False) is False

    def test_env_var_non_one_value_ignored(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "true")
        monkeypatch.delenv("VSCODE_INJECTION", raising=False)
        assert should_use_device_flow(None) is False


class TestDeviceCodeGrant:
    @pytest.mark.anyio
    async def test_raises_when_endpoint_missing(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        with patch.object(config, "configuration", return_value={"token_endpoint": "https://auth.example.com/token"}):
            with pytest.raises(click.ClickException, match="does not support Device Authorization Grant"):
                await config.device_code_grant()

    @pytest.mark.anyio
    async def test_raises_on_non_200_device_endpoint(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        oidc_config = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }

        mock_resp = AsyncMock()
        mock_resp.status = 400
        mock_resp.text = AsyncMock(return_value="Bad Request")
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(config, "configuration", return_value=oidc_config),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=mock_session),
        ):
            with pytest.raises(click.ClickException, match="Device authorization request failed"):
                await config.device_code_grant()

    @pytest.mark.anyio
    async def test_successful_device_flow(self, capsys) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        oidc_config = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }
        device_response = {
            "device_code": "test-device-code",
            "user_code": "ABCD-1234",
            "verification_uri": "https://auth.example.com/device",
            "expires_in": 600,
            "interval": 0,
        }
        token_response = {"access_token": "test-access-token", "token_type": "Bearer"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=device_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.fetch_token = MagicMock(return_value=token_response)

        with (
            patch.object(config, "configuration", return_value=oidc_config),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch.object(config, "client", return_value=mock_client),
            patch("jumpstarter_cli_common.oidc.anyio.sleep", new_callable=AsyncMock),
        ):
            result = await config.device_code_grant()

        assert result == token_response
        captured = capsys.readouterr()
        assert "ABCD-1234" in captured.out
        assert "https://auth.example.com/device" in captured.out

    @pytest.mark.anyio
    async def test_prefers_verification_uri_complete(self, capsys) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        oidc_config = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }
        device_response = {
            "device_code": "test-device-code",
            "user_code": "ABCD-1234",
            "verification_uri": "https://auth.example.com/device",
            "verification_uri_complete": "https://auth.example.com/device?user_code=ABCD-1234",
            "expires_in": 600,
            "interval": 0,
        }
        token_response = {"access_token": "test-access-token", "token_type": "Bearer"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=device_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.fetch_token = MagicMock(return_value=token_response)

        with (
            patch.object(config, "configuration", return_value=oidc_config),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch.object(config, "client", return_value=mock_client),
            patch("jumpstarter_cli_common.oidc.anyio.sleep", new_callable=AsyncMock),
        ):
            await config.device_code_grant()

        captured = capsys.readouterr()
        assert "https://auth.example.com/device?user_code=ABCD-1234" in captured.out

    @pytest.mark.anyio
    async def test_polls_on_authorization_pending(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        oidc_config = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }
        device_response = {
            "device_code": "test-device-code",
            "user_code": "ABCD-1234",
            "verification_uri": "https://auth.example.com/device",
            "expires_in": 600,
            "interval": 0,
        }
        token_response = {"access_token": "test-access-token", "token_type": "Bearer"}

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=device_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def fetch_token_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("authorization_pending")
            return token_response

        mock_client = MagicMock()
        mock_client.fetch_token = MagicMock(side_effect=fetch_token_side_effect)

        with (
            patch.object(config, "configuration", return_value=oidc_config),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch.object(config, "client", return_value=mock_client),
            patch("jumpstarter_cli_common.oidc.anyio.sleep", new_callable=AsyncMock),
        ):
            result = await config.device_code_grant()

        assert result == token_response
        assert call_count == 3

    @pytest.mark.anyio
    async def test_raises_on_access_denied(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        oidc_config = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }
        device_response = {
            "device_code": "test-device-code",
            "user_code": "ABCD-1234",
            "verification_uri": "https://auth.example.com/device",
            "expires_in": 600,
            "interval": 0,
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=device_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_client = MagicMock()
        mock_client.fetch_token = MagicMock(side_effect=Exception("access_denied"))

        with (
            patch.object(config, "configuration", return_value=oidc_config),
            patch("aiohttp.TCPConnector"),
            patch("aiohttp.ClientSession", return_value=mock_session),
            patch.object(config, "client", return_value=mock_client),
            patch("jumpstarter_cli_common.oidc.anyio.sleep", new_callable=AsyncMock),
        ):
            with pytest.raises(click.ClickException, match="Authorization was denied"):
                await config.device_code_grant()


class TestConstants:
    def test_device_flow_poll_interval(self) -> None:
        assert DEVICE_FLOW_POLL_INTERVAL == 5

    def test_device_flow_grant_type(self) -> None:
        assert DEVICE_FLOW_GRANT_TYPE == "urn:ietf:params:oauth:grant-type:device_code"
