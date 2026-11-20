"""Device trees from a committed ``nodes`` table: tree path → Python client class.

Test authors rarely see the exporter's config, so the mapping is committed
next to the tests and needs no exporter config and no lease::

    [tool.jumpstarter.client.rpi4.nodes]
    "dutlink"       = "jumpstarter_driver_dutlink.client.DutlinkClient"
    "dutlink.power" = "jumpstarter_driver_power.client.PowerClient"
    "camera"        = { client = "jumpstarter_driver_ustreamer.client.UStreamerClient", required = false }
    "shell"         = { client = "jumpstarter_driver_shell.client.ShellClient", methods = ["build", "deploy"] }

Nesting comes from the dotted keys; a parent that is not listed is an implied
composite.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from ..tree import DeviceNode, DeviceTree
from ._common import SourceError, class_path, driver_node

NODE_KEYS = ("client", "required", "methods", "interface")


def _methods(path: str, value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [m.strip() for m in value.split(",") if m.strip()]
    if isinstance(value, list) and all(isinstance(m, str) for m in value):
        return list(value)
    raise SourceError(f"nodes.\"{path}\".methods must be a list of method names")


def _spec(path: str, value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"client": value}
    unknown = sorted(set(value) - set(NODE_KEYS))
    if unknown:
        raise SourceError(f"nodes.\"{path}\": unknown key(s) {', '.join(unknown)}; allowed: {', '.join(NODE_KEYS)}")
    if not isinstance(value.get("client"), str) or not value["client"].strip():
        raise SourceError(f"nodes.\"{path}\".client must be a class path like package.module.Client")
    required = value.get("required", True)
    if not isinstance(required, bool):
        raise SourceError(f"nodes.\"{path}\".required must be true or false")
    return dict(value)


def _entries(table: Mapping[str, Any], prefix: tuple[str, ...] = ()) -> Iterator[tuple[list[str], dict[str, Any]]]:
    for key, value in table.items():
        path = [*prefix, *(segment.strip() for segment in str(key).split("."))]
        dotted = ".".join(path)
        if not all(path):
            raise SourceError(f"nodes: '{dotted}' is not a dotted tree path")
        if isinstance(value, Mapping) and "client" not in value:
            yield from _entries(value, tuple(path))  # a nested [nodes.dutlink] table groups its children
        elif isinstance(value, (str, Mapping)):
            yield path, _spec(dotted, value)
        else:
            raise SourceError(f"nodes.\"{dotted}\" must be a class path or a table with client/required/methods")


def from_toml_nodes(table: Mapping[str, Any], *, device_name: str = "Device", ref: str | None = None) -> DeviceTree:
    """Read a committed ``nodes`` mapping (the parsed TOML table) into a tree."""
    nodes: dict[tuple[str, ...], DeviceNode] = {}
    roots: list[DeviceNode] = []
    explicit: set[tuple[str, ...]] = set()

    def ensure(path: tuple[str, ...]) -> DeviceNode:
        node = nodes.get(path)
        if node is None:
            node = driver_node(list(path), None, kind="composite")
            nodes[path] = node
            (ensure(path[:-1]).children if len(path) > 1 else roots).append(node)
        return node

    for path, spec in _entries(table):
        key = tuple(path)
        if key in explicit:
            raise SourceError(f"nodes: '{'.'.join(path)}' is listed twice")
        explicit.add(key)
        node = ensure(key)
        typed = driver_node(
            path,
            class_path(spec["client"]),
            required=spec.get("required", True),
            methods=_methods(".".join(path), spec.get("methods")),
            interface=spec.get("interface"),
        )
        node.kind, node.bindings, node.required = typed.kind, typed.bindings, typed.required
        node.methods, node.interface = typed.methods, typed.interface
    return DeviceTree(device_name=device_name, nodes=roots, source={"kind": "nodes", "ref": ref})


def to_toml_nodes(tree: DeviceTree) -> dict[str, str | dict[str, Any]]:
    """The ``nodes`` mapping of a tree: every typed node, composites implied by their children."""
    mapping: dict[str, str | dict[str, Any]] = {}
    for node in tree.walk():
        client = node.client
        if node.kind == "opaque" or client is None:
            continue
        if node.kind == "composite" and node.children:
            continue  # implied by its children
        if not node.path:
            raise SourceError("a nodes table cannot describe an unnamed root driver; serve it under a name")
        extra: dict[str, Any] = {}
        if not node.required:
            extra["required"] = False
        if node.methods:
            extra["methods"] = list(node.methods)
        mapping[node.dotted] = {"client": client, **extra} if extra else client
    return mapping
