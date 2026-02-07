import json
import os
import ssl
import time
from dataclasses import dataclass
from functools import wraps
from typing import ClassVar

import aiohttp
import certifi
import click
from aiohttp import web
from anyio import create_memory_object_stream
from anyio.to_thread import run_sync
from authlib.integrations.requests_client import OAuth2Session
from joserfc.jws import extract_compact
from yarl import URL

from jumpstarter.config.env import JMP_OIDC_CALLBACK_PORT


def _get_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that respects SSL_CERT_FILE environment variable."""
    ssl_ctx = ssl.create_default_context()
    ca_file = os.environ.get("SSL_CERT_FILE") or certifi.where()
    ssl_ctx.load_verify_locations(ca_file)
    return ssl_ctx


def opt_oidc(f):
    @click.option("--issuer", help="OIDC issuer")
    @click.option("--client-id", "client_id", help="OIDC client id", default="jumpstarter-cli")
    @click.option("--token", help="OIDC access token")
    @click.option("--username", help="OIDC username")
    @click.option("--password", help="OIDC password")
    @click.option("--connector-id", "connector_id", help="OIDC token exchange connector id (Dex specific)")
    @click.option(
        "--callback-port",
        "callback_port",
        type=click.IntRange(0, 65535),
        default=None,
        help="Port for OIDC callback server (0=random port)",
    )
    @click.option(
        "--offline-access/--no-offline-access",
        default=True,
        help="Request offline_access scope (refresh token)",
    )
    @wraps(f)
    def wrapper(*args, **kwds):
        return f(*args, **kwds)

    return wrapper


@dataclass(kw_only=True)
class Config:
    issuer: str
    client_id: str
    offline_access: bool = False
    scope: ClassVar[list[str]] = ["openid", "profile"]

    async def configuration(self):
        connector = aiohttp.TCPConnector(ssl=_get_ssl_context())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                URL(self.issuer).joinpath(".well-known", "openid-configuration"),
                raise_for_status=True,
            ) as response:
                return await response.json()

    def _scopes(self) -> list[str]:
        if self.offline_access:
            return [*self.scope, "offline_access"]
        return list(self.scope)

    def client(self, **kwargs):
        return OAuth2Session(client_id=self.client_id, scope=self._scopes(), **kwargs)

    async def token_exchange_grant(self, token: str, **kwargs):
        config = await self.configuration()

        client = self.client()

        return await run_sync(
            lambda: client.fetch_token(
                config["token_endpoint"],
                grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
                requested_token_type="urn:ietf:params:oauth:token-type:access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:id_token",
                subject_token=token,
                **kwargs,
            )
        )

    async def refresh_token_grant(self, refresh_token: str):
        config = await self.configuration()

        client = self.client()

        return await run_sync(
            lambda: client.fetch_token(
                config["token_endpoint"],
                grant_type="refresh_token",
                refresh_token=refresh_token,
            )
        )

    async def password_grant(self, username: str, password: str):
        config = await self.configuration()

        client = self.client()

        return await run_sync(
            lambda: client.fetch_token(
                config["token_endpoint"], grant_type="password", username=username, password=password
            )
        )

    async def authorization_code_grant(self, callback_port: int | None = None, prompt: str | None = None):
        config = await self.configuration()

        # Use provided port, fall back to env var, then default to 0 (OS picks)
        if callback_port is not None:
            port = callback_port
        else:
            env_value = os.environ.get(JMP_OIDC_CALLBACK_PORT)
            if env_value is None:
                port = 0
            elif env_value.isdigit() and int(env_value) <= 65535:
                port = int(env_value)
            else:
                raise click.ClickException(f'Invalid {JMP_OIDC_CALLBACK_PORT} "{env_value}": must be a valid port')

        tx, rx = create_memory_object_stream()

        async def callback(request):
            await tx.send(str(request.url))
            return web.Response(text="Login successful, you can close this page")

        app = web.Application()
        app.add_routes([web.get("/callback", callback)])

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()

        site = web.TCPSite(runner, "localhost", port)
        try:
            await site.start()
        except OSError as e:
            await runner.cleanup()
            raise click.ClickException(f"Failed to start callback server on port {port}: {e}") from None

        redirect_uri = "http://localhost:%d/callback" % site._server.sockets[0].getsockname()[1]

        client = self.client(redirect_uri=redirect_uri)

        # Add prompt parameter if force requested
        auth_params = {}
        if prompt:
            auth_params["prompt"] = prompt

        uri, state = client.create_authorization_url(config["authorization_endpoint"], **auth_params)

        print("Please open the URL in browser: ", uri)

        authorization_response = await rx.receive()

        await site.stop()
        await runner.cleanup()

        return await run_sync(
            lambda: client.fetch_token(config["token_endpoint"], authorization_response=authorization_response)
        )


def decode_jwt(token: str):
    try:
        return json.loads(extract_compact(token.encode()).payload)
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"Invalid JWT format: {e}") from e


def decode_jwt_issuer(token: str):
    return decode_jwt(token).get("iss")


def get_token_expiry(token: str) -> int | None:
    """Get token expiry timestamp (Unix epoch seconds) from JWT.

    Returns None if token doesn't have an exp claim.
    """
    return decode_jwt(token).get("exp")


def get_token_remaining_seconds(token: str) -> float | None:
    """Get seconds remaining until token expires.

    Returns:
        Positive value if token is still valid
        Negative value if token is expired (magnitude = how long ago)
        None if token doesn't have an exp claim
    """
    exp = get_token_expiry(token)
    if exp is None:
        return None
    return exp - time.time()


# Token expiry warning threshold in seconds (5 minutes)
TOKEN_EXPIRY_WARNING_SECONDS = 300


def is_token_expired(token: str, buffer_seconds: int = 0) -> bool:
    """Check if token is expired or will expire within buffer_seconds.

    Args:
        token: JWT token string
        buffer_seconds: Consider expired if less than this many seconds remain

    Returns:
        True if token is expired or will expire within buffer
        False if token is still valid (or has no exp claim)
    """
    remaining = get_token_remaining_seconds(token)
    if remaining is None:
        return False
    return remaining < buffer_seconds


def format_duration(seconds: float) -> str:
    """Format a duration in seconds as a human-readable string.

    Args:
        seconds: Duration in seconds (can be negative for past times)

    Returns:
        Formatted string like "2h 30m" or "5m 10s"
    """
    abs_seconds = abs(seconds)
    hours = int(abs_seconds // 3600)
    mins = int((abs_seconds % 3600) // 60)
    secs = int(abs_seconds % 60)

    if hours > 0:
        return f"{hours}h {mins}m"
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"
