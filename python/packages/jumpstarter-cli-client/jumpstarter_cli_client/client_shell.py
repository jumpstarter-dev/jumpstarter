import sys

import asyncclick as click

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
def client_shell(name: str, labels, lease_name):
    """Spawns a shell connecting to a leased remote exporter"""
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise ValueError("no client specified")

    exit_code = 0
    with config.lease(metadata_filter=MetadataFilter(labels=dict(labels)), lease_name=lease_name) as lease:
        with lease.serve_unix() as path:
            exit_code = launch_shell(path, "remote", config.drivers.allow, config.drivers.unsafe)

    sys.exit(exit_code)
