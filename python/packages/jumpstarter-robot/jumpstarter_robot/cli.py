"""Click CLI for the client resource generator, ``jmp-robot``.

The module imports only Click; the commands load the generator when they run.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

SOURCES = ("exporter_config", "selector", "lease")


def _one_of(options: dict[str, Any], names: tuple[str, ...]) -> None:
    given = [name for name in names if options[name]]
    if len(given) > 1:
        flags = " and ".join("--" + name.replace("_", "-") for name in given)
        raise click.UsageError(f"{flags} cannot be used together")


def _run(command: Callable[[argparse.Namespace], int], **options: Any) -> None:
    """Run a generator command with the parsed options and exit with its code."""
    from .__main__ import ERRORS, EXIT_ERROR

    try:
        code = command(argparse.Namespace(**options))
    except ERRORS as e:
        click.echo(f"error: {e}", err=True)
        code = EXIT_ERROR
    click.get_current_context().exit(code)


@click.group("robot")
def robot():
    """Robot Framework client resources for Jumpstarter exporters."""


@robot.command("generate")
@click.option(
    "--project", type=click.Path(path_type=Path), help="Project file (default: nearest robot.toml / pyproject.toml)."
)
@click.option("--client", multiple=True, help="Client (table name) to generate; repeatable.")
@click.option("--live", is_flag=True, help="Read the drivers from the running exporter: the `jmp shell`, else a lease.")
@click.option("--exporter-config", help="Exporter config path or alias.")
@click.option("--selector", help="Lease by label selector.")
@click.option("--lease", help="Attach to an existing lease.")
@click.option("--config", help="Client config alias, path or 'env'.")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Resource to write.")
@click.option("--session", help="Session name (default: the client name, or 'default').")
@click.option("--alias", multiple=True, metavar="PATH=PREFIX", help="Keyword prefix for a driver path; repeatable.")
@click.option("--roles", is_flag=True, default=None, help="Role libraries for identical subtrees.")
@click.option("--include", multiple=True, help="Only nodes matching this glob; repeatable.")
@click.option("--exclude", multiple=True, help="Skip nodes matching this glob; repeatable.")
def generate(**options: Any) -> None:
    """Generate client resources.

    With no options, regenerates every client of the nearest robot.toml (or
    pyproject.toml) [tool.jumpstarter.client]. A client's drivers are read
    from its exporter config, offline. A client with only a selector or lease,
    or any client with --live, is read from the running exporter instead.
    """
    from .__main__ import cmd_generate

    _one_of(options, SOURCES)
    lists = ("client", "alias", "include", "exclude")
    _run(cmd_generate, **{k: list(v) if k in lists else v for k, v in options.items()})
