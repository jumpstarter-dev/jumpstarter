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
@click.option(
    "--allow-disabled",
    is_flag=True,
    default=False,
    help="Allow leasing a disabled exporter (only effective with --name/-n)",
)
@click.option(
    "--context",
    "context_entries",
    multiple=True,
    help="Context metadata for the lease (key=value format, can be specified multiple times). "
    "Used for observability correlation (e.g. build_id=abc123, image_digest=sha256:...).",
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
    allow_disabled: bool,
    context_entries: tuple[str, ...],
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

    Attach context metadata for observability correlation:

    .. code-block:: bash

        $ jmp create lease -l foo=bar --duration 1h \\
            --context build_id=nightly-42 --context image_digest=sha256:abc

    """

    if not selector and not exporter_name:
        raise click.UsageError("one of --selector/-l or --name/-n is required")

    parsed_tags = {}
    for tag in tags:
        if "=" not in tag:
            raise click.UsageError(f"Invalid tag format: {tag!r} (expected key=value)")
        k, v = tag.split("=", 1)
        parsed_tags[k] = v

    parsed_context = {}
    for entry in context_entries:
        if "=" not in entry:
            raise click.UsageError(f"Invalid context format: {entry!r} (expected key=value)")
        k, v = entry.split("=", 1)
        if len(k) > 32:
            raise click.UsageError(f"Context key too long: {k!r} (max 32 characters)")
        if len(v) > 64:
            raise click.UsageError(f"Context value too long for key {k!r} (max 64 characters)")
        parsed_context[k] = v

    if len(parsed_context) > 8:
        raise click.UsageError("Too many context entries (max 8)")

    lease = config.create_lease(
        selector=selector,
        exporter_name=exporter_name,
        duration=duration,
        begin_time=begin_time,
        lease_id=lease_id,
        tags=parsed_tags or None,
        allow_disabled=allow_disabled,
        context=parsed_context or None,
    )

    model_print(lease, output)
