from datetime import datetime, timedelta

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_begin_time, opt_duration_partial
from .login import relogin_client


@click.group()
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
    "--client",
    type=str,
    default=None,
    help="Transfer lease to a different client (format: namespaces/{namespace}/clients/{client})",
)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def update_lease(
    config, name: str, duration: timedelta | None, begin_time: datetime | None, client: str | None, output: OutputType
):
    """
    Update a lease

    Update the duration, begin time, and/or owner of an existing lease.
    At least one of --duration, --begin-time, or --client must be specified.

    To transfer a lease to another client, use the --client option with the
    full client identifier (e.g., namespaces/default/clients/other-client).

    Updating the begin time of an already active lease is not allowed.
    """

    if duration is None and begin_time is None and client is None:
        raise click.UsageError("At least one of --duration, --begin-time, or --client must be specified")

    lease = config.update_lease(name, duration=duration, begin_time=begin_time, client=client)

    model_print(lease, output)
