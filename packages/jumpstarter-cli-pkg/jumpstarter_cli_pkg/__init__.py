import asyncclick as click
from jumpstarter_cli_common import AliasedGroup

from .commands.list import list
from .commands.show import show


@click.group(cls=AliasedGroup)
def pkg():
    """Jumpstarter package management CLI tool"""


pkg.add_command(list)
pkg.add_command(show)

if __name__ == "__main__":
    pkg()
