from datetime import datetime, timedelta

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_begin_time, opt_duration_partial, opt_exporter_name, opt_selector
from .login import relogin_client


@click.group(cls=AliasedGroup)
def create():
    """
    Create a resource
    """


@create.command(name="lease")
@opt_config(exporter=False)
@opt_selector
@opt_exporter_name
@opt_duration_partial(required=True)
@opt_begin_time
@click.option(
    "--lease-id",
    type=str,
    default=None,
    help="Optional lease ID to request (if not provided, server will generate one)",
)
@click.option(
    "--tag",
    "tags",
    multiple=True,
    help="Tag to set on the lease (key=value format, can be specified multiple times)",
)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def create_lease(
    config,
    selector: str | None,
    exporter_name: str | None,
    duration: timedelta,
    begin_time: datetime | None,
    lease_id: str | None,
    tags: tuple[str, ...],
    output: OutputType,
):
    """
    Create a lease

    Request an exporter lease from the jumpstarter controller.

    The result of this command will be a lease ID that can be used to
    connect to the remote exporter.

    This is useful for multi-step workflows where you want to hold a lease
    for a specific exporter while performing multiple operations, or for
    CI environments where one step will request the lease and other steps
    will perform operations on the leased exporter.

    Example:

    .. code-block:: bash

        $ JMP_LEASE=$(jmp create lease -l foo=bar --duration 1d --output name)
        $ jmp shell
        $$ j --help
        $$ exit
        $ jmp delete lease "${JMP_LEASE}"

    You can also specify a unique custom lease ID:

    .. code-block:: bash

        $ jmp create lease -l foo=bar --duration 1d --lease-id my-custom-lease-id

    """

    if not selector and not exporter_name:
        raise click.UsageError("one of --selector/-l or --name/-n is required")

    parsed_tags = {}
    for tag in tags:
        if "=" not in tag:
            raise click.UsageError(f"Invalid tag format: {tag!r} (expected key=value)")
        k, v = tag.split("=", 1)
        parsed_tags[k] = v

    lease = config.create_lease(
        selector=selector,
        exporter_name=exporter_name,
        duration=duration,
        begin_time=begin_time,
        lease_id=lease_id,
        tags=parsed_tags or None,
    )

    model_print(lease, output)
