import time
from datetime import datetime, timezone

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.oidc import decode_jwt


@click.group()
def auth():
    """
    Authentication and token management commands
    """


@auth.command(name="status")
@opt_config(exporter=False)
def token_status(config):
    """
    Display token status and expiry information
    """
    token_str = getattr(config, "token", None)

    if not token_str:
        click.echo(click.style("No token found in config", fg="yellow"))
        return

    try:
        payload = decode_jwt(token_str)
    except Exception as e:
        click.echo(click.style(f"Failed to decode token: {e}", fg="red"))
        return

    exp = payload.get("exp")
    if not exp:
        click.echo(click.style("Token has no expiry claim", fg="yellow"))
        return

    remaining = exp - time.time()
    exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)

    click.echo(f"Token expiry: {exp_dt.strftime('%Y-%m-%d %H:%M:%S')}")

    if remaining < 0:
        hours = int(abs(remaining) / 3600)
        mins = int((abs(remaining) % 3600) / 60)
        click.echo(click.style(f"Status: EXPIRED ({hours}h {mins}m ago)", fg="red", bold=True))
        click.echo(click.style("Run 'jmp login' to refresh your credentials.", fg="yellow"))
    elif remaining < 300:  # Less than 5 minutes
        mins = int(remaining / 60)
        secs = int(remaining % 60)
        click.echo(click.style(f"Status: EXPIRING SOON ({mins}m {secs}s remaining)", fg="red", bold=True))
        click.echo(click.style("Run 'jmp login' to refresh your credentials.", fg="yellow"))
    elif remaining < 3600:  # Less than 1 hour
        mins = int(remaining / 60)
        click.echo(click.style(f"Status: Valid ({mins}m remaining)", fg="yellow"))
    else:
        hours = int(remaining / 3600)
        mins = int((remaining % 3600) / 60)
        click.echo(click.style(f"Status: Valid ({hours}h {mins}m remaining)", fg="green"))

    # Show additional token info
    if payload.get("sub"):
        click.echo(f"Subject: {payload.get('sub')}")
    if payload.get("iss"):
        click.echo(f"Issuer: {payload.get('iss')}")
