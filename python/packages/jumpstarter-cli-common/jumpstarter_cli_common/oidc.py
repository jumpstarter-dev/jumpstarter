import asyncio
import json
import os
import ssl
import time
import warnings
from dataclasses import dataclass
from functools import wraps
from typing import ClassVar

import aiohttp
import certifi
import click
from aiohttp import web
from anyio import create_memory_object_stream
from anyio.to_thread import run_sync

# Suppress AuthlibDeprecationWarning emitted unconditionally during the authlib
# import chain (authlib._joserfc_helpers -> authlib.jose).  The project already
# uses joserfc directly for JWT operations; authlib is only needed for
# OAuth2Session.  The warning provides no actionable information to end users.
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"authlib\.")

from authlib.integrations.requests_client import OAuth2Session  # noqa: E402
from joserfc.jws import extract_compact  # noqa: E402
from yarl import URL  # noqa: E402

from jumpstarter.config.env import JMP_OIDC_CALLBACK_PORT, JMP_OIDC_DEVICE_FLOW  # noqa: E402


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
    @click.option(
        "--device-flow",
        "device_flow",
        is_flag=True,
        default=False,
        help="Use OAuth 2.0 Device Authorization Grant (RFC 8628) instead of authorization code flow. "
        "Useful in headless or containerized environments where localhost callbacks are not available.",
    )
    @wraps(f)
    def wrapper(*args, **kwds):
        return f(*args, **kwds)

    return wrapper


def should_use_device_flow(device_flow_flag: bool) -> bool:
    """Determine whether to use the device authorization grant flow.

    Returns True if:
    - The --device-flow CLI flag was explicitly passed, OR
    - The JMP_OIDC_DEVICE_FLOW environment variable is set to "1"
    """
    if device_flow_flag:
        return True
    return os.environ.get(JMP_OIDC_DEVICE_FLOW) == "1"


@dataclass(kw_only=True)
class Config:
    issuer: str
    client_id: str
    offline_access: bool = False
    insecure_tls: bool = False
    scope: ClassVar[list[str]] = ["openid", "profile"]

    async def configuration(self):
        ssl_context: ssl.SSLContext | bool = False if self.insecure_tls else _get_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_context)
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
        session = OAuth2Session(client_id=self.client_id, scope=self._scopes(), **kwargs)
        if self.insecure_tls:
            session.verify = False
            # The user has already opted into insecure TLS (via --insecure flag
            # or config), so urllib3's InsecureRequestWarning is redundant noise.
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        else:
            session.verify = os.environ.get("SSL_CERT_FILE") or certifi.where()
        return session

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

    async def device_authorization_grant(self):
        """Perform OAuth 2.0 Device Authorization Grant (RFC 8628).

        This flow is suitable for headless or containerized environments where
        a localhost callback server is not accessible from the user's browser.

        The flow:
        1. Request a device code from the authorization server.
        2. Display a verification URL and user code to the user.
        3. Poll the token endpoint until the user completes authorization.
        """
        config = await self.configuration()

        device_endpoint = config.get("device_authorization_endpoint")
        if not device_endpoint:
            raise click.ClickException(
                "The identity provider does not support Device Authorization Grant (RFC 8628). "
                "The OIDC discovery document does not include a 'device_authorization_endpoint'. "
                "Contact your IdP administrator to enable device flow, or use a different login method."
            )

        token_endpoint = config["token_endpoint"]

        ssl_context: ssl.SSLContext | bool = False if self.insecure_tls else _get_ssl_context()
        connector = aiohttp.TCPConnector(ssl=ssl_context)

        async with aiohttp.ClientSession(connector=connector) as session:
            # Step 1: Request device authorization
            async with session.post(
                device_endpoint,
                data={
                    "client_id": self.client_id,
                    "scope": " ".join(self._scopes()),
                },
            ) as response:
                if response.status != 200:
                    text = await response.text()
                    raise click.ClickException(
                        f"Device authorization request failed (HTTP {response.status}): {text}"
                    )
                device_data = await response.json()

            device_code = device_data["device_code"]
            interval = device_data.get("interval", 5)
            expires_in = device_data.get("expires_in", 600)

            # Step 2: Display verification URI to user
            verification_uri_complete = device_data.get("verification_uri_complete")
            if verification_uri_complete:
                click.echo(f"To sign in, open the following URL in your browser:\n\n  {verification_uri_complete}\n")
            else:
                verification_uri = device_data.get("verification_uri")
                user_code = device_data.get("user_code")
                click.echo(
                    f"To sign in, open the following URL in your browser:\n\n  {verification_uri}\n\n"
                    f"Then enter the code: {user_code}\n"
                )

            click.echo("Waiting for authentication...")

            # Step 3: Poll the token endpoint
            deadline = time.monotonic() + expires_in
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)

                async with session.post(
                    token_endpoint,
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        "device_code": device_code,
                        "client_id": self.client_id,
                    },
                ) as token_response:
                    token_data = await token_response.json()

                    if token_response.status == 200:
                        return token_data

                    error = token_data.get("error", "")
                    if error == "authorization_pending":
                        continue
                    elif error == "slow_down":
                        interval += 5
                        continue
                    elif error == "expired_token":
                        raise click.ClickException(
                            "Device authorization has expired. Please try again."
                        )
                    elif error == "access_denied":
                        raise click.ClickException(
                            "Authorization request was denied by the user."
                        )
                    else:
                        error_description = token_data.get("error_description", "")
                        raise click.ClickException(
                            f"Device authorization failed: {error}"
                            + (f" - {error_description}" if error_description else "")
                        )

            raise click.ClickException(
                "Device authorization timed out waiting for user approval. Please try again."
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
