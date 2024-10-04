"""Main Jumpstarter CLI"""

import logging

import click

from .client import client
from .exporter import exporter
from .version import version


@click.command(short_help="Show this message and exit.")
def help():
    """Display the Jumpstarter help information"""
    ctx = click.get_current_context()
    # Print out help information for root
    click.echo(ctx.parent.get_help())
    ctx.exit()


@click.group(no_args_is_help=True)
def main():
    logging.basicConfig(level=logging.INFO)


main.add_command(version)
main.add_command(exporter)
main.add_command(client)
