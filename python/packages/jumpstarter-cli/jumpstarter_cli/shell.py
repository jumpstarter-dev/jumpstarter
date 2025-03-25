import sys
from datetime import timedelta

import asyncclick as click
from jumpstarter_cli_common import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_duration_partial, opt_selector
from jumpstarter.common.utils import launch_shell
from jumpstarter.config import ClientConfigV1Alpha1, ExporterConfigV1Alpha1


@click.command("shell")
@opt_config()
# client specific
# TODO: warn if these are specified with exporter config
@click.option("--lease", "lease_name")
@opt_selector
@opt_duration_partial(default=timedelta(minutes=30), show_default="00:30:00")
# end client specific
@handle_exceptions
def shell(config, lease_name, selector, duration):
    """
    Spawns a shell connecting to a local or remote exporter
    """

    match config:
        case ClientConfigV1Alpha1():
            exit_code = 0

            with config.lease(selector=selector, lease_name=lease_name, duration=duration) as lease:
                with lease.serve_unix() as path:
                    with lease.monitor():
                        exit_code = launch_shell(path, "remote", config.drivers.allow, config.drivers.unsafe)

            sys.exit(exit_code)

        case ExporterConfigV1Alpha1():
            with config.serve_unix() as path:
                # SAFETY: the exporter config is local thus considered trusted
                launch_shell(path, "local", allow=[], unsafe=True)
