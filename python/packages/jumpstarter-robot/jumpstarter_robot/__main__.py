"""The client resource generator behind ``jmp-robot`` and ``python -m jumpstarter_robot``.

The command line itself is the Click group in ``jumpstarter_robot.cli``.

``generate`` with no arguments regenerates every client of the nearest
``robot.toml`` (or ``pyproject.toml``) ``[tool.jumpstarter.client]``. A
client's tree comes from its ``exporter-config``, read offline. A client with
only a ``selector`` or ``lease``, or any client with ``--live``, is read from
the running exporter instead: the ``jmp shell`` the command runs in, else a
lease.

Exit codes: 0 success, 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import sessions
from .project import ClientSettings, Project, ProjectError, config_value, find_project, token_warnings
from .render import Plan, RenderError, write_resource
from .sources import SourceError, from_exporter_config, from_report
from .tree import DeviceTree
from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.config.env import JUMPSTARTER_HOST

EXIT_OK = 0
EXIT_ERROR = 2

SOURCE_FLAGS = ("exporter_config", "selector", "lease")


class UsageError(ValueError):
    """The command line asks for something that cannot be done."""


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---- choosing clients ----


def _one_off(args: argparse.Namespace) -> bool:
    """Whether the command line names its own source."""
    return any(getattr(args, flag) for flag in SOURCE_FLAGS)


def _source_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """An explicit source on the command line replaces the client's sources."""
    if not _one_off(args):
        return {}
    exporter_config = config_value(args.exporter_config, Path.cwd()) if args.exporter_config else None
    return {"exporter_config": exporter_config, "selector": args.selector, "lease": args.lease}


def _option_overrides(settings: ClientSettings, args: argparse.Namespace) -> dict[str, Any]:
    options = {
        "config": config_value(args.config, Path.cwd()) if args.config else None,
        "resource": args.output.resolve() if args.output else None,
        "roles": args.roles,
        "aliases": {**settings.aliases, **_aliases(args.alias)} if args.alias else None,
        "include": list(args.include) or None,
        "exclude": list(args.exclude) or None,
        "session_name": args.session,
    }
    return {key: value for key, value in options.items() if value is not None}


def _overrides(settings: ClientSettings, args: argparse.Namespace) -> ClientSettings:
    """Apply command line options over a client's settings."""
    return replace(settings, **_source_overrides(args), **_option_overrides(settings, args))


def _aliases(pairs: Sequence[str]) -> dict[str, str]:
    aliases = {}
    for pair in pairs:
        path, sep, prefix = pair.partition("=")
        if not sep or not path.strip():
            raise UsageError(f"--alias takes PATH=PREFIX, not '{pair}'")
        aliases[path.strip()] = prefix.strip()
    return aliases


def _load_project(args: argparse.Namespace) -> Project | None:
    if args.project is not None:
        return Project.load(args.project)
    found = find_project()
    return Project.load(found) if found is not None else None


def select_clients(args: argparse.Namespace, project: Project | None) -> list[ClientSettings]:
    """The clients this run generates, with command line overrides applied."""
    cwd = Path.cwd()
    if args.client:
        chosen = []
        for name in args.client:
            if project is not None and name in project.clients:
                chosen.append(project.client(name))
            elif _one_off(args):
                chosen.append(ClientSettings(name=name, base_dir=cwd, project=project.path if project else None))
            else:
                where = project.path.name if project else "no robot.toml was found"
                raise UsageError(f"unknown client '{name}' ({where}); give a source such as --exporter-config")
    elif _one_off(args) or ((project is None or not project.clients) and _in_shell(args)):
        # a source on the command line, or the shell's exporter, as a client of this folder
        name = args.session or sessions.DEFAULT_SESSION
        chosen = [ClientSettings(name=name, base_dir=cwd, resource=cwd / "client.resource")]
    elif project is None or not project.clients:
        raise UsageError(
            "nothing to generate: no robot.toml / pyproject.toml with [tool.jumpstarter.client.<name>] tables "
            "was found; add one, or give a source such as --exporter-config exporter.yaml"
        )
    else:
        chosen = list(project.clients.values())
    clients = [_overrides(settings, args) for settings in chosen]
    names = ", ".join(settings.name for settings in clients)
    if len(clients) > 1 and _in_shell(args):
        raise UsageError(f"inside `jmp shell` there is one exporter: name its client with --client ({names})")
    given = [flag for flag, value in (("--output", args.output), ("--session", args.session)) if value]
    if len(clients) > 1 and given:
        raise UsageError(f"{' and '.join(given)} apply to one client: name it with --client ({names})")
    return clients


def _in_shell(args: argparse.Namespace) -> bool:
    """Whether this run reads the exporter of the ``jmp shell`` it runs in."""
    return bool(args.live and os.environ.get(JUMPSTARTER_HOST))


# ---- trees ----


@contextmanager
def open_live(settings: ClientSettings):
    """Connect to the client's exporter: the ``jmp shell``, a lease, or its exporter config served locally."""
    name = settings.session
    if os.environ.get(JUMPSTARTER_HOST):
        session = sessions.open_shell(name)
    elif settings.selector or settings.lease:
        config = settings.config
        session = sessions.open_lease(
            name,
            config=str(config) if config is not None else None,
            config_dir=settings.base_dir,
            selector=settings.selector,
            lease_name=settings.lease,
        )
    elif settings.exporter_config:
        session = sessions.open_local(name, config=str(settings.exporter_config), config_dir=settings.base_dir)
    else:
        raise UsageError(f"client '{name}' has no selector, lease or exporter config to connect to")
    try:
        yield session
    finally:
        session.close()


def live_tree(settings: ClientSettings) -> DeviceTree:
    """The tree of the running exporter."""
    with open_live(settings) as session:
        return from_report(session.client, device_name=settings.device_name, ref=session.exporter or None)


def static_tree(settings: ClientSettings) -> DeviceTree:
    """The tree from the client's exporter config, read offline."""
    if settings.exporter_config is None:
        raise UsageError(f"client '{settings.name}' has no source: set exporter-config, selector or lease")
    return from_exporter_config(settings.exporter_config, base_dir=settings.base_dir)


# ---- writing ----


def emit(settings: ClientSettings, tree: DeviceTree) -> Plan:
    for warning in [*tree.warnings, *token_warnings(settings)]:
        _err(f"warning: {warning}")
    resource, planned = write_resource(
        settings.resource_path,
        tree,
        session=settings.session,
        source_args=settings.source_args(),
        aliases=settings.aliases,
        roles=settings.roles,
        include=settings.include,
        exclude=settings.exclude,
    )
    count = len(planned.imports)
    print(f"{settings.name}: wrote {_shown(resource)} ({count} driver libraries)")
    return planned


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


# ---- commands ----


def cmd_generate(args: argparse.Namespace) -> int:
    for settings in select_clients(args, _load_project(args)):
        live = args.live or settings.exporter_config is None
        emit(settings, live_tree(settings) if live else static_tree(settings))
    return EXIT_OK


# Reported as one line: configuration, leasing and driver import problems.
ERRORS = (
    UsageError,
    ProjectError,
    SourceError,
    RenderError,
    sessions.NoSession,
    JumpstarterException,
    ImportError,
    OSError,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``jmp-robot`` with ``argv`` and return its exit code."""
    import click

    from .cli import robot

    try:
        return robot.main(list(argv) if argv is not None else None, prog_name="jmp-robot", standalone_mode=False) or 0
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.Abort:
        _err("Aborted!")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
