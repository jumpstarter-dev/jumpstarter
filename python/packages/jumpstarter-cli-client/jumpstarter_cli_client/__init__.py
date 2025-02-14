import logging
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import AliasedGroup, opt_log_level, version

from .client_config import create_client_config, delete_client_config, list_client_configs, use_client_config
from .client_login import client_login
from .client_shell import client_shell
from .lease import lease
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
        client.cli()(standalone_mode=False)


client.add_command(create_client_config)
client.add_command(delete_client_config)
client.add_command(list_client_configs)
client.add_command(use_client_config)
client.add_command(lease)
client.add_command(client_login)
client.add_command(client_shell)
client.add_command(version)

if __name__ == "__main__":
    client()
