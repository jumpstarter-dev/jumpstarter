import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputMode, OutputType, opt_output_name_only

from .common import opt_selector
from .login import relogin_client


@click.group()
def delete():
    """
    Delete resources
    """


@delete.command(name="leases")
@opt_config(exporter=False)
@click.argument("name", required=False)
@opt_selector
@click.option("--all", "all", is_flag=True)
@opt_output_name_only
@handle_exceptions_with_reauthentication(relogin_client)
def delete_leases(config, name: str, selector: str | None, all: bool, output: OutputType):
    """
    Delete leases
    """

    names = []

    if name is not None:
        names.append(name)
    elif selector:
        leases = config.list_leases(filter=selector)
        for lease in leases.leases:
            if lease.client == config.metadata.name:
                names.append(lease.name)
    elif all:
        leases = config.list_leases(filter=None)
        for lease in leases.leases:
            if lease.client == config.metadata.name:
                names.append(lease.name)
    else:
        raise click.ClickException("One of NAME, --selector or --all must be specified")

    for name in names:
        config.delete_lease(name=name)
        match output:
            case OutputMode.NAME:
                click.echo(name)
            case _:
                click.echo('lease "{}" deleted'.format(name))
