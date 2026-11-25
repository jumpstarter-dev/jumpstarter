"""Device trees from a connected exporter.

A live tree is the only source for children that drivers build themselves
(Dutlink's power and storage, Qemu's disks), which every static source drops.
"""

from __future__ import annotations

import re
from typing import Any

from ..tree import COMPOSITE_CLIENT, DeviceNode, DeviceTree
from ._common import CLIENT_LABEL, NAME_LABEL, driver_node, opaque_node


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
        opaque = opaque_node(path, f"{missing} is not installed here; install its package and regenerate", warnings)
        # its children may well have clients installed here, and are drivers like any other
        opaque.children = [_live_node(child, [*path, name], warnings) for name, child in node.children.items()]
        return opaque
    children = [_live_node(child, [*path, name], warnings) for name, child in node.children.items()]
    return driver_node(
        path,
        _client_path(node),
        methods=shell_methods(node),
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
