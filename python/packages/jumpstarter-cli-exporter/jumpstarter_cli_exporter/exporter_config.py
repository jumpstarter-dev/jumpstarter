import asyncclick as click
from jumpstarter_cli_common import OutputMode, OutputType, make_table, opt_output_all

from jumpstarter.config.exporter import ExporterConfigListV1Alpha1, ExporterConfigV1Alpha1, ObjectMeta

arg_alias = click.argument("alias", default="default")


@click.command("create-config")
@click.option("--namespace", prompt=True)
@click.option("--name", prompt=True)
@click.option("--endpoint", prompt=True)
@click.option("--token", prompt=True)
@arg_alias
def create_exporter_config(alias, namespace, name, endpoint, token):
    """Create an exporter config."""
    try:
        ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError:
        pass
    else:
        raise click.ClickException(f'exporter "{alias}" exists')

    config = ExporterConfigV1Alpha1(
        alias=alias,
        metadata=ObjectMeta(namespace=namespace, name=name),
        endpoint=endpoint,
        token=token,
    )
    ExporterConfigV1Alpha1.save(config)


@click.command("delete-config")
@arg_alias
def delete_exporter_config(alias):
    """Delete an exporter config."""
    try:
        ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err
    ExporterConfigV1Alpha1.delete(alias)


@click.command("edit-config")
@arg_alias
def edit_exporter_config(alias):
    """Edit an exporter config."""
    try:
        config = ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err
    click.edit(filename=config.path)


@click.command("list-configs")
@opt_output_all
def list_exporter_configs(output: OutputType):
    """List exporter configs."""
    exporters = ExporterConfigV1Alpha1.list()

    if output == OutputMode.JSON:
        click.echo(ExporterConfigListV1Alpha1(items=exporters).dump_json())
    elif output == OutputMode.YAML:
        click.echo(ExporterConfigListV1Alpha1(items=exporters).dump_yaml())
    elif output == OutputMode.NAME:
        if len(exporters) > 0:
            click.echo(exporters[0].alias)
    else:
        columns = ["ALIAS", "PATH"]
        rows = [
            {
                "ALIAS": exporter.alias,
                "PATH": str(exporter.path),
            }
            for exporter in exporters
        ]
        click.echo(make_table(columns, rows))
