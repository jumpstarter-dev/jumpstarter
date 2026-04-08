import ssl

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
