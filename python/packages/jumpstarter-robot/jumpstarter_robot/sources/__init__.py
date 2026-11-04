"""Where a device tree comes from.

Every source returns a ``jumpstarter_robot.tree.DeviceTree``, so one renderer
serves them all:

- ``from_report(client)``: a connected exporter (live, includes driver-built children)
- ``from_exporter_config(path_or_alias)``: a local exporter config, offline
- ``from_toml_nodes(table)``: the committed ``nodes`` mapping of a ``robot.toml`` client
- ``from_describe(json)``: ``jmp describe lease NAME --drivers -o json`` output
- ``from_tree_json(path)``: a device tree JSON from another codegen frontend
- ``from_exporter_class(file, dirs)``: an ExporterClass and its DriverInterfaces
"""

from __future__ import annotations

from pathlib import Path

from ..tree import DeviceTree, TreeError
from ._common import SourceError
from .exporter_class import ExporterClassError, from_exporter_class
from .exporter_config import from_exporter_config
from .report import from_describe, from_report
from .toml_nodes import from_toml_nodes, to_toml_nodes


def from_tree_json(path: str | Path) -> DeviceTree:
    """Read a ``jumpstarter.dev/device-tree: v1`` JSON file."""
    path = Path(path)
    try:
        tree = DeviceTree.read(path)
    except FileNotFoundError:
        raise SourceError(f"device tree {path} does not exist") from None
    except TreeError as e:
        raise SourceError(f"{path}: {e}") from None
    tree.source.setdefault("kind", "tree")
    tree.source.setdefault("ref", str(path))
    return tree


__all__ = [
    "ExporterClassError",
    "SourceError",
    "from_describe",
    "from_exporter_class",
    "from_exporter_config",
    "from_report",
    "from_toml_nodes",
    "from_tree_json",
    "to_toml_nodes",
]
