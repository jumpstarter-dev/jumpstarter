from datetime import timedelta

import asyncclick as click
from jumpstarter_cli_common import OutputType, echo, make_table, opt_config, opt_output_auto
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_duration_partial
from jumpstarter.client.grpc import Lease


@click.group()
def update():
    """
    Update a resource
    """


@update.command(name="lease")
@opt_config(exporter=False)
@click.argument("name")
@opt_duration_partial(required=True)
@opt_output_auto(Lease)
@handle_exceptions
async def update_lease(config, name: str, duration: timedelta, output: OutputType):
    """
    Update a lease
    """

    lease = config.update_lease(name, duration)

    if output:
        echo(lease.dump(output))
    else:
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
