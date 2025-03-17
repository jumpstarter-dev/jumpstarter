from datetime import timedelta

import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import DURATION, load_context, opt_context


@click.group()
def update():
    """
    Update a resource
    """


@update.command(name="lease")
@opt_context
@click.argument("name")
@click.option("--duration", "duration", type=DURATION, required=True)
@opt_output_all
@handle_exceptions
async def update_lease(context: str | None, name: str, duration: timedelta, output: OutputType):
    """
    Update a lease
    """

    config = load_context(context)

    lease = config.update_lease(name, duration)

    match output:
        case OutputMode.JSON:
            click.echo(lease.dump_json())
        case OutputMode.YAML:
            click.echo(lease.dump_yaml())
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
