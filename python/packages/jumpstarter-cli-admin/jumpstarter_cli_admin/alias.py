import copy

import click


def add_aliases(group: click.Group, command: click.Command, *aliases: str):
    """Let a command of a group be called by other names, without listing it again in the help."""
    for alias in aliases:
        hidden = copy.copy(command)
        hidden.hidden = True
        group.add_command(hidden, alias)
