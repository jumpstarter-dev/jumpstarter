import click
import pytest

from .client import BaseFlasherClient
from jumpstarter.common.exceptions import ArgumentError


class MockFlasherClient(BaseFlasherClient):
    """Mock client for testing without full initialization"""

    def __init__(self):
        self._manifest = None
        self._console_debug = False
        self.logger = type(
            "MockLogger", (), {"warning": lambda msg: None, "info": lambda msg: None, "error": lambda msg: None}
        )()

    def close(self):
        pass


def test_validate_bearer_token_fails_invalid():
    """Test bearer token validation fails with invalid tokens"""
    client = MockFlasherClient()

    with pytest.raises(click.ClickException, match="Bearer token cannot be empty"):
        client._validate_bearer_token("")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token("token with spaces")

    with pytest.raises(click.ClickException, match="Bearer token contains invalid characters"):
        client._validate_bearer_token('token"with"quotes')


def test_curl_header_args_handles_quotes():
    """Test curl header formatting safely handles quotes"""
    client = MockFlasherClient()

    result = client._curl_header_args({"Authorization": "Bearer abc'def"})
    assert "'\"'\"'" in result
    assert result.startswith("-H '")
    assert result.endswith("'")


def test_flash_fails_with_invalid_headers():
    """Test flash method fails early with invalid headers"""
    client = MockFlasherClient()

    with pytest.raises(ArgumentError, match="Invalid header name 'Invalid Header': must be an HTTP token"):
        client.flash("test.raw", headers={"Invalid Header": "value"})
