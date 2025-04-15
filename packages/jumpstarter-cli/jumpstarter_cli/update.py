from datetime import timedelta

import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_config, opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_duration_partial


@click.group()
def update():
    """
    Update a resource
    """


@update.command(name="lease")
@opt_config(exporter=False)
@click.argument("name")
@opt_duration_partial(required=True)
@opt_output_all
@handle_exceptions
async def update_lease(config, name: str, duration: timedelta, output: OutputType):
    """
    Update a lease
    """

    lease = config.update_lease(name, duration)

    match output:
        case OutputMode.JSON | OutputMode.YAML:
            click.echo(lease.dump(output))
        case OutputMode.NAME:
            click.echo(lease.name)
        case _:
            columns = ["NAME", "SELECTOR", "DURATION", "CLIENT", "EXPORTER"]
            rows = [
                {
                    "NAME": lease.name,
                    "SELECTOR": lease.selector,
                    "DURATION": str(lease.duration.total_seconds()),
                    "CLIENT": lease.client,
                    "EXPORTER": lease.exporter,
                }
            ]
            click.echo(make_table(columns, rows))
