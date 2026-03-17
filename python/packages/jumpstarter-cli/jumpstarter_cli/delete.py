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
@click.option("-a", "--all", "all", is_flag=True)
@opt_output_name_only
@handle_exceptions_with_reauthentication(relogin_client)
def delete_leases(config, names: tuple[str, ...], selector: str | None, all: bool, output: OutputType):
    """
    Delete leases
    """

    resolved_names = list(names)

    if resolved_names:
        pass
    elif selector:
        leases = config.list_leases(filter=selector)
        leases = leases.filter_by_selector(selector)
        for lease in leases.leases:
            if lease.client == config.metadata.name:
                resolved_names.append(lease.name)
    elif all:
        leases = config.list_leases(filter=None)
        for lease in leases.leases:
            if lease.client == config.metadata.name:
                resolved_names.append(lease.name)
    else:
        raise click.ClickException("One of NAME(S), --selector or --all must be specified")

    for name in resolved_names:
        config.delete_lease(name=name)
        match output:
            case OutputMode.NAME:
                click.echo(name)
            case _:
                click.echo('lease "{}" deleted'.format(name))
