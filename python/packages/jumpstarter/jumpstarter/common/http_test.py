import aiohttp
import pytest

from .http import client_session

pytestmark = pytest.mark.anyio


async def test_client_session_uses_proxy_from_environment(http_proxy):
    async with client_session() as session, session.get("http://package.invalid/file") as response:
        assert await response.json() == {"proxied": True}

    assert http_proxy.requests == [("GET", "package.invalid")]


async def test_client_session_honors_no_proxy(http_proxy, monkeypatch):
    monkeypatch.setenv("no_proxy", "package.invalid")

    with pytest.raises(aiohttp.ClientConnectorError):
        async with client_session() as session, session.get("http://package.invalid/file"):
            pass

    assert http_proxy.requests == []


async def test_client_session_sends_proxy_credentials_from_environment(http_proxy, monkeypatch):
    monkeypatch.setenv("http_proxy", http_proxy.url.replace("http://", "http://user:secret@"))

    async with client_session() as session, session.get("http://package.invalid/file"):
        pass

    assert http_proxy.headers[0]["Proxy-Authorization"] == "Basic dXNlcjpzZWNyZXQ="


async def test_client_session_reads_a_proxy_without_scheme_as_http(http_proxy, monkeypatch):
    monkeypatch.setenv("http_proxy", http_proxy.url.removeprefix("http://"))

    async with client_session() as session, session.get("http://package.invalid/file"):
        pass

    assert http_proxy.requests == [("GET", "package.invalid")]


async def test_client_session_reaches_an_https_proxy_over_tls(tls_http_proxy):
    # ssl=False because the fake proxy's certificate is self-signed.
    async with client_session() as session, session.get("http://package.invalid/file", ssl=False) as response:
        assert await response.json() == {"proxied": True}

    assert tls_http_proxy.requests == [("GET", "package.invalid")]


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


async def test_client_session_ignores_and_reports_unsupported_proxy(http_proxy, monkeypatch, caplog):
    caplog.set_level("WARNING", logger="jumpstarter.common.http")
    monkeypatch.setenv("http_proxy", http_proxy.url.replace("http://", "socks5://"))

    with pytest.raises(aiohttp.ClientConnectorError):
        async with client_session() as session, session.get("http://package.invalid/file"):
            pass

    assert http_proxy.requests == []
    assert "aiohttp only supports http:// and https:// proxies" in caplog.text
