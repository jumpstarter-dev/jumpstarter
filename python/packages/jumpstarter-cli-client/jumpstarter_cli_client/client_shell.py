import sys

import asyncclick as click
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.common import MetadataFilter
from jumpstarter.common.utils import launch_shell
from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)


@click.command("shell", short_help="Spawns a shell connecting to a leased remote exporter")
@click.argument("name", type=str, default="")
@click.option("-l", "--label", "labels", type=(str, str), multiple=True)
@click.option("-n", "--lease", "lease_name", type=str)
@handle_exceptions
def client_shell(name: str, labels, lease_name):
    """Spawns a shell connecting to a leased remote exporter"""
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
       raise click.BadParameter("no client specified, and no default client set:" +
                                 "specify a client name, or use jmp client use-config ", param_hint="name")


    exit_code = 0
    with config.lease(metadata_filter=MetadataFilter(labels=dict(labels)), lease_name=lease_name) as lease:
        with lease.serve_unix() as path:
            exit_code = launch_shell(path, "remote", config.drivers.allow, config.drivers.unsafe)

    sys.exit(exit_code)
