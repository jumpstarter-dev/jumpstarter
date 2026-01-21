from datetime import datetime, timezone

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.oidc import (
    TOKEN_EXPIRY_WARNING_SECONDS,
    decode_jwt,
    format_duration,
    get_token_remaining_seconds,
)


@click.group()
def auth():
    """Authentication and token management commands."""


def _print_token_status(remaining: float) -> None:
    """Print token status message based on remaining time."""
    duration = format_duration(remaining)

    if remaining < 0:
        click.echo(click.style(f"Status: EXPIRED ({duration} ago)", fg="red", bold=True))
        click.echo(click.style("Run 'jmp login --force' to refresh your credentials.", fg="yellow"))
    elif remaining < TOKEN_EXPIRY_WARNING_SECONDS:
        click.echo(click.style(f"Status: EXPIRING SOON ({duration} remaining)", fg="red", bold=True))
        click.echo(click.style("Run 'jmp login --force' to refresh your credentials.", fg="yellow"))
    elif remaining < 3600:
        click.echo(click.style(f"Status: Valid ({duration} remaining)", fg="yellow"))
    else:
        click.echo(click.style(f"Status: Valid ({duration} remaining)", fg="green"))


@auth.command(name="status")
@opt_config(exporter=False)
def token_status(config):
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

    # Show additional token info
    sub = payload.get("sub")
    iss = payload.get("iss")
    if sub:
        click.echo(f"Subject: {sub}")
    if iss:
        click.echo(f"Issuer: {iss}")
