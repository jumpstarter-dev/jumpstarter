import logging
import sys
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import AliasedGroup, opt_log_level, version
from jumpstarter_cli_common.exceptions import handle_exceptions

from .client_exporter import list_client_exporters
from .client_lease import client_lease
from .client_login import client_login
from .client_shell import client_shell
from .config import config
from jumpstarter.common.utils import env


@click.group(cls=AliasedGroup)
@opt_log_level
def client(log_level: Optional[str]):
    """Jumpstarter client CLI tool"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


def j():
    with env() as client:

        @handle_exceptions
        def cli():
            client.cli()(standalone_mode=False)

        try:
            cli()
        except click.ClickException as e:
            e.show()
            sys.exit(1)


client.add_command(list_client_exporters)
client.add_command(config)
client.add_command(client_lease)
client.add_command(client_login)
client.add_command(client_shell)
client.add_command(version)

if __name__ == "__main__":
    client()
