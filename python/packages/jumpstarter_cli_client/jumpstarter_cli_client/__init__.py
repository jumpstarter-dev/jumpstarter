import asyncclick as click
from jumpstarter_cli_common import AliasedGroup, version

from jumpstarter.common.utils import env

from .client_config import create_client_config, delete_client_config, list_client_configs, use_client_config
from .client_shell import client_shell
from .lease import lease


@click.group(cls=AliasedGroup)
def client():
    """Jumpstarter client CLI tool"""

def j():
    with env() as client:
        client.cli()(standalone_mode=False)


client.add_command(create_client_config)
client.add_command(delete_client_config)
client.add_command(list_client_configs)
client.add_command(use_client_config)
client.add_command(lease)
client.add_command(client_shell)
client.add_command(version)

if __name__ == "__main__":
    client()
