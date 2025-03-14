import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_labels, opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)


@click.command("list-exporters", short_help="List available exporters.")
@click.argument("name", type=str, default="")
@opt_labels
@opt_output_all
@handle_exceptions
def list_client_exporters(name: str | None, labels: list[(str, str)], output: OutputType):
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise click.BadParameter(
            "no client specified, and no default client set: specify a client name, or use jmp client config use",
            param_hint="name",
        )

    exporters = config.list_exporters(filter=",".join("{}={}".format(i[0], i[1]) for i in labels))

    if output == OutputMode.JSON:
        click.echo(exporters.dump_json())
    elif output == OutputMode.YAML:
        click.echo(exporters.dump_yaml())
    elif output == OutputMode.NAME:
        for exporter in exporters.exporters:
            click.echo(exporter.name)
    else:
        columns = ["NAME", "LABELS"]

        def make_row(exporter):
            return {
                "NAME": exporter.name,
                "LABELS": ",".join(("{}={}".format(i[0], i[1]) for i in exporter.labels.items())),
            }

        rows = list(map(make_row, exporters.exporters))
        click.echo(make_table(columns, rows))
