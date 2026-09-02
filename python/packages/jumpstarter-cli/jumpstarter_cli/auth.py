from datetime import datetime, timezone
from typing import Literal, Optional

import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.oidc import (
    TOKEN_EXPIRY_WARNING_SECONDS,
    Config,
    decode_jwt,
    decode_jwt_issuer,
    format_duration,
    get_token_remaining_seconds,
)
from jumpstarter_cli_common.opt import DataOutputType, opt_output_json_yaml
from jumpstarter_cli_common.print import model_print
from pydantic import BaseModel, ConfigDict, Field

from jumpstarter.config.client import ClientConfigV1Alpha1


@click.group()
def auth():
    """Authentication and token management commands."""


class AuthStatusV1Alpha1(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    kind: Literal["AuthStatus"] = Field(default="AuthStatus")

    status: Literal["valid", "expiring-soon", "expired", "no-expiry", "no-token", "invalid-token"]
    expires_at: Optional[datetime] = Field(alias="expiresAt", default=None)
    remaining_seconds: Optional[float] = Field(alias="remainingSeconds", default=None)
    subject: Optional[str] = None
    issuer: Optional[str] = None
    issued_at: Optional[datetime] = Field(alias="issuedAt", default=None)
    auth_time: Optional[datetime] = Field(alias="authTime", default=None)
    refresh_token_stored: bool = Field(alias="refreshTokenStored", default=False)
    error: Optional[str] = None


def _timestamp_claim(payload: dict, claim: str) -> Optional[datetime]:
    value = payload.get(claim)
    if not isinstance(value, int):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def _collect_auth_status(config) -> AuthStatusV1Alpha1:
    refresh_token_stored = bool(getattr(config, "refresh_token", None))

    token_str = getattr(config, "token", None)
    if not token_str:
        return AuthStatusV1Alpha1(status="no-token", refresh_token_stored=refresh_token_stored)

    try:
        payload = decode_jwt(token_str)
    except ValueError as e:
        return AuthStatusV1Alpha1(status="invalid-token", error=str(e), refresh_token_stored=refresh_token_stored)

    remaining = get_token_remaining_seconds(token_str)
    if remaining is None:
        status = "no-expiry"
    elif remaining < 0:
        status = "expired"
    elif remaining < TOKEN_EXPIRY_WARNING_SECONDS:
        status = "expiring-soon"
    else:
        status = "valid"

    return AuthStatusV1Alpha1(
        status=status,
        expires_at=_timestamp_claim(payload, "exp"),
        remaining_seconds=remaining,
        subject=payload.get("sub"),
        issuer=payload.get("iss"),
        issued_at=_timestamp_claim(payload, "iat"),
        auth_time=_timestamp_claim(payload, "auth_time"),
        refresh_token_stored=refresh_token_stored,
    )


def _print_token_status(remaining: float) -> None:
    """Print token status message based on remaining time."""
    duration = format_duration(remaining)

    hint = "Run 'jmp login' to refresh your credentials."

    if remaining < 0:
        click.echo(click.style(f"Status: EXPIRED ({duration} ago)", fg="red", bold=True))
        click.echo(click.style(hint, fg="yellow"))
    elif remaining < TOKEN_EXPIRY_WARNING_SECONDS:
        click.echo(click.style(f"Status: EXPIRING SOON ({duration} remaining)", fg="red", bold=True))
        click.echo(click.style(hint, fg="yellow"))
    elif remaining < 3600:
        click.echo(click.style(f"Status: Valid ({duration} remaining)", fg="yellow"))
    else:
        click.echo(click.style(f"Status: Valid ({duration} remaining)", fg="green"))


def _print_subject_issuer(payload: dict) -> None:
    sub = payload.get("sub")
    iss = payload.get("iss")
    if sub:
        click.echo(f"Subject: {sub}")
    if iss:
        click.echo(f"Issuer: {iss}")


def _print_timestamp(label: str, value: int | None) -> None:
    if value is None:
        return
    dt = datetime.fromtimestamp(value, tz=timezone.utc)
    click.echo(f"{label}: {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")


def _print_verbose_details(payload: dict, config) -> None:
    iat = payload.get("iat")
    auth_time = payload.get("auth_time")
    if isinstance(iat, int):
        _print_timestamp("Issued at", iat)
    if isinstance(auth_time, int):
        _print_timestamp("Auth time", auth_time)

    refresh_token = getattr(config, "refresh_token", None)
    click.echo(f"Refresh token stored: {'yes' if refresh_token else 'no'}")


@auth.command(name="status")
@click.option("-v", "--verbose", is_flag=True, help="Show additional token details")
@opt_output_json_yaml
@opt_config(exporter=False)
def token_status(config, verbose: bool, output: DataOutputType):
    """Display token status and expiry information."""
    if output:
        model_print(_collect_auth_status(config), output)
        return

    token_str = getattr(config, "token", None)

    if not token_str:
        click.echo(click.style("No token found in config", fg="yellow"))
        return

    try:
        payload = decode_jwt(token_str)
    except ValueError as e:
        click.echo(click.style(f"Failed to decode token: {e}", fg="red"))
        return

    remaining = get_token_remaining_seconds(token_str)
    if remaining is None:
        click.echo(click.style("Token has no expiry claim", fg="yellow"))
        return

    exp = payload.get("exp")
    exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
    click.echo(f"Token expiry: {exp_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")

    _print_token_status(remaining)

    _print_subject_issuer(payload)

    if verbose:
        _print_verbose_details(payload, config)


@auth.command(name="refresh")
@opt_config(exporter=False)
@blocking
async def refresh_token(config):
    """Refresh the access token using a stored refresh token."""
    refresh_token = getattr(config, "refresh_token", None)
    if not refresh_token:
        raise click.ClickException("No refresh token found. Run 'jmp login --offline-access'.")

    access_token = getattr(config, "token", None)
    if not access_token:
        raise click.ClickException("No access token found. Run 'jmp login --offline-access'.")

    try:
        issuer = decode_jwt_issuer(access_token)
    except Exception as e:
        raise click.ClickException(f"Failed to decode JWT issuer: {e}") from e

    if issuer is None:
        raise click.ClickException("Failed to determine issuer from access token.")

    oidc = Config(issuer=issuer, client_id="jumpstarter-cli")
    tokens = await oidc.refresh_token_grant(refresh_token)
    config.token = tokens["access_token"]
    new_refresh_token = tokens.get("refresh_token")
    if new_refresh_token is not None:
        config.refresh_token = new_refresh_token
    ClientConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]
    click.echo("Access token refreshed.")


@auth.command(name="rotate")
@opt_config(exporter=False)
@blocking
async def rotate_token(config):
    """Rotate the internal token, replacing it with a freshly signed one."""
    token_str = getattr(config, "token", None)
    if not token_str:
        raise click.ClickException("No token found in config.")

    remaining = get_token_remaining_seconds(token_str)
    if remaining is not None and remaining < 0:
        raise click.ClickException(
            "Token is expired. Cannot rotate — recreate the client with 'jmp config client create'."
        )

    new_token = await config.rotate_token()
    if not new_token:
        raise click.ClickException("Token rotation failed: empty token received.")

    try:
        payload = decode_jwt(new_token)
    except ValueError as e:
        raise click.ClickException(f"Token rotation failed: invalid token returned ({e}).") from e

    config.token = new_token
    ClientConfigV1Alpha1.save(config)  # ty: ignore[invalid-argument-type]

    new_remaining = get_token_remaining_seconds(new_token)
    if new_remaining is not None:
        duration = format_duration(new_remaining)
        exp = payload.get("exp")
        if exp:
            exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
            click.echo(f"Token rotated. New expiry: {exp_dt.strftime('%Y-%m-%d %H:%M:%S %Z')} ({duration} remaining)")
        else:
            click.echo(f"Token rotated. {duration} remaining.")
    else:
        click.echo("Token rotated.")
