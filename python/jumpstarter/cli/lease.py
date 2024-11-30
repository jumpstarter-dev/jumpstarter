import click

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)

from .util import AliasedGroup


@click.group(cls=AliasedGroup, short_help="")
def lease():
    """Manage leases held by the current client."""
    pass


@lease.command("list")
@click.argument("name", type=str, default="")
def lease_list(name):
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise ValueError("no client specified")

    for lease in config.list_leases():
        print(lease)


@lease.command("release")
@click.argument("name", type=str, default="")
@click.option("-l", "--lease", "lease", type=str, default="")
@click.option("--all", "all_leases", is_flag=True)
def lease_release(name, lease, all_leases):
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise ValueError("no client specified")

    if all_leases:
        for lease in config.list_leases():
            config.release_lease(lease)
    else:
        if not lease:
            raise ValueError("no lease specified")
        config.release_lease(lease)
