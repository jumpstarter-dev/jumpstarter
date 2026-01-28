from datetime import datetime, timezone

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

from jumpstarter.config.client import ClientConfigV1Alpha1


@click.group()
def auth():
    """Authentication and token management commands."""


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
@click.option("--verbose", is_flag=True, help="Show additional token details")
@opt_config(exporter=False)
def token_status(config, verbose: bool):
    """Display token status and expiry information."""
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
