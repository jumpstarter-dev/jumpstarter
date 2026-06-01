import click
from jumpstarter_cli_common.opt import (
    OutputMode,
    OutputType,
    PathOutputType,
    opt_output_all,
    opt_output_path_only,
)
from jumpstarter_cli_common.print import model_print

from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.config.exporter import ExporterConfigV1Alpha1, ObjectMeta


def _validate_alias_param(ctx, param, value):
    try:
        ExporterConfigV1Alpha1.validate_alias(value)
    except ConfigurationError as e:
        raise click.BadParameter(str(e)) from e
    return value


arg_alias = click.argument("alias", default="default", callback=_validate_alias_param)


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
    # Guard against overwriting an existing user-level config (the write target).
    # A same-named config in the system location (/etc) is allowed to be shadowed.
    if ExporterConfigV1Alpha1.user_config_exists(alias):
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
    try:
        path = ExporterConfigV1Alpha1.delete(alias)
    except ConfigurationError as err:
        raise click.ClickException(str(err)) from err
    if output == OutputMode.PATH:
        click.echo(path)
    if ExporterConfigV1Alpha1.exists(alias):
        click.echo(
            f"Warning: {path} deleted, but a system config at "
            f"{ExporterConfigV1Alpha1.resolve_path(alias)} still exists and will now be used.",
            err=True,
        )


@config_exporter.command("edit")
@arg_alias
def edit_exporter_config(alias):
    """Edit an exporter config."""
    try:
        config = ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err
    click.edit(filename=str(config.path))


@config_exporter.command("list")
@opt_output_all
def list_exporter_configs(output: OutputType):
    """List exporter configs."""
    exporters = ExporterConfigV1Alpha1.list()

    model_print(exporters, output)
