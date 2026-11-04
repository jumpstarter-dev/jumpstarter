"""The client resource generator behind ``jmp robot``, ``jmp-robot`` and ``python -m jumpstarter_robot``.

The command line itself is the Click group in ``jumpstarter_robot.cli``.

``generate`` with no arguments regenerates every client of the nearest
``robot.toml`` (or ``pyproject.toml``) ``[tool.jumpstarter.client]``. Each
client's tree comes from, in order: its committed ``nodes`` table, its
``exporter-config``, its ``tree`` JSON or its ``exporter-class`` (which a
``nodes`` table refines rather than replaces). A client
with only a ``selector`` or ``lease`` needs ``--capture`` (record its nodes
from a live lease) or ``--live``.

``--capture`` leases the exporter (or uses the ``jmp shell`` the command runs
in, for one client, or a ``--from describe.json`` dump), writes the ``nodes``
table and regenerates. ``--check`` compares the committed ``nodes`` with the live tree
and exits 1 on drift, for CI.

``render`` reads a device tree JSON on stdin and writes the resource to
stdout, for other codegen frontends.

Exit codes: 0 success, 1 drift (``--check``), 2 usage or configuration error.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import sessions
from .naming import session_variable
from .project import ClientSettings, Project, ProjectError, config_value, find_project, token_warnings, write_nodes
from .render import Plan, RenderError, render_resource, write_resource
from .sources import (
    SourceError,
    from_describe,
    from_exporter_class,
    from_exporter_config,
    from_report,
    from_toml_nodes,
    from_tree_json,
    to_toml_nodes,
)
from .tree import DeviceTree, TreeError, infer_root_shape
from jumpstarter.client.selectors import parse_label_selector
from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.config.env import JUMPSTARTER_HOST
from jumpstarter.utils.env import ExporterMetadata

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2

SOURCE_FLAGS = ("exporter_config", "selector", "lease", "tree", "exporter_class")


class UsageError(ValueError):
    """The command line asks for something that cannot be done."""


def _err(message: str) -> None:
    print(message, file=sys.stderr)


# ---- choosing clients ----


def _one_off(args: argparse.Namespace) -> bool:
    """Whether the command line names its own source (a ``--from`` dump applies to the chosen clients)."""
    return any(getattr(args, flag) for flag in SOURCE_FLAGS)


def _source_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """An explicit source on the command line replaces the client's sources (and, to generate, its nodes)."""
    if not any(getattr(args, flag) for flag in SOURCE_FLAGS):
        return {}
    values: dict[str, Any] = dict.fromkeys(("exporter_config", "selector", "lease", "tree", "exporter_class"))
    if not (args.capture or args.check):
        values["nodes"] = None
    if args.exporter_config:
        values["exporter_config"] = config_value(args.exporter_config, Path.cwd())
    values["selector"], values["lease"] = args.selector, args.lease
    if args.tree:
        values["tree"] = args.tree.resolve()
    if args.exporter_class:
        path, sep, name = args.exporter_class.partition("#")
        values["exporter_class"] = str(Path(path).resolve()) + (f"#{name}" if sep else "")
    return values


def _option_overrides(settings: ClientSettings, args: argparse.Namespace) -> dict[str, Any]:
    options = {
        "driver_interface_dirs": [d.resolve() for d in args.driver_interface_dir] or None,
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
            elif _one_off(args) or args.capture or args.from_ is not None:
                chosen.append(ClientSettings(name=name, base_dir=cwd, project=project.path if project else None))
            else:
                where = project.path.name if project else "no robot.toml was found"
                raise UsageError(f"unknown client '{name}' ({where}); give a source such as --exporter-config")
        return [_overrides(settings, args) for settings in chosen]
    if _one_off(args) or (args.from_ is not None and (project is None or not project.clients)):
        name = args.session or sessions.DEFAULT_SESSION
        settings = ClientSettings(name=name, base_dir=cwd, resource=cwd / "client.resource")
        return [_overrides(settings, args)]
    if project is not None and args.from_ is not None and len(project.clients) > 1:
        raise UsageError(f"--from describes one exporter: name its client with --client ({', '.join(project.clients)})")
    in_shell = _reads_shell(args)
    if project is None or not project.clients:
        if in_shell and (args.capture or args.live):
            # record the shell's exporter as a new client of this folder
            name = args.session or sessions.DEFAULT_SESSION
            settings = ClientSettings(name=name, base_dir=cwd, resource=cwd / "client.resource")
            return [_overrides(settings, args)]
        raise UsageError(
            "nothing to generate: no robot.toml / pyproject.toml with [tool.jumpstarter.client.<name>] tables "
            "was found; add one, or give a source such as --exporter-config exporter.yaml"
        )
    if in_shell and len(project.clients) > 1:
        return [_overrides(_shell_client(project), args)]
    return [_overrides(settings, args) for settings in project.clients.values()]


def _reads_shell(args: argparse.Namespace) -> bool:
    """Whether this run reads the exporter of the ``jmp shell`` it runs in."""
    reading = args.capture or args.check or args.live
    return bool(reading and args.from_ is None and os.environ.get(JUMPSTARTER_HOST))


_SELECTOR_PARTS = re.compile(r",(?![^()]*\))")
_SET_OPERATOR = re.compile(r"\s+(in|notin)\s*\(")  # the controller also reads `key in(a)`


def _selector_matches(selector: str, labels: Mapping[str, str]) -> bool:
    """Whether exporter ``labels`` satisfy a label ``selector``, as the controller decides.

    Every requirement must hold, several on one key included. A selector with
    a requirement this parser cannot read (``key>1``, ``key=(a)``, an empty
    one), or with none, matches nothing rather than skipping it: the
    controller rejects such a selector, so no lease of this exporter has it.
    """
    requirements: list[tuple[str, str, list[str]]] = []
    for part in (part.strip() for part in _SELECTOR_PARTS.split(selector.strip())):
        match_labels, expressions = parse_label_selector(_SET_OPERATOR.sub(r" \1 (", part))
        parsed = [(key, "=", [value]) for key, value in match_labels.items()] + expressions
        if len(parsed) != 1:
            return False
        requirements += parsed
    for key, operator, values in requirements:
        present, value = key in labels, labels.get(key)
        if not {
            "=": present and value in values,
            "in": present and value in values,
            "notin": not present or value not in values,
            "!=": not present or value not in values,
            "exists": present,
            "!exists": not present,
        }.get(operator, False):
            return False
    return True


def _shell_client(project: Project) -> ClientSettings:
    """The client of ``project`` whose exporter the ``jmp shell`` holds: by its lease, else by its selector.

    The lease names this very exporter, so a client of the shell's lease wins
    over clients whose selector merely matches its labels. Labels the shell
    could not fetch are unknown, not empty, so no selector matches them.
    """
    shell = ExporterMetadata.from_env()
    clients = list(project.clients.values())
    matches = [settings for settings in clients if shell.lease and settings.lease == shell.lease]
    if not matches and shell.labels:
        matches = [s for s in clients if s.selector and _selector_matches(s.selector, shell.labels)]
    if len(matches) == 1:
        return matches[0]
    if shell.labels or shell.lease or shell.name:
        labels = ",".join(f"{k}={v}" for k, v in sorted(shell.labels.items())) or "no labels"
        held = f"exporter {shell.name or '?'} ({labels}), lease {shell.lease or '?'}"
    else:
        held = "a local exporter (no lease or labels to match)"
    names = ", ".join(s.name for s in matches) if matches else ", ".join(project.clients)
    found = "several clients match it" if matches else "no client's selector or lease matches it"
    raise UsageError(f"the `jmp shell` holds {held}; {found}: name its client with --client ({names})")


def _check_single_exporter(args: argparse.Namespace, clients: Sequence[ClientSettings]) -> None:
    """Inside ``jmp shell`` every client would reach the shell's one exporter, whatever its own source."""
    if len(clients) > 1 and _reads_shell(args):
        names = ", ".join(settings.name for settings in clients)
        raise UsageError(f"inside `jmp shell` there is one exporter: name its client with --client ({names})")


# ---- trees ----


def _has_live_source(settings: ClientSettings) -> bool:
    return bool(os.environ.get(JUMPSTARTER_HOST) or settings.selector or settings.lease or settings.exporter_config)


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


def live_tree(settings: ClientSettings, describe: Path | None = None) -> DeviceTree:
    """The tree of the running exporter (or of a ``jmp describe`` dump)."""
    if describe is not None:
        return from_describe(describe, device_name=settings.device_name)
    with open_live(settings) as session:
        tree = from_report(session.client, device_name=settings.device_name, ref=session.exporter or None)
    return tree


def static_tree(settings: ClientSettings) -> DeviceTree:
    """The tree from the client's committed settings, by precedence.

    With an ExporterClass, a committed ``nodes`` table refines it instead of
    replacing it: the client classes, shell methods and optional flags it
    lists apply to the ExporterClass's nodes, and nodes the class does not
    declare are added.
    """
    if settings.nodes is not None and settings.exporter_class is not None:
        return _exporter_class_tree(settings)
    if settings.nodes is not None:
        return from_toml_nodes(settings.nodes, device_name=settings.device_name, ref=settings.nodes_ref())
    if settings.exporter_config is not None:
        return from_exporter_config(settings.exporter_config, base_dir=settings.base_dir)
    if settings.tree is not None:
        return from_tree_json(settings.tree)
    if settings.exporter_class is not None:
        return from_exporter_class(
            settings.exporter_class, settings.driver_interface_dirs, client_name=settings.name
        )
    if settings.selector or settings.lease:
        raise UsageError(
            f"client '{settings.name}' has only a {'selector' if settings.selector else 'lease'}: "
            f"record its drivers with `jmp robot generate --capture --client {settings.name}` "
            "(or generate from the live exporter with --live)"
        )
    raise UsageError(f"client '{settings.name}' has no source: set nodes, exporter-config, tree or exporter-class")


def _exporter_class_tree(settings: ClientSettings) -> DeviceTree:
    committed = from_toml_nodes(settings.nodes or {}, device_name=settings.device_name, ref=settings.nodes_ref())
    clients = {node.dotted: node.client for node in committed.walk() if node.client}
    tree = from_exporter_class(
        str(settings.exporter_class), settings.driver_interface_dirs, clients=clients, client_name=settings.name
    )
    for node in committed.walk():
        found = tree.find(node.path)
        if found is None:
            parent = tree.find(node.path[:-1]) if len(node.path) > 1 else None
            (parent.children if parent is not None else tree.nodes).append(deepcopy(node))
        elif node.client:
            found.methods = list(node.methods) or found.methods
            found.required = found.required and node.required
    tree.root_shape, tree.root_entry = infer_root_shape(tree.nodes)
    return tree


# ---- writing ----


def emit(settings: ClientSettings, tree: DeviceTree, libspec: Path | None) -> Plan:
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
    if libspec is not None:
        write_libspecs(planned, settings.session, libspec)
    return planned


def _shown(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def write_libspecs(planned: Plan, session: str, directory: Path) -> list[Path]:
    """Run libdoc for every Driver import, so tools that read libspecs see the keywords."""
    from robot.libdoc import libdoc

    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for item in planned.imports:
        target = directory / f"{item.alias}.libspec"
        name = "::".join(["jumpstarter_robot.Driver", *item.arguments(session)])
        if libdoc(name, str(target), name=item.alias, format="LIBSPEC", quiet=True):
            raise RenderError(f"libdoc failed for {item.alias}")
        written.append(target)
    return written


def capture(settings: ClientSettings, project: Project | None, describe: Path | None, libspec: Path | None) -> int:
    tree = live_tree(settings, describe)
    mapping = to_toml_nodes(tree)
    if describe is not None and settings.nodes is not None:
        mapping = _keep_methods(mapping, settings.nodes)
    target = settings.project or (project.path if project else Path.cwd() / "robot.toml")
    initial = {
        "selector": settings.selector,
        "lease": settings.lease,
        "exporter-config": _relative(settings.exporter_config, target.parent),
        "config": _relative(settings.config, target.parent),
        "resource": _relative(settings.resource_path, target.parent),
    }
    write_nodes(target, settings.name, mapping, settings=initial)
    print(f"{settings.name}: recorded {len(mapping)} nodes in {_shown(target)}")
    if not (settings.selector or settings.lease or settings.exporter_config):
        variable = session_variable("JMP_SELECTOR", settings.session)
        print(
            f"{settings.name}: it has no selector; for runs outside `jmp shell` to find your lease or "
            "for `Acquire Lease` to get one, add "
            f'selector = "..." to [tool.jumpstarter.client.{settings.name}] or pass -v {variable}:...'
        )
    captured = replace(settings, nodes=mapping, project=target)
    for warning in tree.warnings:
        _err(f"warning: {warning}")
    emit(captured, from_toml_nodes(mapping, device_name=captured.device_name, ref=captured.nodes_ref()), libspec)
    return EXIT_OK


def _keep_methods(mapping: dict[str, Any], nodes: dict[str, Any]) -> dict[str, Any]:
    """A ``jmp describe`` dump has no shell methods: keep the committed ones of nodes whose class is unchanged."""
    committed = to_toml_nodes(from_toml_nodes(nodes))
    kept = dict(mapping)
    for path, value in mapping.items():
        want = committed.get(path)
        if want is not None and _methods_of(want) and _client_of(want) == _client_of(value):
            extra = {} if isinstance(value, str) else dict(value)
            kept[path] = {**extra, "client": _client_of(value), "methods": _methods_of(want)}
    return kept


def _relative(value: Any, base: Path) -> Any:
    if isinstance(value, Path):
        try:
            return value.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return str(value)
    return value


# ---- checking ----


def _node_drift(
    path: str, want: Any, have: dict[str, Any], required: bool, unknown: set[str], methods: bool = True
) -> tuple[str, str]:
    """('drift' | 'note' | '', message) for one committed node; ``methods=False`` leaves shell methods unchecked."""
    expected = _client_of(want)
    if path in unknown:
        return "note", f"{path}: its client is not installed here, so its class ({expected}) cannot be checked"
    if path not in have:
        if required:
            return "drift", f"missing required node {path} ({expected})"
        return "note", f"optional node {path} ({expected}) is not present"
    actual = have[path]
    if _client_of(actual) != expected:
        return "drift", f"changed class of {path}: committed {expected}, live {_client_of(actual)}"
    if methods and _methods_of(actual) != _methods_of(want):
        return "drift", f"changed shell methods of {path}: committed {_methods_of(want)}, live {_methods_of(actual)}"
    return "", ""


def drift(committed: DeviceTree, live: DeviceTree) -> tuple[list[str], list[str]]:
    """Differences between committed nodes and a live tree: (drift, notes).

    A ``jmp describe`` dump carries no shell methods, so they are only
    compared against a connected exporter.
    """
    want, have = to_toml_nodes(committed), to_toml_nodes(live)
    required = {n.dotted: n.required for n in committed.walk()}
    unknown = {n.dotted for n in live.walk() if n.kind == "opaque"}
    methods = live.source.get("kind") != "describe"
    problems, notes = [], []
    for path, value in want.items():
        kind, message = _node_drift(path, value, have, required.get(path, True), unknown, methods)
        if kind:
            (problems if kind == "drift" else notes).append(message)
    problems += [f"new node {path} ({_client_of(value)})" for path, value in have.items() if path not in want]
    problems += _interface_drift(committed, live)
    return problems, notes


def _interface_drift(committed: DeviceTree, live: DeviceTree) -> list[str]:
    live_interfaces = {n.dotted: n.interface for n in live.walk() if n.interface}
    return [
        f"changed interface of {n.dotted}: committed {n.interface}, live {live_interfaces[n.dotted]}"
        for n in committed.walk()
        if n.interface and n.dotted in live_interfaces and live_interfaces[n.dotted] != n.interface
    ]


def _client_of(value: Any) -> str:
    return value if isinstance(value, str) else str(value.get("client"))


def _methods_of(value: Any) -> list[str]:
    return [] if isinstance(value, str) else list(value.get("methods") or [])


def check(settings: ClientSettings, describe: Path | None) -> int:
    if settings.nodes is None:
        raise UsageError(f"client '{settings.name}' has no committed nodes to check; record them with --capture")
    if settings.exporter_class is not None:
        committed = _exporter_class_tree(settings)
    else:
        committed = from_toml_nodes(settings.nodes, device_name=settings.device_name, ref=settings.nodes_ref())
    problems, notes = drift(committed, live_tree(settings, describe))
    for note in notes:
        print(f"{settings.name}: note: {note}")
    if problems:
        print(f"{settings.name}: the committed nodes differ from the live exporter:")
        for problem in problems:
            print(f"  - {problem}")
        print(f"Run `jmp robot generate --capture --client {settings.name}` to update them.")
        return EXIT_DRIFT
    print(f"{settings.name}: the committed nodes match the live exporter")
    return EXIT_OK


# ---- commands ----


def generate_one(settings: ClientSettings, args: argparse.Namespace, project: Project | None) -> int:
    if args.check:
        return check(settings, args.from_)
    if args.capture:
        return capture(settings, project, args.from_, args.libspec)
    live = args.live or args.from_ is not None or (
        settings.nodes is None and _one_off(args) and not (settings.exporter_config or settings.tree)
        and not settings.exporter_class and _has_live_source(settings)
    )
    tree = live_tree(settings, args.from_) if live else static_tree(settings)
    emit(settings, tree, args.libspec)
    return EXIT_OK


def cmd_generate(args: argparse.Namespace) -> int:
    project = _load_project(args)
    code = EXIT_OK
    clients = select_clients(args, project)
    _check_single_exporter(args, clients)
    for settings in clients:
        code = max(code, generate_one(settings, args, project))
    return code


def cmd_render(args: argparse.Namespace) -> int:
    tree = DeviceTree.from_json(sys.stdin.read())
    sys.stdout.write(
        render_resource(tree, session=args.session, roles=args.roles)
    )
    return EXIT_OK


# Reported as one line: configuration, leasing and driver import problems.
ERRORS = (
    UsageError,
    ProjectError,
    SourceError,
    RenderError,
    TreeError,
    sessions.NoSession,
    JumpstarterException,
    ImportError,
    OSError,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Run ``jmp robot`` with ``argv`` and return its exit code."""
    import click

    from .cli import robot

    try:
        return robot.main(list(argv) if argv is not None else None, prog_name="jmp robot", standalone_mode=False) or 0
    except click.ClickException as e:
        e.show()
        return e.exit_code
    except click.Abort:
        _err("Aborted!")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
