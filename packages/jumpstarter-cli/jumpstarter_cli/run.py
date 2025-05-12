import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions, leaf_exceptions


async def _serve_with_exc_handling(exporter):
    result = 0
    try:
        await exporter.serve()
    except* Exception as excgroup:
        for exc in leaf_exceptions(excgroup):
            click.echo(
                f"Exception while serving on the exporter: {type(exc).__name__}: {exc}",
                err=True,
            )
        result = 1
    return result


@click.command("run")
@opt_config(client=False)
@handle_exceptions
@blocking
async def run(config):
    """Run an exporter locally."""

    return await _serve_with_exc_handling(config)
