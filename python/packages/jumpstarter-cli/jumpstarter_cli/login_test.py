import click
import pytest

from jumpstarter_cli.login import fetch_auth_config


@pytest.mark.asyncio
async def test_fetch_auth_config_rejects_http_without_insecure():
    with pytest.raises(click.UsageError, match="--insecure-login-http"):
        await fetch_auth_config("http://login.example.com", insecure_tls=False, use_http=False)


@pytest.mark.asyncio
async def test_fetch_auth_config_allows_http_with_use_http():
    """When use_http is True, an explicit http:// endpoint should be accepted (not raise UsageError).

    It will fail with a connection error since the host does not exist,
    but it must NOT raise UsageError.
    """
    with pytest.raises(Exception) as exc_info:
        await fetch_auth_config("http://login.example.com", insecure_tls=False, use_http=True)
    assert not isinstance(exc_info.value, click.UsageError)
