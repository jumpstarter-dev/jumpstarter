import sys
from datetime import timedelta

import click
from anyio.from_thread import start_blocking_portal
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_duration_partial, opt_selector
from jumpstarter.client import log_client_from_path
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


@click.command("shell")
@opt_config()
@click.argument("command", nargs=-1)
# client specific
# TODO: warn if these are specified with exporter config
@click.option("--lease", "lease_name")
@opt_selector
@opt_duration_partial(default=timedelta(minutes=30), show_default="00:30:00")
# end client specific
@handle_exceptions
def shell(config, command: tuple[str, ...], lease_name, selector, duration):
    """
    Spawns a shell (or custom command) connecting to a local or remote exporter

    COMMAND is the custom command to run instead of shell.

    Example:

    .. code-block:: bash

        $ jmp shell --exporter foo -- python bar.py
    """

    match config:
        case ClientConfigV1Alpha1():
            exit_code = 0

            with (
                start_blocking_portal() as portal,
                portal.wrap_async_context_manager(
                    config.lease_async(portal=portal, selector=selector, lease_name=lease_name, duration=duration)
                ) as lease,
                portal.wrap_async_context_manager(lease.monitor_async()),
                portal.wrap_async_context_manager(lease.serve_unix_async()) as path,
                portal.wrap_async_context_manager(log_client_from_path(path=path, portal=portal)) as log,
                log.log_stream(),
            ):
                exit_code = launch_shell(
                    path,
                    "remote",
                    config.drivers.allow,
                    config.drivers.unsafe,
                    command=command,
                )

            sys.exit(exit_code)

        case ExporterConfigV1Alpha1():
            exit_code = 0

            with config.serve_unix() as path:
                # SAFETY: the exporter config is local thus considered trusted
                exit_code = launch_shell(
                    path,
                    "local",
                    allow=[],
                    unsafe=True,
                    command=command,
                )

            sys.exit(exit_code)
