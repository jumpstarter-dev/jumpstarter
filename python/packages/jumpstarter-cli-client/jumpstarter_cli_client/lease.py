import asyncclick as click
from jumpstarter.common import MetadataFilter
from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)
from jumpstarter_cli_common import AliasedGroup


@click.group(cls=AliasedGroup, short_help="")
def lease():
    """Manage leases held by the current client"""
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

@lease.command("request")
@click.option("-l", "--label", "labels", type=(str, str), multiple=True)
@click.argument("name", type=str, default="")
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
    try:
        if name:
            config = ClientConfigV1Alpha1.load(name)
        else:
            config = UserConfigV1Alpha1.load_or_create().config.current_client
        if not config:
            raise ValueError("No client specified")
        lease = config.request_lease(metadata_filter=MetadataFilter(labels=dict(labels)))
        print(lease.name)
    except ValueError as e:
        raise click.ClickException(str(e)) from e
    except Exception as e:
        raise e
