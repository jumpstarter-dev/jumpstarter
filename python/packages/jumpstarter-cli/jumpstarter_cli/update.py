from datetime import datetime, timedelta

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_begin_time, opt_duration_partial
from .login import relogin_client


@click.group(cls=AliasedGroup)
def update():
    """
    Update a resource
    """


@update.command(name="lease")
@opt_config(exporter=False)
@click.argument("name")
@opt_duration_partial(required=False)
@opt_begin_time
@click.option(
    "--to-client",
    type=str,
    default=None,
    help="Transfer lease to a different client in the same namespace",
)
@click.option(
    "--share-add",
    multiple=True,
    help="Add a client name to the lease's shared access list (can be specified multiple times)",
)
@click.option(
    "--share-remove",
    multiple=True,
    help="Remove a client name from the lease's shared access list (can be specified multiple times)",
)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def update_lease(
    config,
    name: str,
    duration: timedelta | None,
    begin_time: datetime | None,
    to_client: str | None,
    share_add: tuple[str, ...],
    share_remove: tuple[str, ...],
    output: OutputType,
):
    """
    Update a lease

    Update the duration, begin time, owner, or sharing of an existing lease.
    At least one update option must be specified.

    To transfer a lease to another client in the same namespace, use the --to-client option.
    To share a lease with other clients, use --share-add and --share-remove.

    Updating the begin time of an already active lease is not allowed.
    """

    if duration is None and begin_time is None and to_client is None and not share_add and not share_remove:
        raise click.UsageError(
            "At least one of --duration, --begin-time, --to-client, --share-add, or --share-remove must be specified"
        )

    client_path = f"namespaces/{config.metadata.namespace}/clients/{to_client}" if to_client else None

    lease = config.update_lease(
        name,
        duration=duration,
        begin_time=begin_time,
        client=client_path,
        add_shared_with=list(share_add) if share_add else None,
        remove_shared_with=list(share_remove) if share_remove else None,
    )

    model_print(lease, output)
