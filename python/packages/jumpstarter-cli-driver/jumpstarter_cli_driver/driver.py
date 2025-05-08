from importlib.metadata import entry_points

import click
from rich.console import Console
from rich.table import Table


@click.command("list")
def list_drivers():
    drivers = list(entry_points(group="jumpstarter.drivers"))
    if not drivers:
        click.echo("No drivers found.")
    else:
        table = Table(
            box=None,
            header_style=None,
            pad_edge=None,
        )

        table.add_column("NAME")
        table.add_column("TYPE")

        for driver in drivers:
            table.add_row(
                driver.name,
                driver.value.replace(":", "."),
            )

        Console().print(table)
