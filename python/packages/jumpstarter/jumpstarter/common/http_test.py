import aiohttp
import pytest

from .http import client_session

pytestmark = pytest.mark.anyio


async def test_client_session_uses_proxy_from_environment(http_proxy):
    async with client_session() as session, session.get("http://package.invalid/file") as response:
        assert await response.json() == {"proxied": True}

    assert http_proxy.requests == [("GET", "package.invalid")]


async def test_client_session_honors_no_proxy(http_proxy, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "package.invalid")
    monkeypatch.setenv("no_proxy", "package.invalid")

    with pytest.raises(aiohttp.ClientConnectorError):
        async with client_session() as session, session.get("http://package.invalid/file"):
            pass

    assert http_proxy.requests == []


async def test_client_session_can_opt_out_of_environment(http_proxy):
    with pytest.raises(aiohttp.ClientConnectorError):
        async with client_session(trust_env=False) as session, session.get("http://package.invalid/file"):
            pass

    assert http_proxy.requests == []


async def test_client_session_sends_proxy_credentials_from_environment(http_proxy, monkeypatch):
    proxy_with_credentials = http_proxy.url.replace("http://", "http://user:secret@")
    monkeypatch.setenv("HTTP_PROXY", proxy_with_credentials)
    monkeypatch.setenv("http_proxy", proxy_with_credentials)

    async with client_session() as session, session.get("http://package.invalid/file"):
        pass

    assert http_proxy.headers[0]["Proxy-Authorization"] == "Basic dXNlcjpzZWNyZXQ="


async def test_client_session_keeps_explicit_authorization_with_netrc_entry(http_proxy, monkeypatch, tmp_path):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine package.invalid login user password secret\n")
    monkeypatch.setenv("NETRC", str(netrc))

    async with (
        client_session() as session,
        session.get("http://package.invalid/file", headers={"Authorization": "Bearer token"}),
    ):
        pass

    assert http_proxy.headers[0]["Authorization"] == "Bearer token"


async def test_client_session_ignores_and_reports_https_proxy(http_proxy, monkeypatch, caplog):
    https_proxy = http_proxy.url.replace("http://", "https://")
    monkeypatch.setenv("HTTP_PROXY", https_proxy)
    monkeypatch.setenv("http_proxy", https_proxy)

    with pytest.raises(aiohttp.ClientConnectorError):
        async with client_session() as session, session.get("http://package.invalid/file"):
            pass

    assert http_proxy.requests == []
    assert "aiohttp only supports http:// proxies" in caplog.text
