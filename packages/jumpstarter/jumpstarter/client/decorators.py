"""
Client-side Click group helpers for building driver CLIs.
"""

from typing import TYPE_CHECKING, Any, Callable

import click

if TYPE_CHECKING:
    from jumpstarter.client import DriverClient


def driver_click_group(client: "DriverClient", **kwargs: Any) -> Callable:
    """
    Decorator factory for multi-command driver groups.

    Allows server-side description override, otherwise uses Click's default behavior.

    Usage:
        def cli(self):
            @driver_click_group(self)
            def base():
                '''Generic power interface'''  # ← Click uses this by default
                pass

            @base.command()
            def on():
                '''Power on'''
                self.on()

            return base

    :param client: DriverClient instance (provides description and methods_description)
    :param kwargs: Keyword arguments passed to DriverClickGroup
    :return: Decorator that creates a DriverClickGroup
    """
    def decorator(f: Callable) -> DriverClickGroup:
        # Use function docstring if no help= provided
        if 'help' not in kwargs or kwargs['help'] is None:
            if f.__doc__:
                kwargs['help'] = f.__doc__.strip()

        # Server description overrides Click defaults
        if getattr(client, 'description', None):
            kwargs['help'] = client.description

        group = DriverClickGroup(client, name=f.__name__, callback=f, **kwargs)

        # Transfer Click parameters attached by decorators like @click.option
        group.params = getattr(f, '__click_params__', [])

        return group

    return decorator


def driver_click_command(client: "DriverClient", **kwargs: Any) -> Callable:
    """
    Decorator factory for single-command drivers (e.g., SSH, TMT).

    Allows server-side description override, otherwise uses Click's default behavior.

    Usage:
        def cli(self):
            @driver_click_command(self)
            @click.argument("args", nargs=-1)
            def ssh(args):
                '''Run SSH command'''  # ← Click uses this by default
                ...
            return ssh

    :param client: DriverClient instance (provides description field)
    :param kwargs: Keyword arguments passed to click.command
    :return: click.command decorator
    """
    # Server description overrides Click's defaults (help= parameter or docstring)
    if getattr(client, 'description', None):
        kwargs['help'] = client.description

    return click.command(**kwargs)


class DriverClickGroup(click.Group):
    """Click Group with server-configurable help text via methods_description."""

    def __init__(self, client: "DriverClient", *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.client = client

    def command(self, *args: Any, **kwargs: Any) -> Callable:
        """Command decorator with server methods_description override support."""
        def decorator(f: Callable) -> click.Command:
            name = kwargs.get('name')
            if not name:
                name = f.__name__.lower().replace('_', '-')

            if name in self.client.methods_description:
                kwargs['help'] = self.client.methods_description[name]

            return super(DriverClickGroup, self).command(*args, **kwargs)(f)

        return decorator
