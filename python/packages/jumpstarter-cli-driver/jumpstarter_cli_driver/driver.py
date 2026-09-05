from importlib.metadata import entry_points

import click
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print
from pydantic import BaseModel


class DriverEntry(BaseModel):
    name: str
    type: str
    package: str | None = None
    version: str | None = None

    def rich_add_rows(self, table):
        table.add_row(self.name, self.type)

    def rich_add_names(self, names):
        names.append(self.name)


class DriverEntryList(BaseModel):
    drivers: list[DriverEntry]

    @classmethod
    def rich_add_columns(cls, table):
        table.add_column("NAME", no_wrap=True)
        table.add_column("TYPE")

    def rich_add_rows(self, table):
        for entry in self.drivers:
            entry.rich_add_rows(table)

    def rich_add_names(self, names):
        for entry in self.drivers:
            entry.rich_add_names(names)


@click.command("list")
@opt_output_all
def list_drivers(output: OutputType):
    """List drivers installed in the current environment"""
    drivers = []
    for entry_point in entry_points(group="jumpstarter.drivers"):
        dist = entry_point.dist
        drivers.append(
            DriverEntry(
                name=entry_point.name,
                type=entry_point.value.replace(":", "."),
                package=dist.name if dist else None,
                version=dist.version if dist else None,
            )
        )

    if not drivers and output is None:
        click.echo("No drivers found.")
        return

    model_print(DriverEntryList(drivers=drivers), output)
