import asyncclick as click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions
from jumpstarter_cli_common.opt import OutputMode, OutputType, opt_output_all
from jumpstarter_cli_common.table import make_table

from .common import opt_selector


@click.group()
def get():
    """
    Display one or many resources
    """


@get.command(name="exporters")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@handle_exceptions
def get_exporters(config, selector: str | None, output: OutputType):
    """
    Display one or many exporters
    """

    exporters = config.list_exporters(filter=selector)

    match output:
        case OutputMode.JSON:
            click.echo(exporters.dump_json())
        case OutputMode.YAML:
            click.echo(exporters.dump_yaml())
        case OutputMode.NAME:
            for exporter in exporters.exporters:
                click.echo(exporter.name)
        case _:
            columns = ["NAME", "LABELS"]
            rows = [
                {
                    "NAME": exporter.name,
                    "LABELS": ",".join(("{}={}".format(i[0], i[1]) for i in exporter.labels.items())),
                }
                for exporter in exporters.exporters
            ]
            click.echo(make_table(columns, rows))


@get.command(name="leases")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@handle_exceptions
def get_leases(config, selector: str | None, output: OutputType):
    """
    Display one or many leases
    """

    leases = config.list_leases(filter=selector)

    match output:
        case OutputMode.JSON:
            click.echo(leases.dump_json())
        case OutputMode.YAML:
            click.echo(leases.dump_yaml())
        case OutputMode.NAME:
            for lease in leases.leases:
                click.echo(lease.name)
        case _:
            columns = ["NAME", "CLIENT", "EXPORTER"]
            rows = [
                {
                    "NAME": lease.name,
                    "CLIENT": lease.client,
                    "EXPORTER": lease.exporter,
                }
                for lease in leases.leases
            ]
            click.echo(make_table(columns, rows))
