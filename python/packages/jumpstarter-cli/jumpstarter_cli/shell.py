import sys
from datetime import timedelta

import anyio
import click
from anyio import create_task_group, get_cancelled_exc_class
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.signal import signal_handler

from .common import opt_duration_partial, opt_selector
from .login import relogin_client
from jumpstarter.common.utils import launch_shell
from jumpstarter.config.client import ClientConfigV1Alpha1
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


def _run_shell_with_lease(lease, exporter_logs, config, command):
    """Run shell with lease context managers."""
    def launch_remote_shell(path: str) -> int:
        return launch_shell(
            path, lease.exporter_name, config.drivers.allow, config.drivers.unsafe,
            config.shell.use_profiles, command=command
        )

    with lease.serve_unix() as path:
        with lease.monitor():
            if exporter_logs:
                with lease.connect() as client:
                    with client.log_stream():
                        return launch_remote_shell(path)
            else:
                return launch_remote_shell(path)


async def _shell_with_signal_handling(config, selector, lease_name, duration, exporter_logs, command):
    """Handle lease acquisition and shell execution with signal handling."""
    exit_code = 0
    cancelled_exc_class = get_cancelled_exc_class()

    try:
        async with create_task_group() as tg:
            tg.start_soon(signal_handler, tg.cancel_scope)
            try:
                try:
                    async with anyio.from_thread.BlockingPortal() as portal:
                        async with config.lease_async(selector, lease_name, duration, portal) as lease:
                            exit_code = await anyio.to_thread.run_sync(
                                _run_shell_with_lease, lease, exporter_logs, config, command
                            )
                except BaseExceptionGroup as eg:
                    for exc in eg.exceptions:
                        if isinstance(exc, TimeoutError):
                            raise exc from None
                    raise
                except cancelled_exc_class:
                    exit_code = 2
            finally:
                if not tg.cancel_scope.cancel_called:
                    tg.cancel_scope.cancel()
    except* TimeoutError:
        exit_code = 1

    return exit_code


@click.command("shell")
@opt_config()
@click.argument("command", nargs=-1)
# client specific
# TODO: warn if these are specified with exporter config
@click.option("--lease", "lease_name")
@opt_selector
@opt_duration_partial(default=timedelta(minutes=30), show_default="00:30:00")
@click.option("--exporter-logs", is_flag=True, help="Enable exporter log streaming")
# end client specific
@handle_exceptions_with_reauthentication(relogin_client)
def shell(config, command: tuple[str, ...], lease_name, selector, duration, exporter_logs):
    """
    Spawns a shell (or custom command) connecting to a local or remote exporter

    COMMAND is the custom command to run instead of shell.

    Example:

    .. code-block:: bash

        $ jmp shell --exporter foo -- python bar.py
    """

    match config:
        case ClientConfigV1Alpha1():
            exit_code = anyio.run(
                _shell_with_signal_handling, config, selector, lease_name, duration, exporter_logs, command
            )
            sys.exit(exit_code)

        case ExporterConfigV1Alpha1():
            with config.serve_unix() as path:
                # SAFETY: the exporter config is local thus considered trusted
                launch_shell(
                    path,
                    "local",
                    allow=[],
                    unsafe=True,
                    use_profiles=False,
                    command=command,
                )
