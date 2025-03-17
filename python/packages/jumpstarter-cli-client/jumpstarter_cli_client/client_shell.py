import sys

import asyncclick as click
from jumpstarter_cli_common.exceptions import handle_exceptions

from .common import opt_config, opt_selector_simple
from jumpstarter.common.utils import launch_shell


@click.command("shell", short_help="Spawns a shell connecting to a leased remote exporter")
@click.option("-n", "--lease", "lease_name", type=str)
@opt_config
@opt_selector_simple
@handle_exceptions
def client_shell(config, selector: str, lease_name):
    """Spawns a shell connecting to a leased remote exporter"""

    exit_code = 0

    with config.lease(selector=selector, lease_name=lease_name) as lease:
        with lease.serve_unix() as path:
            with lease.monitor():
                exit_code = launch_shell(path, "remote", config.drivers.allow, config.drivers.unsafe)

    sys.exit(exit_code)
