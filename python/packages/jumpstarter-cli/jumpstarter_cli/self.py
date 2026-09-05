import click

from .self_update import self_update


@click.group
def self():
    """
    Manage the jumpstarter executables
    """
    pass


self.add_command(self_update)
