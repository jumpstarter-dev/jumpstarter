import ssl
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest

from jumpstarter_cli_common.oidc import Config, _get_ssl_context, should_use_device_flow


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
    def test_returns_true_when_flag_is_set(self) -> None:
        assert should_use_device_flow(device_flow_flag=True) is True

    def test_returns_true_when_env_var_is_1(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "1")
        assert should_use_device_flow(device_flow_flag=False) is True

    def test_returns_false_when_env_var_is_not_1(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "0")
        assert should_use_device_flow(device_flow_flag=False) is False

    def test_returns_false_when_env_var_unset(self, monkeypatch) -> None:
        monkeypatch.delenv("JMP_OIDC_DEVICE_FLOW", raising=False)
        assert should_use_device_flow(device_flow_flag=False) is False

    def test_flag_takes_priority_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv("JMP_OIDC_DEVICE_FLOW", "0")
        assert should_use_device_flow(device_flow_flag=True) is True


def _make_async_cm(response):
    """Create an async context manager wrapper for a MagicMock response."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=response)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_authorization_code_prompt_uses_stderr(monkeypatch, capsys):
    config = Config(issuer="https://auth.example.com", client_id="test")
    discovery = {"authorization_endpoint": "https://auth.example.com/auth", "token_endpoint": "https://auth.example.com/token"}
    monkeypatch.setattr(config, "configuration", AsyncMock(return_value=discovery))
    client = MagicMock()
    client.create_authorization_url.return_value = ("https://auth.example.com/auth?state=test", "test")
    client.fetch_token.return_value = {"access_token": "new-token"}
    monkeypatch.setattr(config, "client", lambda **kwargs: client)
    receiver = AsyncMock()
    receiver.receive.return_value = "http://localhost:12345/callback?code=test"
    monkeypatch.setattr("jumpstarter_cli_common.oidc.create_memory_object_stream", lambda: (MagicMock(), receiver))
    monkeypatch.setattr("jumpstarter_cli_common.oidc.web.AppRunner", lambda *args, **kwargs: AsyncMock())
    site = MagicMock(start=AsyncMock(), stop=AsyncMock())
    site._server.sockets[0].getsockname.return_value = ("localhost", 12345)
    monkeypatch.setattr("jumpstarter_cli_common.oidc.web.TCPSite", lambda *args: site)

    result = await config.authorization_code_grant()

    assert result == {"access_token": "new-token"}
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Please open the URL in browser:" in captured.err
    assert "https://auth.example.com/auth?state=test" in captured.err


class TestDeviceAuthorizationGrant:
    @pytest.mark.asyncio
    async def test_raises_when_no_device_endpoint_in_discovery(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        with patch.object(config, "configuration", new_callable=AsyncMock) as mock_config:
            mock_config.return_value = {
                "token_endpoint": "https://auth.example.com/token",
                # No device_authorization_endpoint
            }
            with pytest.raises(click.ClickException, match="does not support Device Authorization Grant"):
                await config.device_authorization_grant()

    @pytest.mark.asyncio
    async def test_error_message_mentions_device_authorization_endpoint(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")
        with patch.object(config, "configuration", new_callable=AsyncMock) as mock_config:
            mock_config.return_value = {
                "token_endpoint": "https://auth.example.com/token",
            }
            with pytest.raises(click.ClickException, match="device_authorization_endpoint"):
                await config.device_authorization_grant()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("complete_uri", [True, False])
    async def test_successful_device_flow(self, capsys, complete_uri) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")

        discovery = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }

        device_response_data = {
            "device_code": "test-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.com/device",
            "verification_uri_complete": "https://auth.example.com/device?user_code=ABCD-EFGH",
            "interval": 0.01,  # Speed up test
            "expires_in": 300,
        }
        if not complete_uri:
            device_response_data.pop("verification_uri_complete")

        token_data = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "token_type": "Bearer",
        }

        # Track poll count: first returns authorization_pending, second returns success
        poll_count = 0

        def mock_post(url, data=None, **kwargs):
            nonlocal poll_count
            response = MagicMock()

            if "device" in str(url) and "grant_type" not in (data or {}):
                # Device authorization endpoint
                response.status = 200
                response.json = AsyncMock(return_value=device_response_data)
                response.text = AsyncMock(return_value="")
            else:
                # Token endpoint
                poll_count += 1  # ty: ignore[unresolved-reference]
                if poll_count == 1:
                    response.status = 400
                    response.json = AsyncMock(return_value={"error": "authorization_pending"})
                else:
                    response.status = 200
                    response.json = AsyncMock(return_value=token_data)

            return _make_async_cm(response)

        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=discovery),
            patch("jumpstarter_cli_common.oidc.aiohttp.ClientSession", return_value=mock_session),
        ):
            result = await config.device_authorization_grant()

        assert result["access_token"] == "test-access-token"
        assert result["refresh_token"] == "test-refresh-token"
        assert poll_count == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        verification_uri = device_response_data.get(
            "verification_uri_complete", device_response_data["verification_uri"]
        )
        assert verification_uri in captured.err
        assert "ABCD-EFGH" in captured.err
        assert "Waiting for authentication..." in captured.err

    @pytest.mark.asyncio
    async def test_handles_slow_down_response(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")

        discovery = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }

        device_response_data = {
            "device_code": "test-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.com/device",
            "interval": 0.01,
            "expires_in": 300,
        }

        token_data = {"access_token": "test-access-token", "token_type": "Bearer"}

        poll_count = 0

        def mock_post(url, data=None, **kwargs):
            nonlocal poll_count
            response = MagicMock()

            if "device" in str(url) and "grant_type" not in (data or {}):
                response.status = 200
                response.json = AsyncMock(return_value=device_response_data)
                response.text = AsyncMock(return_value="")
            else:
                poll_count += 1  # ty: ignore[unresolved-reference]
                if poll_count == 1:
                    response.status = 400
                    response.json = AsyncMock(return_value={"error": "slow_down"})
                else:
                    response.status = 200
                    response.json = AsyncMock(return_value=token_data)

            return _make_async_cm(response)

        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=discovery),
            patch("jumpstarter_cli_common.oidc.aiohttp.ClientSession", return_value=mock_session),
        ):
            result = await config.device_authorization_grant()

        assert result["access_token"] == "test-access-token"

    @pytest.mark.asyncio
    async def test_raises_on_access_denied(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")

        discovery = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }

        device_response_data = {
            "device_code": "test-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.com/device",
            "interval": 0.01,
            "expires_in": 300,
        }

        def mock_post(url, data=None, **kwargs):
            response = MagicMock()

            if "device" in str(url) and "grant_type" not in (data or {}):
                response.status = 200
                response.json = AsyncMock(return_value=device_response_data)
                response.text = AsyncMock(return_value="")
            else:
                response.status = 400
                response.json = AsyncMock(return_value={"error": "access_denied"})

            return _make_async_cm(response)

        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=discovery),
            patch("jumpstarter_cli_common.oidc.aiohttp.ClientSession", return_value=mock_session),
            pytest.raises(click.ClickException, match="denied by the user"),
        ):
            await config.device_authorization_grant()

    @pytest.mark.asyncio
    async def test_raises_on_expired_token(self) -> None:
        config = Config(issuer="https://auth.example.com", client_id="test")

        discovery = {
            "token_endpoint": "https://auth.example.com/token",
            "device_authorization_endpoint": "https://auth.example.com/device",
        }

        device_response_data = {
            "device_code": "test-device-code",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://auth.example.com/device",
            "interval": 0.01,
            "expires_in": 300,
        }

        def mock_post(url, data=None, **kwargs):
            response = MagicMock()

            if "device" in str(url) and "grant_type" not in (data or {}):
                response.status = 200
                response.json = AsyncMock(return_value=device_response_data)
                response.text = AsyncMock(return_value="")
            else:
                response.status = 400
                response.json = AsyncMock(return_value={"error": "expired_token"})

            return _make_async_cm(response)

        mock_session = MagicMock()
        mock_session.post = mock_post
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=discovery),
            patch("jumpstarter_cli_common.oidc.aiohttp.ClientSession", return_value=mock_session),
            pytest.raises(click.ClickException, match="expired"),
        ):
            await config.device_authorization_grant()


# ---------------------------------------------------------------------------
# Warning suppression tests (NS-REQ-4, NS-REQ-5)
# ---------------------------------------------------------------------------


class TestAuthlibDeprecationWarningSuppressed:
    """TS-NS-4: importing oidc must not emit AuthlibDeprecationWarning."""

    def test_no_authlib_deprecation_warning_on_import(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            # Force re-evaluation of the filter + import chain
            import importlib

            import jumpstarter_cli_common.oidc

            importlib.reload(jumpstarter_cli_common.oidc)

        authlib_warnings = [
            w
            for w in caught
            if issubclass(w.category, DeprecationWarning) and "authlib" in str(w.message).lower()
        ]
        assert authlib_warnings == [], f"Unexpected authlib deprecation warnings: {authlib_warnings}"


class TestInsecureRequestWarningSuppressed:
    """TS-NS-5: Config.client() with insecure_tls=True suppresses InsecureRequestWarning."""

    def test_urllib3_insecure_request_warning_suppressed(self) -> None:
        import urllib3.exceptions

        config = Config(issuer="https://auth.example.com", client_id="test", insecure_tls=True)
        config.client()

        # After calling client() with insecure_tls=True, the urllib3
        # InsecureRequestWarning should be in the warning filters.
        matching_filters = [
            f
            for f in warnings.filters
            if len(f) >= 3 and f[0] == "ignore" and f[2] is urllib3.exceptions.InsecureRequestWarning
        ]
        assert len(matching_filters) > 0, "InsecureRequestWarning was not suppressed"
