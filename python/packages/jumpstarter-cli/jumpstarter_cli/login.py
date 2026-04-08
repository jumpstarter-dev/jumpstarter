import json
import ssl
from typing import Any
from urllib.parse import urlparse

import aiohttp
import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions
from jumpstarter_cli_common.oidc import Config, decode_jwt_issuer, opt_oidc
from jumpstarter_cli_common.opt import confirm_insecure_tls, opt_insecure_tls, opt_nointeractive

from jumpstarter.common.exceptions import ReauthenticationFailed
from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1

# Default timeout for HTTP requests to prevent CLI from hanging indefinitely
_HTTP_TIMEOUT_SECONDS = 30


def _validate_login_endpoint_url(url: str, *, allow_http: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise click.ClickException(
            f"Invalid login endpoint '{url}': unsupported URL scheme '{parsed.scheme}'. Use http or https."
        )
    if parsed.scheme == "http" and not allow_http:
        raise click.ClickException(
            f"Refusing insecure login endpoint '{url}'. "
            "Use --insecure-tls / -k to allow plain HTTP login endpoints."
        )
    if not parsed.netloc:
        raise click.ClickException(f"Invalid login endpoint '{url}': missing host.")


def _validate_auth_config_payload(payload: Any, source_url: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise click.ClickException(
            f"Invalid auth config response from {source_url}: expected a JSON object."
        )
    grpc_endpoint = payload.get("grpcEndpoint")
    if not isinstance(grpc_endpoint, str) or not grpc_endpoint.strip():
        raise click.ClickException(
            f"Invalid auth config response from {source_url}: missing required field 'grpcEndpoint'."
        )
    return payload


async def fetch_auth_config(
    login_endpoint: str,
    insecure_tls: bool = False,
) -> dict[str, Any]:
    if login_endpoint.startswith("http://") and not insecure_tls:
        raise click.UsageError("HTTP login endpoints require --insecure-tls / -k.")

    if not login_endpoint.startswith(("http://", "https://")):
        login_endpoint = f"https://{login_endpoint}"

    _validate_login_endpoint_url(login_endpoint, allow_http=insecure_tls)

    url = f"{login_endpoint.rstrip('/')}/v1/auth/config"
    ssl_context: ssl.SSLContext | bool = False if insecure_tls else True
    timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT_SECONDS)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, ssl=ssl_context) as response:
                if response.status != 200:
                    raise click.ClickException(f"Failed to fetch auth config from {url}: HTTP {response.status}")
                payload = await response.json()
                return _validate_auth_config_payload(payload, url)
    except aiohttp.ClientConnectorCertificateError as e:
        raise click.ClickException(
            f"TLS certificate verification failed while connecting to {login_endpoint}. "
            "Verify the endpoint certificate, or use --insecure-tls / -k only for testing."
        ) from e
    except aiohttp.ClientConnectorSSLError as e:
        raise click.ClickException(
            f"TLS handshake failed while connecting to {login_endpoint}: {e}"
        ) from e
    except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
        raise click.ClickException(
            f"Invalid JSON response received from {url}. Verify the login endpoint or proxy configuration."
        ) from e
    except TimeoutError as e:
        raise click.ClickException(
            f"Timed out while connecting to {login_endpoint}. Check network connectivity and retry."
        ) from e


def parse_login_argument(login_arg: str) -> tuple[str | None, str]:
    """Parse a login argument in the format [username@]endpoint.

    Args:
        login_arg: String in format "username@login.example.com" or "login.example.com"

    Returns:
        Tuple of (username, endpoint) where username may be None
    """
    login_arg = login_arg.strip()
    if login_arg == "":
        raise click.ClickException("Login target cannot be empty.")

    if "@" in login_arg:
        # Split on the last @ to handle email-like usernames
        parts = login_arg.rsplit("@", 1)
        client_name = parts[0].strip()
        endpoint = parts[1].strip()
        if client_name == "":
            raise click.ClickException("Client name before '@' cannot be empty.")
        if endpoint == "":
            raise click.ClickException("Login endpoint after '@' cannot be empty.")
        return client_name, endpoint
    return None, login_arg


@click.command("login", short_help="Login")
@click.argument("login_target", required=False, default=None)
@click.option("-e", "--endpoint", type=str, help="Enter the Jumpstarter service endpoint.", default=None)
@click.option("--namespace", type=str, help="Enter the Jumpstarter exporter namespace.", default=None)
@click.option("--name", type=str, help="Enter the Jumpstarter exporter name.", default=None)
@opt_oidc
# client specific
# TODO: warn if used with exporter
@click.option(
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    default="",
)
@click.option(
    "--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).", default=None
)
# end client specific
@opt_insecure_tls
@opt_nointeractive
@opt_config(allow_missing=True)
@handle_exceptions
@blocking
async def login(  # noqa: C901
    config,
    login_target: str | None,
    endpoint: str,
    namespace: str,
    name: str,
    username: str | None,
    password: str | None,
    token: str | None,
    issuer: str,
    client_id: str,
    connector_id: str,
    callback_port: int | None,
    offline_access: bool,
    unsafe,
    insecure_tls: bool,
    nointeractive: bool,
    allow,
):
    """Login into a jumpstarter instance.

    Supports simplified login format: jmp login [client-name@]login.endpoint.com

    When using simplified format:
    - The part before @ becomes the client name (--name and --client)
    - The endpoint fetches configuration from the login service's /v1/auth/config API

    The fetched configuration provides:
    - gRPC endpoint
    - OIDC issuer
    - CA certificate (optional)
    - Default namespace
    """

    confirm_insecure_tls(insecure_tls, nointeractive)

    # Handle simplified login format: [client-name@]login.endpoint.com
    ca_bundle = None
    parsed_client_name = None
    if login_target is not None:
        parsed_client_name, login_endpoint = parse_login_argument(login_target)

        # If name was parsed from login target and --name not provided, use it
        if parsed_client_name and name is None:
            name = parsed_client_name

        # Fetch auth config from the login endpoint
        try:
            click.echo(f"Fetching configuration from {login_endpoint}...")
            auth_config = await fetch_auth_config(
                login_endpoint,
                insecure_tls=insecure_tls,
            )

            # Use fetched values if not explicitly provided
            if endpoint is None:
                endpoint = auth_config.get("grpcEndpoint")
            if namespace is None:
                namespace = auth_config.get("namespace")
            if issuer is None and auth_config.get("oidc"):
                # Use the first OIDC provider
                issuer = auth_config["oidc"][0].get("issuer")
                if client_id == "jumpstarter-cli" and auth_config["oidc"][0].get("clientId"):
                    client_id = auth_config["oidc"][0]["clientId"]

            # Store CA bundle for TLS configuration
            ca_bundle = auth_config.get("caBundle")
            if ca_bundle:
                click.echo("Retrieved CA certificate from login service.")

        except aiohttp.ClientError as e:
            raise click.ClickException(f"Failed to fetch auth config from {login_endpoint}: {e}") from e

    # If we parsed a client name from login_target and the config is an existing client
    # with a different alias, we should create a new config instead of updating the wrong one
    # (e.g., when the current default client differs from the target in simplified login)
    if (
        parsed_client_name
        and isinstance(config, ClientConfigV1Alpha1)
        and config.alias != parsed_client_name
    ):
        config = ("client", parsed_client_name)

    config_kind = None
    match config:
        # we are updating an existing config
        case ClientConfigV1Alpha1():
            issuer = decode_jwt_issuer(config.token)
            config_kind = "client"
        case ExporterConfigV1Alpha1():
            issuer = decode_jwt_issuer(config.token)
            config_kind = "exporter"
        # we are creating a new config
        case (kind, value):
            config_kind = kind

            # If client name was parsed from login_target and value is "default", use parsed name as alias
            if kind == "client" and value == "default" and parsed_client_name:
                value = parsed_client_name

            if namespace is None:
                if nointeractive:
                    raise click.UsageError("Namespace is required in non-interactive mode.")
                namespace = click.prompt("Enter the Jumpstarter exporter namespace")
            if name is None:
                if nointeractive:
                    raise click.UsageError("Name is required in non-interactive mode.")
                name = click.prompt("Enter the Jumpstarter exporter name")
            if endpoint is None:
                if nointeractive:
                    raise click.UsageError("Endpoint is required in non-interactive mode.")
                endpoint = click.prompt("Enter the Jumpstarter service endpoint")

            if kind.startswith("client"):
                if unsafe is None:
                    unsafe = False if nointeractive else click.confirm("Allow unsafe driver client imports?")
                if unsafe is False and allow == "":
                    if nointeractive:
                        raise click.UsageError("--allow TEXT or --unsafe is required in non-interactive mode.")
                    allow = click.prompt(
                        "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                    )

            # Build TLS config with CA bundle if available
            tls_config = TLSConfigV1Alpha1(insecure=insecure_tls, ca=ca_bundle or "")

            if kind.startswith("client"):
                config = ClientConfigV1Alpha1(
                    alias=value if kind == "client" else "default",
                    metadata=ObjectMeta(namespace=namespace, name=name),
                    tls=tls_config,
                    endpoint=endpoint,
                    token="",
                    drivers=ClientConfigV1Alpha1Drivers(allow=allow.split(","), unsafe=unsafe),
                )

            if kind.startswith("exporter"):
                config = ExporterConfigV1Alpha1(
                    alias=value if kind == "exporter" else "default",
                    tls=tls_config,
                    metadata=ObjectMeta(namespace=namespace, name=name),
                    endpoint=endpoint,
                    token="",
                )

    if issuer is None:
        if nointeractive:
            raise click.UsageError("Issuer is required in non-interactive mode.")
        issuer = click.prompt("Enter the OIDC issuer")

    stored_refresh_token = getattr(config, "refresh_token", None)
    oidc = Config(
        issuer=issuer,
        client_id=client_id,
        offline_access=offline_access or stored_refresh_token is not None,
        insecure_tls=insecure_tls,
    )

    def save_config() -> None:
        match config_kind:
            case "client":
                ClientConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]
            case "client_config":
                ClientConfigV1Alpha1.save(config, value)  # ty: ignore[invalid-argument-type]
            case "exporter":
                ExporterConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]
            case "exporter_config":
                ExporterConfigV1Alpha1.save(config, value)  # ty: ignore[invalid-argument-type]

    if stored_refresh_token and token is None and username is None and password is None:
        try:
            tokens = await oidc.refresh_token_grant(stored_refresh_token)
            config.token = tokens["access_token"]
            refresh_token = tokens.get("refresh_token")
            if refresh_token is not None and isinstance(config, ClientConfigV1Alpha1):
                config.refresh_token = refresh_token
            save_config()
            click.echo("Refreshed access token using stored refresh token.")
            return
        except Exception as e:
            if nointeractive:
                raise click.ClickException(f"Failed to refresh access token: {e}") from e
            pass

    if token is not None:
        kwargs = {"connector_id": connector_id} if connector_id is not None else {}
        tokens = await oidc.token_exchange_grant(token, **kwargs)
    elif username is not None and password is not None:
        tokens = await oidc.password_grant(username, password)
    else:
        tokens = await oidc.authorization_code_grant(callback_port=callback_port)

    config.token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")

    # only client configs support refresh_token
    if refresh_token is not None and isinstance(config, ClientConfigV1Alpha1):
        config.refresh_token = refresh_token

    save_config()
    # Set the new client as the default if it's a client config
    if config_kind in ("client", "client_config") and isinstance(config, ClientConfigV1Alpha1):
        user_config = UserConfigV1Alpha1.load_or_create()
        user_config.use_client(config.alias)
        click.echo(f"Set '{config.alias}' as the default client.")


@blocking
async def relogin_client(config: ClientConfigV1Alpha1):
    """Relogin into a jumpstarter instance"""
    client_id = "jumpstarter-cli"  # TODO: store this metadata in the config
    try:
        issuer = decode_jwt_issuer(config.token)
    except Exception as e:
        raise ReauthenticationFailed(f"Failed to decode JWT issuer: {e}") from e

    try:
        oidc = Config(
            issuer=issuer,
            client_id=client_id,
            offline_access=config.refresh_token is not None,
            insecure_tls=config.tls.insecure,
        )
        if config.refresh_token:
            try:
                tokens = await oidc.refresh_token_grant(config.refresh_token)
                config.token = tokens["access_token"]
                refresh_token = tokens.get("refresh_token")
                if refresh_token is not None:
                    config.refresh_token = refresh_token
                ClientConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]
                return
            except Exception:
                pass

        tokens = await oidc.authorization_code_grant()
        config.token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")
        if refresh_token is not None:
            config.refresh_token = refresh_token
        ClientConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]
    except Exception as e:
        raise ReauthenticationFailed(f"Failed to re-authenticate: {e}") from e
