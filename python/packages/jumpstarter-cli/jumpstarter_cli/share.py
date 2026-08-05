import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .login import relogin_client


@click.group(cls=AliasedGroup)
def share():
    """Manage lease sharing with other clients"""


@share.command(name="add")
@opt_config(exporter=False)
@click.argument("lease")
@click.argument("clients", nargs=-1, required=True)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def share_add(config, lease: str, clients: tuple[str, ...], output: OutputType):
    """Grant clients access to a shared lease.

    Example:

    \b
        $ jmp share add my-lease client-alice client-bob
    """
    updated = config.update_lease(lease, add_shared_with=list(clients))
    model_print(updated, output)
    click.echo(
        f"Shared lease {lease} with: {', '.join(clients)}",
        err=True,
    )


@share.command(name="remove")
@opt_config(exporter=False)
@click.argument("lease")
@click.argument("clients", nargs=-1, required=True)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def share_remove(config, lease: str, clients: tuple[str, ...], output: OutputType):
    """Revoke clients' access to a shared lease.

    Example:

    \b
        $ jmp share remove my-lease client-alice
    """
    updated = config.update_lease(lease, remove_shared_with=list(clients))
    model_print(updated, output)
    click.echo(
        f"Removed from lease {lease}: {', '.join(clients)}",
        err=True,
    )


@share.command(name="list")
@opt_config(exporter=False)
@click.argument("lease")
@handle_exceptions_with_reauthentication(relogin_client)
def share_list(config, lease: str):
    """List clients with shared access to a lease.

    Example:

    \b
        $ jmp share list my-lease
    """
    target = config.get_lease(lease)

    if not target.shared_with:
        click.echo(f"Lease {lease} is not shared with anyone.")
        return

    click.echo(f"Lease {lease} shared with:")
    for client_name in target.shared_with:
        click.echo(f"  {client_name}")
