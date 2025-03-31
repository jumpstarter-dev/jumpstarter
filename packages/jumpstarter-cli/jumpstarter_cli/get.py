import asyncclick as click
from jumpstarter_cli_common import OutputType, make_table, opt_config, opt_output_auto
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_selector
from jumpstarter.client.grpc import ExporterList, LeaseList


@click.group()
def get():
    """
    Display one or many resources
    """


@get.command(name="exporters")
@opt_config(exporter=False)
@opt_selector
@opt_output_auto(ExporterList)
@handle_exceptions
def get_exporters(config, selector: str | None, output: OutputType):
    """
    Display one or many exporters
    """

    exporters = config.list_exporters(filter=selector)

    if output:
        click.echo(exporters.dump(output))
    else:
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
@opt_output_auto(LeaseList)
@handle_exceptions
def get_leases(config, selector: str | None, output: OutputType):
    """
    Display one or many leases
    """

    leases = config.list_leases(filter=selector)

    if output:
        click.echo(leases.dump(output))
    else:
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
