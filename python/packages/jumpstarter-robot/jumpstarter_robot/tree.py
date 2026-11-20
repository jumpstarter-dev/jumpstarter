"""The device tree every client-resource source produces and the renderer reads.

A ``DeviceTree`` is the exporter's driver tree as typed codegen sees it: one
``DeviceNode`` per driver, with its position (``path``), what it is (``kind``)
and the Python client class that drives it (``bindings["python"]``). Sources
(``jumpstarter_robot.sources``) build it from a live exporter, an exporter
config, an ExporterClass, a committed ``nodes`` table or a ``jmp describe``
dump; ``jumpstarter_robot.render`` turns it into a client resource.

It mirrors rust-core's ``ResolvedDevice`` / ``ResolvedNode`` field for field
(``jumpstarter-codegen/src/resolver.rs``) and adds what Robot Framework needs
on top: ``required``, shell ``methods`` and the root shape the typed wrappers
use. Its JSON form carries the header ``"jumpstarter.dev/device-tree": "v1"``
so other codegen frontends can hand a tree over.

``path`` is the full name path from the export root (``["dut", "power"]``),
the same as rust-core and the same path ``Session.resolve`` takes. The typed
accessor path drops the root entry of a single-composite exporter
(``rig.power`` for ``dut.power``); ``DeviceTree.accessor`` computes it.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

HEADER = "jumpstarter.dev/device-tree"
VERSION = "v1"

NodeKind = Literal["driver", "composite", "opaque"]
RootShape = Literal["single_driver", "single_composite", "forest"]
NODE_KINDS = ("driver", "composite", "opaque")
ROOT_SHAPES = ("single_driver", "single_composite", "forest")
PYTHON = "python"

COMPOSITE_CLIENT = "jumpstarter_driver_composite.client.CompositeClient"
COMPOSITE_DRIVER = "jumpstarter_driver_composite.driver.Composite"


class TreeError(ValueError):
    """A device tree (or its JSON) is malformed."""


@dataclass
class DeviceNode:
    """One driver of the tree.

    ``kind`` is ``driver`` for a driver with a client, ``composite`` for a
    pure grouping node, and ``opaque`` for a node no client could be found
    for (``reason`` says why); renderers skip opaque nodes.
    """

    name: str
    path: list[str]
    kind: NodeKind = "driver"
    interface: str | None = None
    driver_type: str | None = None
    bindings: dict[str, str] = field(default_factory=dict)
    required: bool = True
    methods: list[str] = field(default_factory=list)
    children: list[DeviceNode] = field(default_factory=list)
    reason: str | None = None

    @property
    def dotted(self) -> str:
        return ".".join(self.path)

    @property
    def client(self) -> str | None:
        """The Python client class path, if any."""
        return self.bindings.get(PYTHON)

    def walk(self) -> Iterator[DeviceNode]:
        """This node and every node below it, parents first."""
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "path": list(self.path),
            "kind": self.kind,
            "interface": self.interface,
            "driver_type": self.driver_type,
            "bindings": dict(self.bindings),
            "required": self.required,
            "methods": list(self.methods),
        }
        if self.reason is not None:
            data["reason"] = self.reason
        data["children"] = [child.to_dict() for child in self.children]
        return data

    @classmethod
    def from_dict(cls, data: Any, parent: list[str] | None = None) -> DeviceNode:
        if not isinstance(data, dict):
            raise TreeError(f"a device tree node must be an object, not {type(data).__name__}")
        try:
            name = str(data["name"])
        except KeyError:
            raise TreeError("a device tree node has no 'name'") from None
        path = [str(segment) for segment in data.get("path") or [*(parent or []), name]]
        kind = data.get("kind", "driver")
        if kind not in NODE_KINDS:
            raise TreeError(f"node '{'.'.join(path)}': kind must be one of {', '.join(NODE_KINDS)}, not '{kind}'")
        bindings = data.get("bindings") or {}
        if not isinstance(bindings, dict):
            raise TreeError(f"node '{'.'.join(path)}': bindings must be an object of language to class")
        return cls(
            name=name,
            path=path,
            kind=kind,
            interface=data.get("interface"),
            driver_type=data.get("driver_type"),
            bindings={str(k): str(v) for k, v in bindings.items()},
            required=bool(data.get("required", True)),
            methods=[str(m) for m in data.get("methods") or []],
            children=[cls.from_dict(child, path) for child in data.get("children") or []],
            reason=data.get("reason"),
        )


@dataclass
class DeviceTree:
    """A resolved device: its export roots plus where they came from."""

    device_name: str
    nodes: list[DeviceNode]
    root_shape: RootShape | None = None
    root_entry: str | None = None
    source: dict[str, Any] = field(default_factory=dict)
    interfaces: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.root_shape is None:
            self.root_shape, self.root_entry = infer_root_shape(self.nodes)

    def walk(self) -> Iterator[DeviceNode]:
        for node in self.nodes:
            yield from node.walk()

    def find(self, path: str | list[str]) -> DeviceNode | None:
        wanted = path.split(".") if isinstance(path, str) else list(path)
        return next((node for node in self.walk() if node.path == wanted), None)

    def accessor(self, node: DeviceNode) -> list[str]:
        """The typed accessor path of ``node``: the root entry of a single composite is dropped.

        The root entry itself keeps its name, so it still has a readable prefix.
        """
        path = list(node.path)
        if self.root_shape == "single_composite" and len(path) > 1 and path[0] == self.root_entry:
            return path[1:]
        return path or [node.name]

    def to_dict(self) -> dict[str, Any]:
        return {
            HEADER: VERSION,
            "device_name": self.device_name,
            "root_shape": self.root_shape,
            "root_entry": self.root_entry,
            "source": dict(self.source),
            "interfaces": dict(self.interfaces),
            "warnings": list(self.warnings),
            "nodes": [node.to_dict() for node in self.nodes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    def write(self, path: str | Path) -> Path:
        path = Path(path)
        path.write_text(self.to_json())
        return path

    @classmethod
    def from_dict(cls, data: Any) -> DeviceTree:
        if not isinstance(data, dict):
            raise TreeError("a device tree must be a JSON object")
        version = data.get(HEADER)
        if version != VERSION:
            raise TreeError(f"not a device tree: expected \"{HEADER}\": \"{VERSION}\", found {version!r}")
        shape = data.get("root_shape")
        if shape is not None and shape not in ROOT_SHAPES:
            raise TreeError(f"root_shape must be one of {', '.join(ROOT_SHAPES)}, not '{shape}'")
        nodes = [DeviceNode.from_dict(node) for node in data.get("nodes") or []]
        return cls(
            device_name=str(data.get("device_name") or "Device"),
            nodes=nodes,
            root_shape=shape,
            root_entry=data.get("root_entry"),
            source=dict(data.get("source") or {}),
            interfaces=dict(data.get("interfaces") or {}),
            warnings=[str(w) for w in data.get("warnings") or []],
        )

    @classmethod
    def from_json(cls, text: str) -> DeviceTree:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise TreeError(f"device tree is not valid JSON: {e}") from None
        return cls.from_dict(data)

    @classmethod
    def read(cls, path: str | Path) -> DeviceTree:
        return cls.from_json(Path(path).read_text())


def infer_root_shape(nodes: list[DeviceNode]) -> tuple[RootShape, str | None]:
    """rust-core's ``root_shape``: one typed export entry roots the device on it, otherwise a forest."""
    if len(nodes) == 1 and nodes[0].kind != "opaque":
        root = nodes[0]
        return ("single_composite" if root.kind == "composite" else "single_driver"), root.name
    return "forest", None


def pascal_case(name: str) -> str:
    """rust-core's ``pascal_case``: ``power-board`` / ``power_board`` / ``powerBoard`` → ``PowerBoard``."""
    out = []
    upper_next = True
    for char in name:
        if char in "-_. ":
            upper_next = True
        elif upper_next:
            out.append(char.upper())
            upper_next = False
        else:
            out.append(char)
    return "".join(out)
