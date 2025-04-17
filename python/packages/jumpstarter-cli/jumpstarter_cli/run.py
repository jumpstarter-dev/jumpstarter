import sys
import traceback

import asyncclick as click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions


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
@opt_config(client=False)
@handle_exceptions
async def run(config):
    """Run an exporter locally."""

    return await _serve_with_exc_handling(config)
