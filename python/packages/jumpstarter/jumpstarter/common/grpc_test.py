import asyncio
import socket
from unittest.mock import patch

import pytest

from jumpstarter.common.exceptions import ConnectionError
from jumpstarter.common.grpc import _override_default_grpc_options, _ssl_channel_credentials_insecure


def test_default_options_preserve_existing_defaults():
    options = dict(_override_default_grpc_options(None))
    assert options["grpc.lb_policy_name"] == "round_robin"
    assert options["grpc.keepalive_time_ms"] == 20000


def test_user_options_override_defaults():
    user_options = {"grpc.keepalive_time_ms": 50000}
    options = dict(_override_default_grpc_options(user_options))
    assert options["grpc.keepalive_time_ms"] == 50000


def _addr_info(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class _LoopWithFakeResolver:
    """Delegates to the real loop, which anyio still needs, and fakes only
    getaddrinfo."""

    def __init__(self, loop, getaddrinfo):
        self._loop = loop
        self.getaddrinfo = getaddrinfo

    def __getattr__(self, name):
        return getattr(self._loop, name)


def _patch_resolver(getaddrinfo):
    def fake_get_running_loop():
        return _LoopWithFakeResolver(asyncio.events.get_running_loop(), getaddrinfo)

    return patch("asyncio.get_running_loop", fake_get_running_loop)


class TestSslChannelCredentialsInsecure:
    """Resolution and connection are timed separately, so the error a user
    sees points at the step that actually stalled."""

    @pytest.mark.asyncio
    async def test_returns_credentials_from_first_reachable_ip(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def connect(ip_address, *_args, **_kwargs):
            if ip_address == "192.0.2.1":
                raise OSError("connection refused")
            return b"-----BEGIN CERTIFICATE-----\n"

        with _patch_resolver(getaddrinfo):
            with patch(
                "jumpstarter.common.grpc._try_connect_and_extract_cert",
                connect,
            ):
                credentials = await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

        assert credentials is not None

    @pytest.mark.asyncio
    async def test_resolution_failure_names_the_host(self):
        async def getaddrinfo(*_args, **_kwargs):
            raise socket.gaierror("Name or service not known")

        with _patch_resolver(getaddrinfo):
            with pytest.raises(ConnectionError, match="Failed resolving example.com"):
                await _ssl_channel_credentials_insecure("example.com:443", timeout=5)

    @pytest.mark.asyncio
    async def test_slow_resolver_is_reported_as_a_resolution_timeout(self):
        async def getaddrinfo(*_args, **_kwargs):
            await asyncio.sleep(10)

        with _patch_resolver(getaddrinfo):
            with pytest.raises(ConnectionError, match="Timeout resolving example.com"):
                await _ssl_channel_credentials_insecure("example.com:443", timeout=0.05)

    @pytest.mark.asyncio
    async def test_connect_timeout_reports_the_resolved_ips(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1")

        async def never_connects(*_args, **_kwargs):
            await asyncio.sleep(10)

        with _patch_resolver(getaddrinfo):
            with patch("jumpstarter.common.grpc._try_connect_and_extract_cert", never_connects):
                with pytest.raises(
                    ConnectionError,
                    match=r"Timeout connecting to example\.com:443.*resolved to 192\.0\.2\.1",
                ):
                    await _ssl_channel_credentials_insecure("example.com:443", timeout=0.05)

    @pytest.mark.asyncio
    async def test_all_ips_failing_lists_the_errors(self):
        async def getaddrinfo(*_args, **_kwargs):
            return _addr_info("192.0.2.1", "192.0.2.2")

        async def refused(*_args, **_kwargs):
            raise OSError("connection refused")

        with _patch_resolver(getaddrinfo):
            with patch("jumpstarter.common.grpc._try_connect_and_extract_cert", refused):
                with pytest.raises(ConnectionError, match="all IPs exhausted"):
                    await _ssl_channel_credentials_insecure("example.com:443", timeout=5)
