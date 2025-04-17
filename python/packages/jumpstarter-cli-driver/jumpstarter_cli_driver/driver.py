from importlib.metadata import entry_points

import asyncclick as click
from jumpstarter_cli_common.table import make_table


@click.command("list")
async def list_drivers():
    drivers = list(entry_points(group="jumpstarter.drivers"))
    if not drivers:
        click.echo("No drivers found.")
    else:
        click.echo(make_table(["NAME", "TYPE"], [{"NAME": e.name, "TYPE": e.value.replace(":", ".")} for e in drivers]))
