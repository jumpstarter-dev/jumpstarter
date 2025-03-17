import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, opt_output_name_only
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_config


@click.group()
def delete():
    """
    Delete resources
    """


@delete.command(name="leases")
@opt_config
@click.argument("name", required=False)
@click.option("--all", "all", is_flag=True)
@opt_output_name_only
@handle_exceptions
def delete_leases(config, name: str, all: bool, output: OutputType):
    """
    Delete leases
    """

    names = []

    if name is not None:
        names.append(name)
    elif all:
        leases = config.list_leases(filter="")
        for lease in leases.leases:
            if lease.client == config.metadata.name:
                names.append(lease.name)
    else:
        raise click.ClickException("One of NAME or --all must be specified")

    for name in names:
        config.delete_lease(name=name)
        match output:
            case OutputMode.NAME:
                click.echo(name)
            case _:
                click.echo('lease "{}" deleted'.format(name))
