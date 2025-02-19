import sys
import traceback
from pathlib import Path

import asyncclick as click

from jumpstarter.common.utils import launch_shell
from jumpstarter.config.exporter import ExporterConfigV1Alpha1

arg_alias = click.argument("alias", default="default")

opt_config_path = click.option(
    "-c", "--config", "config_path", type=click.Path(exists=True), help="Path of exporter config, overrides ALIAS"
)


async def _serve_with_exc_handling(exporter):
    result = 0
    try:
        await exporter.serve()
    except* Exception as excgroup:
        print(f"Exception while serving on the exporter: {excgroup.exceptions}", file=sys.stderr)
        for exc in excgroup.exceptions:
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stderr)
        result = 1
    return result


@click.command("run")
@arg_alias
@opt_config_path
async def run_exporter(alias, config_path):
    """Run an exporter locally."""
    try:
        if config_path:
            config = ExporterConfigV1Alpha1.load_path(Path(config_path))
        else:
            config = ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err

    return await _serve_with_exc_handling(config)


@click.command("shell")
@arg_alias
@opt_config_path
def exporter_shell(alias, config_path):
    """Spawns a shell connecting to a transient exporter"""
    try:
        if config_path:
            config = ExporterConfigV1Alpha1.load_path(Path(config_path))
        else:
            config = ExporterConfigV1Alpha1.load(alias)
    except FileNotFoundError as err:
        raise click.ClickException(f'exporter "{alias}" does not exist') from err

    with config.serve_unix() as path:
        # SAFETY: the exporter config is local thus considered trusted
        launch_shell(path, "local", allow=[], unsafe=True)
