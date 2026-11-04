"""Render a device tree as a Robot Framework client resource.

The resource imports ``Jumpstarter``, declares its session with ``Session``
and imports one ``Driver`` library per driver node, so every keyword is
visible to libdoc and the editor without a lease::

    *** Settings ***
    Library    jumpstarter_robot.Jumpstarter    auto_import=False
    Library    jumpstarter_robot.Session    default    exporter_config=${JMP_EXPORTER_CONFIG}
    ...    selector=${JMP_SELECTOR}    lease_name=${JMP_LEASE_NAME}    config=${JMP_CLIENT_CONFIG}
    ...    AS    session.default
    Library    jumpstarter_robot.Driver    dut.power    client=jumpstarter_driver_power.client.PowerClient
    ...    prefix=Power    session=default    AS    dut.power

How the session connects (exporter config, selector, lease, client config)
is a ``*** Variables ***`` default, so ``robot -v JMP_SELECTOR:board=rpi4``
or a pabot argument file can point the same resource at another exporter.
A named session has its own variables (``${JMP_ANDROID_SELECTOR}``) and
never defines the default session's, so client resources of different
sessions can be imported into one suite.
The output is deterministic: no timestamps, no absolute paths of the
machine it was generated on.
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .naming import normalize, session_tag, session_variable, title
from .tree import DeviceNode, DeviceTree

DEFAULT_SESSION = "default"
SEPARATOR = "    "

# Session library argument → resource variable, in the order they are written.
SOURCE_VARIABLES = (
    ("exporter_config", "JMP_EXPORTER_CONFIG"),
    ("selector", "JMP_SELECTOR"),
    ("lease_name", "JMP_LEASE_NAME"),
    ("config", "JMP_CLIENT_CONFIG"),
)
SECRET_KEYS = ("token", "password", "secret")
# A named session left without a value of its own for these takes the
# default session's variable when it is given on the command line, so
# ``-v JMP_SELECTOR:...`` reaches every session that has no value of its own.
# The Session library resolves that at run time: a resource that wrote
# ``${JMP_SELECTOR}`` itself would set it for every other resource of the
# suite, since Robot Framework keeps the first value of a variable. The
# exporter config never falls back: each session's default is its own.
FALLBACK_ARGS = ("selector", "lease_name", "config")


def devices_variable(index: int, session: str) -> str:
    """The ``@{DEVICES}`` list of role group ``index``: ``DEVICES_2``, or ``ANDROID_DEVICES`` for session android."""
    name = "DEVICES" if index == 0 else f"DEVICES_{index + 1}"
    return name if session == DEFAULT_SESSION else f"{session_tag(session)}_{name}"


_SOURCE_LABELS = {
    "exporter-config": "exporter config",
    "exporter-class": "ExporterClass",
    "nodes": "the committed nodes table",
    "report": "a live exporter",
    "describe": "the driver description",
    "tree": "the device tree",
}


class RenderError(ValueError):
    """The tree cannot be rendered as a resource without ambiguous keywords."""


@dataclass
class DriverImport:
    """One ``Library    jumpstarter_robot.Driver`` line."""

    path: str
    alias: str
    client: str
    prefix: str
    required: bool = True
    methods: tuple[str, ...] = ()
    role: str | None = None
    aliased: bool = False
    keywords: tuple[str, ...] = ()

    def arguments(self, session: str) -> list[str]:
        """The library arguments (without ``AS``), as libdoc and the resource take them."""
        args = [self.path] if self.path else ["path="]
        if self.role:
            args.append(f"role={self.role}")
        args += [f"client={self.client}", f"prefix={self.prefix}", f"session={session}"]
        if not self.required:
            args.append("required=False")
        if self.methods:
            args.append(f"methods={','.join(self.methods)}")
        return args


@dataclass
class Plan:
    """What a resource imports: the driver libraries, skipped nodes and role devices."""

    imports: list[DriverImport]
    skipped: list[str]
    devices: list[list[str]]


# ---- selecting nodes ----


def _selected(path: str, session: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    candidates = (path, f"{session}.{path}")

    def hit(patterns: Sequence[str]) -> bool:
        return any(fnmatch.fnmatchcase(c, p) for c in candidates for p in patterns)

    return (not include or hit(include)) and not hit(exclude)


def _alias_for(session: str, path: str) -> str:
    return path if session == DEFAULT_SESSION else f"{session}.{path}"


def _signature(node: DeviceNode) -> tuple:
    """What a subtree looks like below ``node``: relative paths and classes (for role detection)."""
    depth = len(node.path)
    return tuple(
        (tuple(d.path[depth:]), d.kind, d.client, d.required, tuple(d.methods))
        for d in node.walk()
        if d is not node and d.kind != "opaque"
    )


def find_role_groups(tree: DeviceTree) -> list[list[DeviceNode]]:
    """Sets of identical subtrees under different parents (``dut1``/``dut2`` with the same drivers).

    A group needs two or more members with at least one typed descendant;
    groups nested inside an already chosen group are left to it.
    """
    by_signature: dict[tuple, list[DeviceNode]] = {}
    for node in tree.walk():
        if node.kind == "opaque" or not node.children:
            continue
        signature = _signature(node)
        if any(entry[1] == "driver" for entry in signature):
            by_signature.setdefault(signature, []).append(node)
    groups = sorted((g for g in by_signature.values() if len(g) > 1), key=lambda g: min(len(n.path) for n in g))
    chosen: list[list[DeviceNode]] = []
    covered: list[list[str]] = []
    for group in groups:
        if any(node.path[: len(c)] == c for node in group for c in covered):
            continue
        chosen.append(group)
        covered.extend(node.path for node in group)
    return chosen


# ---- keywords and collisions ----


def _keywords(item: DriverImport, session: str) -> tuple[str, ...]:
    from .driver_library import Driver

    try:
        driver = Driver(
            item.path,
            client=item.client,
            prefix=item.prefix,
            session=session,
            methods=list(item.methods),
            role=item.role,
            required=item.required,
        )
    except (ImportError, AttributeError, TypeError, ValueError) as e:
        raise RenderError(
            f"driver '{item.path or item.alias}': cannot import its client {item.client} ({e}); "
            "install the driver's package in this environment and generate again"
        ) from None
    return tuple(driver.get_keyword_names())


def reserved_keywords(session: str) -> dict[str, str]:
    """Keyword names the resource already gets from ``Jumpstarter`` and ``Session`` (normalized → name)."""
    from .keywords import load_all
    from .library import Jumpstarter

    reserved = {}
    for owner in (Jumpstarter, *load_all()):
        for attr in dir(owner):
            member = getattr(owner, attr, None)
            if not attr.startswith("_") and callable(member) and hasattr(member, "robot_name"):
                name = member.robot_name or title(attr)
                reserved[normalize(name)] = name
    for verb in ("Connect", "Release"):
        name = f"{verb} {title(session)} Session"
        reserved[normalize(name)] = name
    return reserved


def _collisions(imports: Sequence[DriverImport], reserved: Mapping[str, str]) -> dict[int, list[str]]:
    """Index of each import whose keywords clash with another's (or a reserved one) → clashing names."""
    owners: dict[str, list[int]] = {}
    for index, item in enumerate(imports):
        for name in item.keywords:
            owners.setdefault(normalize(name), []).append(index)
    clashes: dict[int, list[str]] = {}
    for key, indexes in owners.items():
        if len(indexes) > 1 or key in reserved:
            for index in indexes:
                clashes.setdefault(index, []).append(key)
    return clashes


def _describe_clash(imports: Sequence[DriverImport], clashes: Mapping[int, list[str]], reserved) -> str:
    lines = []
    for index, keys in sorted(clashes.items()):
        item = imports[index]
        names = sorted({next((n for n in item.keywords if normalize(n) == k), k) for k in keys})
        lines.append(f"  {item.path or item.alias} (prefix '{item.prefix}'): {', '.join(names)}")
    reserved_hits = sorted({reserved[k] for keys in clashes.values() for k in keys if k in reserved})
    hint = f"; already defined by the Jumpstarter library: {', '.join(reserved_hits)}" if reserved_hits else ""
    return (
        "keyword names would be ambiguous between drivers" + hint + ":\n" + "\n".join(lines) + "\n"
        "Give the drivers distinct prefixes with aliases (robot.toml [tool.jumpstarter.client.<name>.aliases] "
        "or --alias path=Prefix)."
    )


def _resolve_collisions(imports: list[DriverImport], session: str, reserved: Mapping[str, str]) -> None:
    """Refuse keyword names two drivers would share; aliases tell them apart."""
    for item in imports:
        item.keywords = _keywords(item, session)
    clashes = _collisions(imports, reserved)
    if clashes:
        raise RenderError(_describe_clash(imports, clashes, reserved))


# ---- planning ----


def _driver_import(node: DeviceNode, path: list[str], aliases, session, role=None) -> DriverImport:
    """A driver's keywords are prefixed with its path (``dut.power`` → ``Dut Power``), or its alias."""
    alias_prefix = aliases.get(role) if role else aliases.get(node.dotted)
    return DriverImport(
        path=node.dotted,
        alias=_alias_for(session, role or node.dotted or node.name),
        client=node.client or "",
        prefix=alias_prefix if alias_prefix is not None else title(*path),
        required=node.required,
        methods=tuple(node.methods),
        role=role,
        aliased=alias_prefix is not None,
    )


def _wanted(node: DeviceNode) -> bool:
    """Composite nodes only get a library when their client adds keywords of its own."""
    return node.kind != "opaque" and node.client is not None


def plan(
    tree: DeviceTree,
    *,
    session: str = DEFAULT_SESSION,
    aliases: Mapping[str, str] | None = None,
    roles: bool = False,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    check: bool = True,
) -> Plan:
    """Decide which Driver libraries a resource imports, with their prefixes."""
    aliases = dict(aliases or {})
    skipped = [
        f"{n.dotted or n.name}: skipped ({' '.join((n.reason or 'no client').split())})"
        for n in tree.walk()
        if n.kind == "opaque"
    ]
    groups, roles_by_path = _role_libraries(find_role_groups(tree) if roles else [])
    covered = [member.path for group in groups for member in group]
    plain = [
        n
        for n in tree.walk()
        if _wanted(n)
        and not any(n.path[: len(c)] == c and len(n.path) > len(c) for c in covered)
        and _selected(n.dotted, session, include, exclude)
    ]
    imports = [_driver_import(n, list(n.path) or [n.name], aliases, session) for n in plain]
    imports += [
        _driver_import(node, role.split("."), aliases, session, role=role)
        for role, (node, _) in roles_by_path.items()
        if _selected(role, session, include, exclude)
    ]
    imports = [item for item in imports if _has_keywords(item, tree, session)]
    _check_aliases(imports)
    if check:
        _resolve_collisions(imports, session, reserved_keywords(session))
    return Plan(imports=imports, skipped=skipped, devices=[[n.dotted for n in group] for group in groups])


def _role_key(node: DeviceNode) -> tuple:
    return node.kind, node.client, node.required, tuple(node.methods)


def _role_libraries(
    groups: Sequence[list[DeviceNode]],
) -> tuple[list[list[DeviceNode]], dict[str, tuple[DeviceNode, str]]]:
    """The groups that get role libraries, and each role (relative path) → (its default node, its group).

    `Use Device` binds roles by relative path, so two groups can share a role
    only when it is the same driver in both. A group that would give a role
    another client class keeps plain per-node libraries instead.
    """
    kept: list[list[DeviceNode]] = []
    roles: dict[str, tuple[DeviceNode, str]] = {}
    for group in groups:
        first = group[0]
        own = {
            ".".join(node.path[len(first.path) :]): node
            for node in first.walk()
            if node is not first and _wanted(node)
        }
        if any(role in roles and _role_key(roles[role][0]) != _role_key(node) for role, node in own.items()):
            continue
        kept.append(group)
        members = ".".join(n.dotted for n in group)
        for role, node in own.items():
            roles.setdefault(role, (node, members))
    return kept, roles


def _has_keywords(item: DriverImport, tree: DeviceTree, session: str) -> bool:
    node = tree.find(item.path.split(".")) if item.path else None
    if node is None or node.kind != "composite":
        return True
    item.keywords = _keywords(item, session)
    return bool(item.keywords)


def _check_aliases(imports: Sequence[DriverImport]) -> None:
    seen: dict[str, str] = {}
    for item in imports:
        key = item.alias.lower()
        if key in seen:
            raise RenderError(f"two driver libraries would be imported AS '{item.alias}' ({seen[key]} and {item.path})")
        seen[key] = item.path
    prefixes: dict[str, DriverImport] = {}
    for item in imports:
        if item.aliased:
            other = prefixes.get(normalize(item.prefix))
            if other is not None:
                raise RenderError(f"aliases give '{other.path}' and '{item.path}' the same prefix '{item.prefix}'")
            prefixes[normalize(item.prefix)] = item


# ---- text ----


def escape(value: Any) -> str:
    """A Robot Framework cell for ``value``; empty becomes ``${EMPTY}``."""
    text = "" if value is None else str(value)
    if not text:
        return "${EMPTY}"
    text = text.replace("\\", "\\\\")
    for marker in ("${", "@{", "&{", "%{"):
        text = text.replace(marker, "\\" + marker)
    stripped = text.rstrip(" ")
    trailing = len(text) - len(stripped)
    # every space after the first of a run, so no two spaces in a row are read as a separator
    text = re.sub(r"(?<= ) ", lambda _: "\\ ", stripped).replace("\t", "\\t").replace("\n", "\\n")
    if text.startswith((" ", "#")):
        text = "\\" + text
    # Robot Framework strips trailing spaces from a line, escaped or not
    return text + "${SPACE}" * trailing


def variable_value(value: Any, resource_dir: Path) -> str:
    """A variable default: paths become ``${CURDIR}``-relative, aliases stay as they are."""
    if isinstance(value, Path):
        relative = Path(os.path.relpath(value.resolve(), resource_dir.resolve())).as_posix()
        return "${CURDIR}/" + escape(relative)
    return escape(value)


def _line(*cells: str) -> str:
    return SEPARATOR.join(cells)


def _relative_ref(ref: Any, resource_dir: Path) -> Any:
    if isinstance(ref, str) and os.path.isabs(ref):
        return Path(os.path.relpath(ref, resource_dir.resolve())).as_posix()
    return ref


def describe_source(tree: DeviceTree, resource_dir: Path) -> str:
    kind = tree.source.get("kind")
    label = _SOURCE_LABELS.get(kind, str(kind or "a device tree"))
    ref = _relative_ref(tree.source.get("ref"), resource_dir)
    return f"{label} {ref}" if ref else label


def _check_secrets(source_args: Mapping[str, Any]) -> None:
    for key in source_args:
        if any(secret in key.lower() for secret in SECRET_KEYS):
            raise RenderError(f"refusing to write '{key}' into a resource: credentials stay out of the repository")
    unknown = sorted(set(source_args) - {name for name, _ in SOURCE_VARIABLES})
    if unknown:
        raise RenderError(f"unknown session argument(s): {', '.join(unknown)}")


def render_resource(
    tree: DeviceTree,
    *,
    session: str = DEFAULT_SESSION,
    source_args: Mapping[str, Any] | None = None,
    aliases: Mapping[str, str] | None = None,
    roles: bool = False,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    resource_dir: str | Path | None = None,
    planned: Plan | None = None,
) -> str:
    """The text of the client resource for ``tree``.

    ``source_args`` are the defaults of the session variables
    (``exporter_config``, ``selector``, ``lease_name``, ``config``);
    ``Path`` values are written relative to ``resource_dir`` as
    ``${CURDIR}/...``. ``aliases`` map a node path (or a role path) to its
    keyword prefix. With ``roles``, identical subtrees become role
    libraries plus a ``@{DEVICES}`` list for `Use Device`
    (``@{ANDROID_DEVICES}`` for session ``android``).
    """
    source_args = dict(source_args or {})
    _check_secrets(source_args)
    resource_dir = Path(resource_dir or Path.cwd())
    planned = planned or plan(
        tree, session=session, aliases=aliases, roles=roles, include=include, exclude=exclude
    )
    lines = [
        "*** Settings ***",
        _line(
            "Documentation",
            f"Generated by `jmp robot generate` from {escape(describe_source(tree, resource_dir))} "
            f"(device {escape(tree.device_name)}, session {escape(session)}). Do not edit: regenerating overwrites it.",
        ),
        _line("Library", "jumpstarter_robot.Jumpstarter", "auto_import=False"),
        _line(
            "Library",
            "jumpstarter_robot.Session",
            escape(session),
            *(f"{arg}=${{{session_variable(var, session)}}}" for arg, var in SOURCE_VARIABLES),
            "AS",
            f"session.{session}",
        ),
    ]
    for item in planned.imports:
        arguments = map(_cell, item.arguments(session))
        lines.append(_line("Library", "jumpstarter_robot.Driver", *arguments, "AS", item.alias))
    lines += [f"# {skipped}" for skipped in planned.skipped]
    lines += ["", "*** Variables ***"]
    lines += _variable_lines(session, source_args, resource_dir)
    for index, devices in enumerate(planned.devices):
        lines.append(_line(f"@{{{devices_variable(index, session)}}}", *map(escape, devices)))
    return "\n".join(lines) + "\n"


def _variable_lines(session: str, source_args: Mapping[str, Any], resource_dir: Path) -> list[str]:
    return [
        _line(f"${{{session_variable(var, session)}}}", variable_value(source_args.get(arg), resource_dir))
        for arg, var in SOURCE_VARIABLES
    ]


def _cell(argument: str) -> str:
    name, sep, value = argument.partition("=")
    return f"{name}={escape(value) if value else '${EMPTY}'}" if sep else escape(argument)


def write_resource(path: str | Path, tree: DeviceTree, **options: Any) -> tuple[Path, Plan]:
    """Write the client resource for ``tree`` to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    options.setdefault("resource_dir", path.parent)
    planned = options.pop("planned", None) or plan(
        tree,
        **{k: options[k] for k in ("session", "aliases", "roles", "include", "exclude") if k in options},
    )
    path.write_text(render_resource(tree, planned=planned, **options))
    return path, planned
