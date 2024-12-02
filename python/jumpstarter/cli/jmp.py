import click

from .client import client
from .create import create
from .exporter import exporter
from .get import get
from .import_resource import import_resource
from .lease import lease
from .shell import shell
from .util import AliasedGroup
from .version import version


@click.group(cls=AliasedGroup)
def jmp():
    """The Jumpstarter CLI"""

jmp.add_command(client)
jmp.add_command(exporter)
jmp.add_command(lease)
jmp.add_command(shell)
jmp.add_command(get)
jmp.add_command(create)
jmp.add_command(import_resource)
jmp.add_command(version)

if __name__ == "__main__":
    jmp()
