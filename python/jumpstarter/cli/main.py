"""Main Jumpstarter CLI"""

import click

from .client import client
from .shell import shell
from .version import version
from .control import control_from_env


@click.group(context_settings={"help_option_names": ["-h", "--help"]}, no_args_is_help=True)
def main():
    pass


main.add_command(version)
main.add_command(shell)
main.add_command(client)
main.add_command(control_from_env(), "control")
