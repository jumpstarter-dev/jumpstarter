import asyncclick as click
from jumpstarter_cli_common import AliasedGroup
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.common import MetadataFilter
from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)


@click.group(name="lease", cls=AliasedGroup, short_help="")
def client_lease():
    """Manage leases held by the current client"""
    pass


@client_lease.command("list")
@click.argument("name", type=str, default="")
@handle_exceptions
def lease_list(name):
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise ValueError("no client specified")

    for lease in config.list_leases():
        print(lease)


@client_lease.command("release")
@click.argument("name", type=str, default="")
@click.option("-l", "--lease", "lease", type=str, default="")
@click.option("--all", "all_leases", is_flag=True)
@handle_exceptions
def lease_release(name, lease, all_leases):
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise click.BadParameter("no client specified, and no default client set:" +
                                 "specify a client name, or use jmp client use-config ", param_hint="name")

    if all_leases:
        for lease in config.list_leases():
            config.release_lease(lease)
    else:
        if not lease:
            raise click.BadParameter("no lease specified, provide one or use --all to release all leases",
                                    param_hint="lease")
        config.release_lease(lease)


@client_lease.command("request")
@click.option("-l", "--label", "labels", type=(str, str), multiple=True)
@click.argument("name", type=str, default="")
@handle_exceptions
def lease_request(name, labels):
    """Request an exporter lease from the jumpstarter controller.

    The result of this command will be a lease ID that can be used to
    connect to the remote exporter.

    This is useful for multi-step workflows where you want to hold a lease
    for a specific exporter while performing multiple operations, or for
    CI environments where one step will request the lease and other steps
    will perform operations on the leased exporter.

    Example:

    .. code-block:: bash

        $ JMP_LEASE=$(jmp lease request -l label match)
        $ jmp shell
        $$ j --help
        $$ exit
        $ jmp lease release -l "${JMP_LEASE}"

    """
    if name:
        config = ClientConfigV1Alpha1.load(name)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise click.BadParameter("no client specified, and no default client set:" +
                                 "specify a client name, or use jmp client use-config ", param_hint="name")
    lease = config.request_lease(metadata_filter=MetadataFilter(labels=dict(labels)))
    print(lease.name)

