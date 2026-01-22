import click

from .config_client import config_client
from .config_exporter import config_exporter


@click.group
def config():
    """
    Manage local configurations
    """
    pass


config.add_command(config_client)
config.add_command(config_exporter)
