"""Click CLI for the client resource generator: ``jmp robot``, ``jmp-robot``.

``jmp`` adds this group when ``jumpstarter-robot`` is installed. The module
imports only Click, so ``jmp`` starts as fast as before; the commands load the
generator when they run.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

MODES = ("capture", "check", "live")
SOURCES = ("exporter_config", "selector", "lease", "tree", "exporter_class")


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
    """Robot Framework client resources for Jumpstarter exporters.

    Exit codes: 0 success, 1 drift (generate --check), 2 usage or configuration error.
    """


@robot.command("generate")
@click.option(
    "--project", type=click.Path(path_type=Path), help="Project file (default: nearest robot.toml / pyproject.toml)."
)
@click.option("--client", multiple=True, help="Client (table name) to generate; repeatable.")
@click.option("--capture", is_flag=True, help="Record the client's nodes from a live exporter.")
@click.option("--check", is_flag=True, help="Compare the committed nodes with a live exporter; exit 1 on drift.")
@click.option("--live", is_flag=True, help="Generate from a live exporter without writing nodes.")
@click.option(
    "--from", "from_", type=click.Path(path_type=Path), help="Use a `jmp describe lease --drivers -o json` dump."
)
@click.option("--exporter-config", help="Exporter config path or alias.")
@click.option("--selector", help="Lease by label selector.")
@click.option("--lease", help="Attach to an existing lease.")
@click.option("--tree", type=click.Path(path_type=Path), help="Device tree JSON.")
@click.option("--exporter-class", help="ExporterClass YAML (FILE or FILE#name).")
@click.option(
    "--driver-interface-dir", multiple=True, type=click.Path(path_type=Path), help="DriverInterface directory."
)
@click.option("--config", help="Client config alias, path or 'env'.")
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Resource to write.")
@click.option("--session", help="Session name (default: the client name, or 'default').")
@click.option("--alias", multiple=True, metavar="PATH=PREFIX", help="Keyword prefix for a driver path; repeatable.")
@click.option("--roles", is_flag=True, default=None, help="Role libraries for identical subtrees.")
@click.option("--include", multiple=True, help="Only nodes matching this glob; repeatable.")
@click.option("--exclude", multiple=True, help="Skip nodes matching this glob; repeatable.")
@click.option(
    "--libspec", type=click.Path(path_type=Path), metavar="DIR", help="Also write a libspec per driver library."
)
def generate(**options: Any) -> None:
    """Generate client resources.

    With no options, regenerates every client of the nearest robot.toml (or
    pyproject.toml) [tool.jumpstarter.client]. Each client's drivers come from
    its committed nodes table, its exporter config, its tree JSON or its
    ExporterClass. A client with only a selector or lease needs --capture or
    --live.

    --capture leases the exporter (or uses the `jmp shell` it runs in, or a
    --from dump), writes the nodes table and regenerates. --check compares the
    committed nodes with the live tree and exits 1 on drift, for CI.
    """
    from .__main__ import cmd_generate

    _one_of(options, MODES)
    _one_of(options, SOURCES)
    lists = ("client", "driver_interface_dir", "alias", "include", "exclude")
    _run(cmd_generate, **{k: list(v) if k in lists else v for k, v in options.items()})


@robot.command("render")
@click.option("--session", default="default", show_default=True, help="Session name.")
@click.option("--roles", is_flag=True, help="Role libraries for identical subtrees.")
def render(**options: Any) -> None:
    """Render a device tree JSON from stdin to a resource on stdout.

    For other codegen frontends that hand over a jumpstarter.dev/device-tree.
    """
    from .__main__ import cmd_render

    _run(cmd_render, **options)
