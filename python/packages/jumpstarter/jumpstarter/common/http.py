import logging
from typing import Any
from urllib.request import getproxies, proxy_bypass

import aiohttp
from aiohttp.typedefs import LooseHeaders
from yarl import URL

logger = logging.getLogger(__name__)


def _proxy_from_environment(url: URL) -> URL | None:
    """Return the proxy that http_proxy, https_proxy and no_proxy select for url, if any."""
    if url.host is None or proxy_bypass(url.host):
        return None
    value = getproxies().get(url.scheme)
    if not value:
        return None
    proxy = URL(value)
    if proxy.scheme != "http":
        logger.warning("Ignoring proxy %s: aiohttp only supports http:// proxies", proxy.with_user(None))
        return None
    # aiohttp sends the credentials of the proxy URL, if any, in Proxy-Authorization.
    return proxy


class ProxyFromEnvironmentRequest(aiohttp.ClientRequest):
    """Use the proxy from the environment when the request does not set one."""

    def update_proxy(
        self, proxy: URL | None, proxy_auth: aiohttp.BasicAuth | None, proxy_headers: LooseHeaders | None
    ) -> None:
        if proxy is None:
            proxy = _proxy_from_environment(self.url)
        super().update_proxy(proxy, proxy_auth, proxy_headers)


# This helper is the only code allowed to create aiohttp sessions directly, hence the
# TID251 exemptions: ruff bans aiohttp.ClientSession everywhere else.
def client_session(*, trust_env: bool = True, **kwargs: Any) -> aiohttp.ClientSession:  # noqa: TID251
    """Create an aiohttp client session that honors the proxy environment.

    aiohttp ignores http_proxy, https_proxy and no_proxy unless the session is created with
    trust_env=True, unlike requests and urllib. Use this helper for every outgoing HTTP
    session so Jumpstarter behaves the same on hosts that reach the network through a proxy.

    The proxy is selected as aiohttp does with trust_env=True, but ~/.netrc is never read:
    aiohttp would send its credentials to the target host, and refuse requests that already
    carry an Authorization header for that host.

    Pass trust_env=False for connections that must never go through a proxy, such as a Unix
    socket.
    """
    if trust_env:
        kwargs.setdefault("request_class", ProxyFromEnvironmentRequest)
    return aiohttp.ClientSession(**kwargs)  # noqa: TID251
