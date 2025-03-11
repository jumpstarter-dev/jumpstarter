import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import load_context, opt_context

opt_selector = click.option(
    "-l",
    "--selector",
    help="Selector (label query) to filter on, supports '=', '==', and '!=' (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints.",
)


@click.group()
def get():
    """
    Display one or many resources
    """


@get.command(name="exporters")
@opt_context
@opt_selector
@opt_output_all
@handle_exceptions
def get_exporters(context: str | None, selector: str | None, output: OutputType):
    """
    Display one or many exporters
    """
    config = load_context(context)

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
@opt_context
@opt_selector
@opt_output_all
@handle_exceptions
def get_leases(context: str | None, selector: str | None, output: OutputType):
    """
    Display one or many leases
    """
    config = load_context(context)

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
