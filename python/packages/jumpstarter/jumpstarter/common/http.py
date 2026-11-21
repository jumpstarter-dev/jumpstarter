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
    # Like curl and requests, read a proxy without a scheme as an http:// proxy.
    proxy = URL(value if "://" in value else f"http://{value}")
    if proxy.scheme not in ("http", "https"):
        logger.warning("Ignoring the %s:// proxy: aiohttp only supports http:// and https:// proxies", proxy.scheme)
        return None
    # aiohttp sends the credentials of the proxy URL, if any, in Proxy-Authorization, and
    # reaches an https:// proxy over TLS.
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
def client_session(**kwargs: Any) -> aiohttp.ClientSession:  # noqa: TID251
    """Create an aiohttp client session that honors the proxy environment.

    aiohttp ignores http_proxy, https_proxy and no_proxy unless the session is created with
    trust_env=True, unlike requests and urllib. Use this helper for every outgoing HTTP
    session so Jumpstarter behaves the same on hosts that reach the network through a proxy.

    The proxy is selected from the environment like urllib does, but unlike trust_env=True,
    ~/.netrc is never read: aiohttp would send its credentials to the target host, and refuse
    requests that already carry an Authorization header for that host.
    """
    return aiohttp.ClientSession(request_class=ProxyFromEnvironmentRequest, **kwargs)  # noqa: TID251
