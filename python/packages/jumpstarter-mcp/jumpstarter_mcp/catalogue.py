"""The commands of a leased device, as tools: one for each public member of each driver's client.

A client class is exposed through several frontends: the ``j`` command line, Robot Framework keywords, and this
server. They share one mechanism, and this module is the server's part of it:

* the drivers of a lease are the nodes of its client tree, each named by its path (``dutlink.power``);
* the members of a driver are what ``jumpstarter.client.introspect.iter_client_members`` says its client class has,
  the same list Robot Framework makes its keywords from: methods, properties (read) and generators (read up to a
  number of items). What cannot be one call (a context manager, an ``async`` member) is not a command;
* a driver's author curates them with ``jumpstarter.client.decorators.mcp`` (``@mcp.tool``, ``@mcp.not_tool``,
  ``@mcp.toolset``).

A :class:`Command` has what a tool needs: a name, a description, a JSON schema of its arguments made from the
method's signature and type hints, and the hints a host shows a person (read-only, destructive). :func:`invoke`
checks the arguments against that schema, calls the member on the lease's client and gives back plain JSON.

Nothing here knows a driver by name: a driver added later has its commands the same way.
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import itertools
import re
import typing
from collections.abc import Iterator, Mapping
from pathlib import PurePath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, create_model

from jumpstarter.client.base import StubDriverClient
from jumpstarter.client.decorators._frontend import frontend_meta
from jumpstarter.client.introspect import ClientMember, iter_client_members

FRONTEND = "mcp"
#: How many items of a generator a command reads when it is not told: a console that never ends has an end here.
DEFAULT_MAX_ITEMS = 100
MAX_ITEMS = 10_000
MAX_RESULT_DEPTH = 8
#: Members whose name starts with one of these only read: a host need not ask a person before each.
_READS = ("get_", "read", "list_", "status", "info", "is_", "has_", "describe", "version", "should_", "wait_")


MAX_TOOL_NAME = 64


def tool_name(driver_path: tuple[str, ...], name: str) -> str:
    """The tool name of a driver's command: its path and name joined with ``_``, in what every host accepts."""
    joined = "_".join((*driver_path, name)).lower()
    cleaned = "".join(char if char.isascii() and (char.isalnum() or char == "_") else "_" for char in joined)
    if len(cleaned) <= MAX_TOOL_NAME:
        return cleaned
    # the end says what it does: the start of the path is what is cut, and a mark of it kept so names stay apart
    mark = format(sum(joined.encode()) % 0xFFFF, "04x")
    return f"{mark}_{cleaned[-(MAX_TOOL_NAME - 5) :]}"


class CommandError(Exception):
    """A command cannot be found or called as asked, and why."""


@dataclasses.dataclass(frozen=True)
class Command:
    """One member of one driver, as a tool."""

    #: the driver's path in the lease's client tree, its children's names in order (``["dutlink", "power"]``)
    driver_path: tuple[str, ...]
    #: the driver's client class, ``module.Qualname``
    client_class: str
    #: the member's name on the client (``on``, ``read``)
    member: str
    #: ``method``, ``property`` (read, no arguments) or ``generator`` (read up to ``max_items``)
    kind: str
    #: the name within the driver (the member's, unless its author gave another)
    name: str
    description: str
    #: the JSON schema of the arguments: an object
    schema: dict[str, Any]
    read_only: bool
    destructive: bool
    #: checks and converts the arguments (a pydantic model made from the signature)
    arguments: type[BaseModel] = dataclasses.field(repr=False, compare=False)

    @property
    def driver(self) -> str:
        """The driver's path as text: ``dutlink.power``; ``""`` is the root."""
        return ".".join(self.driver_path)

    @property
    def qualified(self) -> str:
        """The command's name in the lease: ``dutlink.power.on``."""
        return ".".join((*self.driver_path, self.name))

    @property
    def tool(self) -> str:
        """The command's name as a tool: ``dutlink_power_on``. Lower case letters, digits and ``_`` only (what
        every agent's host takes), and no longer than :data:`MAX_TOOL_NAME`. It names a driver and a command, never
        a lease: two leases with the same driver have the same tools, and a call says which lease it is for."""
        return tool_name(self.driver_path, self.name)


def class_path(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def walk(client: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    """The drivers of a client tree with their paths, parents first. A driver whose client is not installed (a stub)
    has no commands and is left out, with what is under it."""
    if isinstance(client, StubDriverClient):
        return
    yield path, client
    children = getattr(client, "children", None)
    if isinstance(children, Mapping):
        for name, child in children.items():
            yield from walk(child, (*path, name))


def _doc(function: Any) -> str:
    """What a function says of itself: its own docstring, never one it inherits. A method that overrides another
    and says nothing is not described by what the other does."""
    own = getattr(function, "__doc__", None)
    return inspect.cleandoc(own) if isinstance(own, str) else ""


def _hints(function: Any) -> dict[str, Any]:
    """The type hints of a function that can be resolved; one that cannot is left out, and is then any value."""
    try:
        return typing.get_type_hints(function)
    except Exception:  # noqa: BLE001 - a hint that names something not importable here
        hints: dict[str, Any] = {}
        for name, annotation in getattr(function, "__annotations__", {}).items():
            if not isinstance(annotation, str):
                hints[name] = annotation
        return hints


def _usable(hint: Any) -> Any:
    """A hint a JSON schema can be made of, or ``Any``: an argument of a type JSON cannot say is passed as it comes."""
    if hint is inspect.Parameter.empty:
        return Any
    try:
        TypeAdapter(hint).json_schema()
    except Exception:  # noqa: BLE001 - whatever pydantic cannot describe
        return Any
    return hint


_SECTION = re.compile(r"^\s*(Args|Arguments|Parameters|Params)\s*:\s*$", re.IGNORECASE)
_ENTRY = re.compile(r"^(\s+)\*{0,2}(\w+)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
_FIELD = re.compile(r"^\s*:param\s+(?:[\w\[\], .|]+\s+)?(\w+)\s*:\s*(.*)$")


def documented(doc: str | None) -> dict[str, str]:
    """What a docstring says of each argument: the entries of its ``Args:`` section (as this project writes them),
    or its ``:param name:`` fields. The documentation of a driver's method is the documentation of its tool."""
    said: dict[str, str] = {}
    if not doc:
        return said
    inside = False
    indent: int | None = None
    current: str | None = None
    for line in doc.splitlines():
        field = _FIELD.match(line)
        if field:
            current = field.group(1)
            said[current] = field.group(2).strip()
            inside = False
            continue
        if _SECTION.match(line):
            inside, indent, current = True, None, None
            continue
        if not inside:
            if current is not None and line.startswith((" ", "\t")) and line.strip():
                said[current] = f"{said[current]} {line.strip()}".strip()
            else:
                current = None
            continue
        if not line.strip():
            continue
        entry = _ENTRY.match(line)
        depth = len(line) - len(line.lstrip())
        if entry and (indent is None or depth == indent):
            indent = depth
            current = entry.group(2)
            said[current] = entry.group(3).strip()
        elif current is not None and indent is not None and depth > indent:
            said[current] = f"{said[current]} {line.strip()}".strip()
        else:
            inside, current = False, None  # the section is over: another one, or text that is not indented
    return said


def _arguments(member: ClientMember) -> type[BaseModel]:
    """The arguments of a member as a model: its schema is the tool's, and it checks what a call gives."""
    fields: dict[str, Any] = {}
    notes = documented(_doc(member.function))

    def field(name: str, default: Any) -> Any:
        return Field(default, description=notes[name]) if name in notes else default

    if member.kind == "generator":
        fields["max_items"] = (int, Field(DEFAULT_MAX_ITEMS, description="How many items to read at most."))
    if member.kind != "property":
        hints = _hints(member.function)
        parameters = list(inspect.signature(member.function).parameters.values())[1:]  # without `self`
        for parameter in parameters:
            hint = _usable(hints.get(parameter.name, parameter.annotation))
            if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
                fields[parameter.name] = (list[hint], field(parameter.name, []))
            elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
                fields[parameter.name] = (dict[str, hint], field(parameter.name, {}))
            elif parameter.default is inspect.Parameter.empty:
                fields[parameter.name] = (hint, field(parameter.name, ...))
            else:
                fields[parameter.name] = (hint, field(parameter.name, parameter.default))
    try:
        return create_model(f"{member.name}_arguments", __config__=ConfigDict(extra="forbid"), **fields)
    except Exception:  # noqa: BLE001 - a default pydantic cannot keep: the argument is then any value, not required
        loose = dict.fromkeys(fields, (Any, None))
        return create_model(f"{member.name}_arguments", __config__=ConfigDict(extra="forbid"), **loose)  # ty: ignore[no-matching-overload]


def _exposed(member: ClientMember, cls: type) -> bool:
    meta = frontend_meta(member.function, FRONTEND)
    if meta.get("hidden") is True:
        return False
    if meta.get("hidden") is False:  # said to be a tool
        return True
    return frontend_meta(cls, FRONTEND).get("auto_tools", True) is not False


def _command(path: tuple[str, ...], cls: type, member: ClientMember) -> Command | None:
    if member.kind not in ("method", "property", "generator") or not _exposed(member, cls):
        return None
    meta = frontend_meta(member.function, FRONTEND)
    arguments = _arguments(member)
    schema = arguments.model_json_schema()
    schema.pop("title", None)
    read_only = meta.get("read_only")
    if read_only is None:
        read_only = member.kind != "method" or member.name.startswith(_READS)
    described = meta.get("description") or _doc(member.function)
    return Command(
        driver_path=path,
        client_class=class_path(cls),
        member=member.name,
        kind=member.kind,
        name=meta.get("name") or member.name,
        description=described.strip(),
        schema=schema,
        read_only=bool(read_only),
        destructive=bool(meta.get("destructive")) and not read_only,
        arguments=arguments,
    )


def driver_commands(path: tuple[str, ...], client: Any) -> list[Command]:
    """The commands of one driver: its client class's members that can be a tool, by name.

    A member that only gives one of the driver's own children (a property named as the child is) is not one: the
    child is a driver of the lease, with tools of its own.
    """
    cls = type(client)
    children = getattr(client, "children", None)
    below = set(children) if isinstance(children, Mapping) else set()
    members = (
        member for member in iter_client_members(cls) if not (member.kind == "property" and member.name in below)
    )
    found = (_command(path, cls, member) for member in members)
    return sorted((command for command in found if command is not None), key=lambda command: command.name)


def commands(client: Any) -> list[Command]:
    """Every command of a lease: those of each driver of its client tree, drivers in tree order."""
    return [command for path, node in walk(client) for command in driver_commands(path, node)]


def drivers(client: Any) -> list[dict[str, Any]]:
    """The drivers of a lease, shortly: path, client class, what the class says of itself, and how many commands."""
    listed = []
    for path, node in walk(client):
        cls = type(node)
        said = frontend_meta(cls, FRONTEND).get("description") or getattr(node, "description", None)
        doc = (said or inspect.getdoc(cls) or "").strip().splitlines()
        listed.append(
            {
                "driver": ".".join(path),
                "class": class_path(cls),
                "description": doc[0] if doc else "",
                "commands": [command.name for command in driver_commands(path, node)],
            }
        )
    return listed


def resolve(client: Any, path: tuple[str, ...]) -> Any:
    """The driver at a path of the lease's client tree. Raises :class:`CommandError` with the names there are."""
    node = client
    for at, name in enumerate(path):
        children = getattr(node, "children", None)
        if not isinstance(children, Mapping) or name not in children:
            here = ".".join(path[:at]) or "the device"
            have = ", ".join(children) if isinstance(children, Mapping) and children else "none"
            raise CommandError(f"{here} has no driver {name!r} (it has: {have})")
        node = children[name]
    return node


def find_tool(client: Any, tool: str) -> tuple[Any, Command]:
    """The driver and the command a tool name means in this lease. Raises :class:`CommandError`, with the names
    nearest to the one asked for."""
    wanted = tool.strip()
    known: list[str] = []
    for path, node in walk(client):
        for command in driver_commands(path, node):
            if wanted in (command.tool, command.qualified):
                return node, command
            known.append(command.tool)
    near = [name for name in known if wanted.lower() in name or name in wanted.lower()][:8]
    hint = f" Did you mean: {', '.join(near)}?" if near else " List them with jmp_tools."
    raise CommandError(f"This device has no tool {tool!r}.{hint}")


def find(client: Any, driver: str, name: str) -> tuple[Any, Command]:
    """The driver at ``driver`` (``dutlink.power``) and its command ``name``. Raises :class:`CommandError`."""
    path = tuple(part for part in driver.replace("/", ".").split(".") if part)
    node = resolve(client, path)
    offered = driver_commands(path, node)
    for command in offered:
        if name in (command.name, command.member):
            return node, command
    have = ", ".join(command.name for command in offered) or "none"
    raise CommandError(f"{driver or 'the device'} has no command {name!r} (it has: {have})")


def plain(value: Any, depth: int = 0) -> Any:
    """A result as plain JSON: models and dataclasses as objects, what is not data as what it is called."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if depth >= MAX_RESULT_DEPTH:
        return repr(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, enum.Enum):
        return plain(value.value, depth + 1)
    if isinstance(value, bytes | bytearray | memoryview):
        return {"bytes": len(value)}
    if isinstance(value, PurePath):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name), depth + 1) for field in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(key): plain(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [plain(item, depth + 1) for item in value]
    return repr(value)


def invoke(node: Any, command: Command, arguments: Mapping[str, Any] | None = None) -> Any:
    """Calls a command on its driver with arguments as a tool gives them, and gives back plain JSON. Blocks.

    Raises :class:`CommandError` when the arguments are not the command's; what the driver raises is its own.
    """
    try:
        checked = command.arguments.model_validate(dict(arguments or {}))
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(str(at) for at in error['loc'])}: {error['msg']}" for error in exc.errors())
        raise CommandError(f"{command.qualified}: {problems}") from exc
    given = {name: getattr(checked, name) for name in type(checked).model_fields}
    if command.kind == "property":
        return plain(getattr(node, command.member))
    limit = min(max(int(given.pop("max_items", DEFAULT_MAX_ITEMS)), 0), MAX_ITEMS) if command.kind == "generator" else 0
    positional: list[Any] = []
    named: dict[str, Any] = {}
    for parameter in list(inspect.signature(getattr(type(node), command.member)).parameters.values())[1:]:
        if parameter.name not in given:
            continue
        value = given[parameter.name]
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            positional.extend(value)
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            named.update(value)
        elif parameter.kind is inspect.Parameter.POSITIONAL_ONLY:
            positional.append(value)
        else:
            named[parameter.name] = value
    result = getattr(node, command.member)(*positional, **named)
    if command.kind == "generator":
        try:
            return plain(list(itertools.islice(result, limit)))
        finally:
            close = getattr(result, "close", None)
            if callable(close):
                close()
    return plain(result)
