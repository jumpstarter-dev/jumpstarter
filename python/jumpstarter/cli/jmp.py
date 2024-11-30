import click

from .client import client
from .exporter import exporter
from .lease import lease
from .shell import shell
from .util import AliasedGroup, opt_log_level
from .version import version


@click.group(cls=AliasedGroup, short_help="The Jumpstarter client CLI.")
@opt_log_level
def jmp():
    """The Jumpstarter CLI."""
    pass

jmp.add_command(client)
jmp.add_command(exporter)
jmp.add_command(lease)
jmp.add_command(shell)
jmp.add_command(version)

if __name__ == "__main__":
    jmp()
