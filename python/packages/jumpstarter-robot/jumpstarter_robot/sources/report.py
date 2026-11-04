"""Device trees from a connected exporter, or from a ``jmp describe`` dump of one.

A live tree is the only source for children that drivers build themselves
(Dutlink's power and storage, Qemu's disks), which every static source drops.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..tree import COMPOSITE_CLIENT, DeviceNode, DeviceTree, pascal_case
from ._common import CLIENT_LABEL, INTERFACE_LABEL, NAME_LABEL, STUB_CLIENT, SourceError, driver_node, opaque_node


def _client_path(node: Any) -> str:
    cls = type(node)
    return node.labels.get(CLIENT_LABEL) or f"{cls.__module__}.{cls.__qualname__}"


def _is_wrapper_root(root: Any) -> bool:
    """The composite an exporter wraps its ``export:`` entries in (it carries no name)."""
    return NAME_LABEL not in root.labels and _client_path(root) == COMPOSITE_CLIENT


def _bare_name(client: str) -> str:
    """A name for an unnamed single-driver root: ``...PowerClient`` → ``power``."""
    name = client.rsplit(".", 1)[-1].removesuffix("Client") or "driver"
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def _live_node(node: Any, path: list[str], warnings: list[str]) -> DeviceNode:
    from ..driver_library import shell_methods
    from jumpstarter.client.base import StubDriverClient

    if isinstance(node, StubDriverClient):
        missing = node.labels.get(CLIENT_LABEL, "its client class")
        return opaque_node(path, f"{missing} is not installed here; install its package and regenerate", warnings)
    children = [_live_node(child, [*path, name], warnings) for name, child in node.children.items()]
    return driver_node(
        path,
        _client_path(node),
        methods=shell_methods(node),
        interface=node.labels.get(INTERFACE_LABEL),
        children=children,
    )


def from_report(root: Any, *, device_name: str | None = None, ref: str | None = None) -> DeviceTree:
    """Read the tree of a connected client root (``serve()``, a lease, ``jmp shell``).

    Children are walked positionally by their ``jumpstarter.dev/name``, the
    binding is the node's ``jumpstarter.dev/client`` label, and shell
    drivers report their configured methods. Nodes whose client package is
    not installed here are kept as opaque nodes, with a warning.
    """
    warnings: list[str] = []
    if _is_wrapper_root(root):
        nodes = [_live_node(child, [name], warnings) for name, child in root.children.items()]
    else:
        name = root.labels.get(NAME_LABEL)
        node = _live_node(root, [name] if name else [], warnings)
        node.name = name or _bare_name(_client_path(root))
        nodes = [node]
    return DeviceTree(
        device_name=device_name or "Device",
        nodes=nodes,
        source={"kind": "report", "ref": ref},
        warnings=warnings,
    )


def _describe_drivers(data: Any) -> list[dict[str, Any]]:
    """The driver list of a ``jmp describe lease --drivers -o json`` dump (or of describe_client())."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("driver_tree"), dict):
            data = data["driver_tree"]
        if isinstance(data.get("drivers"), list):
            return data["drivers"]
    raise SourceError(
        "not a driver description: expected the output of `jmp describe lease NAME --drivers -o json` "
        "(an object with driver_tree.drivers)"
    )


def _describe_node(entry: dict[str, Any], warnings: list[str]) -> tuple[list[str], DeviceNode]:
    try:
        path = [str(segment) for segment in entry["driver_path"]]
        client = str(entry["class"])
    except (KeyError, TypeError):
        raise SourceError(f"driver entry without driver_path/class: {entry!r}") from None
    if client == STUB_CLIENT:
        return path, opaque_node(path, "its client package was not installed where it was described", warnings)
    return path, driver_node(path, client)


def from_describe(source: str | Path | dict[str, Any] | list[Any], *, device_name: str | None = None) -> DeviceTree:
    """Read the drivers of ``jmp describe lease NAME --drivers -o json`` (a path, JSON text or the parsed data).

    The dump lists every driver with its tree path and client class; it does
    not carry shell methods, which stay reachable through ``Run Method``.
    """
    ref = None
    if isinstance(source, Path) or (isinstance(source, str) and not source.lstrip().startswith(("{", "["))):
        ref = str(source)
        source = Path(source).read_text()
    if isinstance(source, str):
        try:
            source = json.loads(source)
        except json.JSONDecodeError as e:
            raise SourceError(f"driver description is not valid JSON: {e}") from None
    lease = source.get("lease") if isinstance(source, dict) else None
    warnings: list[str] = []
    by_path: dict[tuple[str, ...], DeviceNode] = {}
    roots: list[DeviceNode] = []
    for entry in _describe_drivers(source):
        path, node = _describe_node(entry, warnings)
        if not path:
            if node.client == COMPOSITE_CLIENT:
                continue  # the exporter's wrapper composite
            node.name = _bare_name(node.client or "")
        by_path[tuple(path)] = node
        parent = by_path.get(tuple(path[:-1])) if path else None
        (parent.children if parent is not None else roots).append(node)
    exporter = lease.get("exporter") if isinstance(lease, dict) else None
    name = device_name or (pascal_case(str(exporter)) if exporter else "Device")
    return DeviceTree(device_name=name, nodes=roots, source={"kind": "describe", "ref": ref}, warnings=warnings)
