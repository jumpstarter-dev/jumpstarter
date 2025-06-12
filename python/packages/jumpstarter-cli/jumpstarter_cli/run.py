import os
from multiprocessing.sharedctypes import Value

import anyio
import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions, leaf_exceptions


def _serve_with_exc_handling(exporter):
    while True:
        result = Value("i", 0)
        pid = os.fork()
        if pid > 0:
            os.waitpid(pid, 0)
            if result.value != 0:
                return result.value
        else:
            try:
                anyio.run(exporter.serve)
            except* Exception as excgroup:
                for exc in leaf_exceptions(excgroup):
                    click.echo(
                        f"Exception while serving on the exporter: {type(exc).__name__}: {exc}",
                        err=True,
                    )
                result.value = 1
            return


@click.command("run")
@opt_config(client=False)
@handle_exceptions
def run(config):
    """Run an exporter locally."""

    return _serve_with_exc_handling(config)
