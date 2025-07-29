from datetime import timedelta

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_duration_partial, opt_selector
from .login import relogin_client


@click.group()
def create():
    """
    Create a resource
    """


@create.command(name="lease")
@opt_config(exporter=False)
@opt_selector
@opt_duration_partial(required=True)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def create_lease(config, selector: str, duration: timedelta, output: OutputType):
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

    """

    lease = config.create_lease(selector=selector, duration=duration)

    model_print(lease, output)
