import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputMode, OutputType, opt_output_name_only

from .common import opt_selector
from .login import relogin_client


@click.group(cls=AliasedGroup)
def delete():
    """
    Delete resources
    """


@delete.command(name="leases")
@opt_config(exporter=False)
@click.argument("names", nargs=-1)
@opt_selector
@click.option("-a", "--all", "delete_all", is_flag=True, help="Delete all your active leases")
@click.option("-A", "--all-clients", "all_clients", is_flag=True, help="Delete active leases from all clients")
@opt_output_name_only
@handle_exceptions_with_reauthentication(relogin_client)
def delete_leases(
    config, names: tuple[str, ...], selector: str | None,
    delete_all: bool, all_clients: bool, output: OutputType,
):
    """
    Delete leases
    """

    to_delete = []

    if names:
        to_delete.extend(names)
    elif selector:
        leases = config.list_leases(filter=selector)
        leases = leases.filter_by_selector(selector)
        if not all_clients:
            leases = leases.filter_by_client(config.metadata.name)
        to_delete.extend(lease.name for lease in leases.leases)
    elif delete_all or all_clients:
        leases = config.list_leases(filter=None)
        if not all_clients:
            leases = leases.filter_by_client(config.metadata.name)
        to_delete.extend(lease.name for lease in leases.leases)
    else:
        raise click.ClickException("One of NAMES, --selector, --all or --all-clients must be specified")

    for name in to_delete:
        config.delete_lease(name=name)
        match output:
            case OutputMode.NAME:
                click.echo(name)
            case _:
                click.echo('lease "{}" deleted'.format(name))
