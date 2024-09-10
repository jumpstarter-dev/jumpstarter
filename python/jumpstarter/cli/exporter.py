import anyio
import click

from jumpstarter.config.exporter import ExporterConfigV1Alpha1


@click.command
@click.argument("name", type=str, default="default")
def exporter(name):
    try:
        exporter = ExporterConfigV1Alpha1.load(name)
    except FileNotFoundError as e:
        raise click.ClickException(f"exporter config with name {name} not found: {e}") from e

    anyio.run(exporter.serve)
