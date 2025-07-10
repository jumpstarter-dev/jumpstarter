from datetime import timedelta

import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_duration_partial
from .login import relogin_client


@click.group()
def update():
    """
    Update a resource
    """


@update.command(name="lease")
@opt_config(exporter=False)
@click.argument("name")
@opt_duration_partial(required=True)
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def update_lease(config, name: str, duration: timedelta, output: OutputType):
    """
    Update a lease
    """

    lease = config.update_lease(name, duration)

    model_print(lease, output)
