from importlib.metadata import entry_points

import asyncclick as click
from jumpstarter_cli_common import make_table


@click.command("list")
async def list_drivers():
    click.echo(
        make_table(
            ["NAME", "TYPE"], [{"NAME": e.name, "TYPE": e.value} for e in entry_points(group="jumpstarter.drivers")]
        )
    )
