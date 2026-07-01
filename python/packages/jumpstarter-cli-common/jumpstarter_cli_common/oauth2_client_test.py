import warnings
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from jumpstarter_cli_common.oidc import (
    Config,
    OAuthError,
    OAuthStateMismatchError,
    _OAuth2Client,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class TestOAuth2ClientInit:
    def test_init_sets_attributes(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid", "profile"])
        assert client.client_id == "my-client"
        assert client.scope == ["openid", "profile"]
        assert client.redirect_uri is None
        assert client._expected_state is None
        assert isinstance(client._session, requests.Session)

    def test_init_with_redirect_uri(self):
        client = _OAuth2Client(
            client_id="my-client",
            scope=["openid"],
            redirect_uri="http://localhost:8080/callback",
        )
        assert client.redirect_uri == "http://localhost:8080/callback"


class TestOAuth2ClientVerifyProperty:
    def test_verify_getter_returns_session_verify(self):
        client = _OAuth2Client(client_id="c", scope=[])
        assert client.verify is True

    def test_verify_setter_updates_session_verify(self):
        client = _OAuth2Client(client_id="c", scope=[])
        client.verify = False
        assert client.verify is False
        assert client._session.verify is False

    def test_verify_setter_with_cert_path(self):
        client = _OAuth2Client(client_id="c", scope=[])
        client.verify = "/path/to/ca-bundle.crt"
        assert client.verify == "/path/to/ca-bundle.crt"
        assert client._session.verify == "/path/to/ca-bundle.crt"


class TestOAuth2ClientClose:
    def test_close_calls_session_close(self):
        client = _OAuth2Client(client_id="c", scope=[])
        with patch.object(client._session, "close") as mock_close:
            client.close()
            mock_close.assert_called_once()


class TestOAuth2ClientCreateAuthorizationUrl:
    def test_basic_url_construction(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid", "profile"])
        url, state = client.create_authorization_url("https://auth.example.com/authorize")

        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        assert parsed.scheme == "https"
        assert parsed.netloc == "auth.example.com"
        assert parsed.path == "/authorize"
        assert params["response_type"] == ["code"]
        assert params["client_id"] == ["test-client"]
        assert params["scope"] == ["openid profile"]
        assert params["state"] == [state]
        assert len(state) > 0

    def test_stores_expected_state(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid"])
        _, state = client.create_authorization_url("https://auth.example.com/authorize")
        assert client._expected_state == state

    def test_includes_redirect_uri_when_set(self):
        client = _OAuth2Client(
            client_id="test-client",
            scope=["openid"],
            redirect_uri="http://localhost:9999/callback",
        )
        url, _state = client.create_authorization_url("https://auth.example.com/authorize")
        params = parse_qs(urlparse(url).query)
        assert params["redirect_uri"] == ["http://localhost:9999/callback"]

    def test_no_redirect_uri_when_not_set(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid"])
        url, _state = client.create_authorization_url("https://auth.example.com/authorize")
        params = parse_qs(urlparse(url).query)
        assert "redirect_uri" not in params

    def test_extra_kwargs_included(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid"])
        url, _state = client.create_authorization_url(
            "https://auth.example.com/authorize",
            prompt="consent",
            nonce="abc123",
        )
        params = parse_qs(urlparse(url).query)
        assert params["prompt"] == ["consent"]
        assert params["nonce"] == ["abc123"]

    def test_url_with_existing_query_params(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid"])
        url, _state = client.create_authorization_url("https://auth.example.com/authorize?foo=bar")
        assert "authorize?foo=bar&" in url

    def test_state_is_unique(self):
        client = _OAuth2Client(client_id="test-client", scope=["openid"])
        _, state1 = client.create_authorization_url("https://auth.example.com/authorize")
        _, state2 = client.create_authorization_url("https://auth.example.com/authorize")
        assert state1 != state2


class TestOAuth2ClientFetchToken:
    def _mock_response(self, json_data, status_code=200):
        mock_resp = MagicMock()
        mock_resp.json.return_value = json_data
        mock_resp.raise_for_status.return_value = None
        mock_resp.status_code = status_code
        return mock_resp

    def test_fetch_token_with_grant_type(self):
        token_data = {"access_token": "tok123", "token_type": "Bearer"}
        client = _OAuth2Client(client_id="my-client", scope=["openid", "profile"])

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)) as mock_post:
            result = client.fetch_token(
                "https://auth.example.com/token",
                grant_type="password",
                username="user",
                password="pass",
            )

        assert result == token_data
        call_kwargs = mock_post.call_args
        post_data = call_kwargs.kwargs["data"]
        assert post_data["client_id"] == "my-client"
        assert post_data["grant_type"] == "password"
        assert post_data["username"] == "user"
        assert post_data["password"] == "pass"
        assert post_data["scope"] == "openid profile"

    def test_fetch_token_sends_correct_headers(self):
        token_data = {"access_token": "tok123"}
        client = _OAuth2Client(client_id="my-client", scope=["openid"])

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)) as mock_post:
            client.fetch_token("https://auth.example.com/token", grant_type="password")

        headers = mock_post.call_args.kwargs["headers"]
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/x-www-form-urlencoded"

    def test_fetch_token_with_authorization_response(self):
        token_data = {"access_token": "tok456", "token_type": "Bearer"}
        client = _OAuth2Client(
            client_id="my-client",
            scope=["openid"],
            redirect_uri="http://localhost:8080/callback",
        )
        client._expected_state = "xyz"

        callback_url = "http://localhost:8080/callback?code=authcode123&state=xyz"

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)) as mock_post:
            result = client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

        assert result == token_data
        post_data = mock_post.call_args.kwargs["data"]
        assert post_data["code"] == "authcode123"
        assert post_data["redirect_uri"] == "http://localhost:8080/callback"
        assert post_data["grant_type"] == "authorization_code"

    def test_fetch_token_validates_state(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])
        client._expected_state = "expected-state"

        callback_url = "http://localhost:8080/callback?code=abc&state=wrong-state"

        with pytest.raises(OAuthStateMismatchError, match="state mismatch"):
            client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

    def test_fetch_token_validates_state_missing_in_response(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])
        client._expected_state = "expected-state"

        callback_url = "http://localhost:8080/callback?code=abc"

        with pytest.raises(OAuthStateMismatchError, match="state mismatch"):
            client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

    def test_fetch_token_skips_state_validation_when_no_expected_state(self):
        token_data = {"access_token": "tok789"}
        client = _OAuth2Client(client_id="my-client", scope=["openid"])
        # _expected_state is None (no create_authorization_url was called)

        callback_url = "http://localhost:8080/callback?code=abc&state=any"

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)):
            result = client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )
        assert result == token_data

    def test_fetch_token_raises_on_oauth_error_response(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])

        callback_url = "http://localhost:8080/callback?error=access_denied&error_description=User+denied+access"

        with pytest.raises(OAuthError, match="access_denied.*User denied access"):
            client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

    def test_fetch_token_raises_on_oauth_error_without_description(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])

        callback_url = "http://localhost:8080/callback?error=server_error"

        with pytest.raises(OAuthError, match="server_error"):
            client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

    def test_fetch_token_authorization_response_without_redirect_uri(self):
        token_data = {"access_token": "tok000"}
        client = _OAuth2Client(client_id="my-client", scope=["openid"])

        callback_url = "http://localhost:8080/callback?code=abc"

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)) as mock_post:
            client.fetch_token(
                "https://auth.example.com/token",
                authorization_response=callback_url,
            )

        post_data = mock_post.call_args.kwargs["data"]
        assert "redirect_uri" not in post_data

    def test_fetch_token_scope_provided_in_kwargs(self):
        token_data = {"access_token": "tok999"}
        client = _OAuth2Client(client_id="my-client", scope=["openid", "profile"])

        with patch.object(client._session, "post", return_value=self._mock_response(token_data)) as mock_post:
            client.fetch_token(
                "https://auth.example.com/token",
                grant_type="client_credentials",
                scope="custom_scope",
            )

        post_data = mock_post.call_args.kwargs["data"]
        assert post_data["scope"] == "custom_scope"

    def test_fetch_token_raises_on_http_error(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")

        with patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(requests.HTTPError):
                client.fetch_token("https://auth.example.com/token", grant_type="password")

    def test_fetch_token_raises_on_non_json_response(self):
        client = _OAuth2Client(client_id="my-client", scope=["openid"])
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.status_code = 200
        mock_resp.json.side_effect = ValueError("No JSON")

        with patch.object(client._session, "post", return_value=mock_resp):
            with pytest.raises(OAuthError, match="non-JSON response"):
                client.fetch_token("https://auth.example.com/token", grant_type="password")


class TestConfigClient:
    def test_client_returns_oauth2_client(self):
        config = Config(issuer="https://issuer.example.com", client_id="test-client")
        session = config.client()
        assert isinstance(session, _OAuth2Client)
        assert session.client_id == "test-client"
        assert session.scope == ["openid", "profile"]

    def test_client_passes_kwargs(self):
        config = Config(issuer="https://issuer.example.com", client_id="test-client")
        session = config.client(redirect_uri="http://localhost:9999/callback")
        assert session.redirect_uri == "http://localhost:9999/callback"


class TestConfigGrantMethods:
    """Tests for Config grant methods that use _OAuth2Client.fetch_token."""

    @pytest.fixture
    def config(self):
        return Config(issuer="https://issuer.example.com", client_id="test-client")

    @pytest.fixture
    def mock_configuration(self):
        return {
            "token_endpoint": "https://issuer.example.com/token",
            "authorization_endpoint": "https://issuer.example.com/authorize",
        }

    @pytest.fixture
    def token_response(self):
        return {"access_token": "access-tok", "token_type": "Bearer", "expires_in": 3600}

    @pytest.mark.anyio
    async def test_token_exchange_grant(self, config, mock_configuration, token_response):
        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=mock_configuration),
            patch.object(_OAuth2Client, "fetch_token", return_value=token_response) as mock_fetch,
            patch.object(_OAuth2Client, "close") as mock_close,
        ):
            result = await config.token_exchange_grant(token="id-token-value", connector_id="ldap")

        assert result == token_response
        call_kwargs = mock_fetch.call_args
        assert call_kwargs.args[0] == "https://issuer.example.com/token"
        assert call_kwargs.kwargs["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
        assert call_kwargs.kwargs["subject_token"] == "id-token-value"
        assert call_kwargs.kwargs["connector_id"] == "ldap"
        mock_close.assert_called_once()

    @pytest.mark.anyio
    async def test_refresh_token_grant(self, config, mock_configuration, token_response):
        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=mock_configuration),
            patch.object(_OAuth2Client, "fetch_token", return_value=token_response) as mock_fetch,
            patch.object(_OAuth2Client, "close") as mock_close,
        ):
            result = await config.refresh_token_grant(refresh_token="refresh-tok")

        assert result == token_response
        call_kwargs = mock_fetch.call_args
        assert call_kwargs.args[0] == "https://issuer.example.com/token"
        assert call_kwargs.kwargs["grant_type"] == "refresh_token"
        assert call_kwargs.kwargs["refresh_token"] == "refresh-tok"
        mock_close.assert_called_once()

    @pytest.mark.anyio
    async def test_password_grant(self, config, mock_configuration, token_response):
        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=mock_configuration),
            patch.object(_OAuth2Client, "fetch_token", return_value=token_response) as mock_fetch,
            patch.object(_OAuth2Client, "close") as mock_close,
        ):
            result = await config.password_grant(username="testuser", password="testpass")

        assert result == token_response
        call_kwargs = mock_fetch.call_args
        assert call_kwargs.args[0] == "https://issuer.example.com/token"
        assert call_kwargs.kwargs["grant_type"] == "password"
        assert call_kwargs.kwargs["username"] == "testuser"
        assert call_kwargs.kwargs["password"] == "testpass"
        mock_close.assert_called_once()

    @pytest.mark.anyio
    async def test_grant_closes_client_on_error(self, config, mock_configuration):
        with (
            patch.object(config, "configuration", new_callable=AsyncMock, return_value=mock_configuration),
            patch.object(_OAuth2Client, "fetch_token", side_effect=requests.HTTPError("500")),
            patch.object(_OAuth2Client, "close") as mock_close,
        ):
            with pytest.raises(requests.HTTPError):
                await config.password_grant(username="u", password="p")

        mock_close.assert_called_once()


class TestNoAuthlibDeprecationWarning:
    def test_import_oidc_module_does_not_raise_deprecation_warning(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import importlib

            import jumpstarter_cli_common.oidc

            importlib.reload(jumpstarter_cli_common.oidc)
            deprecation_warnings = [
                w for w in caught if issubclass(w.category, DeprecationWarning)
            ]
            assert deprecation_warnings == [], (
                f"DeprecationWarning raised on import: {deprecation_warnings}"
            )
