import ssl
import warnings

from jumpstarter_cli_common.oidc import Config, _get_ssl_context


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


class TestAuthlibDeprecationWarningSuppressed:
    """Importing oidc must not emit AuthlibDeprecationWarning."""

    def test_no_authlib_deprecation_warning_on_import(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
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
    """Config.client() with insecure_tls=True suppresses InsecureRequestWarning."""

    def test_urllib3_insecure_request_warning_suppressed(self) -> None:
        import urllib3.exceptions

        config = Config(issuer="https://auth.example.com", client_id="test", insecure_tls=True)
        config.client()

        matching_filters = [
            f
            for f in warnings.filters
            if len(f) >= 3 and f[0] == "ignore" and f[2] is urllib3.exceptions.InsecureRequestWarning
        ]
        assert len(matching_filters) > 0, "InsecureRequestWarning was not suppressed"
