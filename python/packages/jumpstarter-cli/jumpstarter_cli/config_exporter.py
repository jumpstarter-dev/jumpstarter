import click
from jumpstarter_cli_common.opt import (
    OutputMode,
    OutputType,
    PathOutputType,
    opt_output_all,
    opt_output_path_only,
)
from jumpstarter_cli_common.print import model_print

from jumpstarter.config.exporter import ExporterConfigV1Alpha1, ObjectMeta

arg_alias = click.argument("alias", default="default")


@click.group("exporter")
def config_exporter():
    """
    Modify jumpstarter exporter config files
    """


@config_exporter.command("create")
@click.option("--namespace", prompt=True)
@click.option("--name", prompt=True)
@click.option("--endpoint", prompt=True)
@click.option("--token", prompt=True)
@opt_output_path_only
@arg_alias
def create_exporter_config(alias, namespace, name, endpoint, token, output: PathOutputType):
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
    path = ExporterConfigV1Alpha1.save(config)

    if output == OutputMode.PATH:
        click.echo(path)


@config_exporter.command("delete")
@arg_alias
@opt_output_path_only
def delete_exporter_config(alias, output: PathOutputType):
    """Delete an exporter config."""
    try:
        ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err
    path = ExporterConfigV1Alpha1.delete(alias)
    if output == OutputMode.PATH:
        click.echo(path)


@config_exporter.command("edit")
@arg_alias
def edit_exporter_config(alias):
    """Edit an exporter config."""
    try:
        config = ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err
    click.edit(filename=config.path)


@config_exporter.command("list")
@opt_output_all
def list_exporter_configs(output: OutputType):
    """List exporter configs."""
    exporters = ExporterConfigV1Alpha1.list()

    model_print(exporters, output)
