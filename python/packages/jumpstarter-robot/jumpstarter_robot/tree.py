"""The device tree every client-resource source produces and the renderer reads.

A ``DeviceTree`` is the exporter's driver tree as typed codegen sees it: one
``DeviceNode`` per driver, with its position (``path``), what it is (``kind``)
and the Python client class that drives it (``bindings["python"]``). Sources
(``jumpstarter_robot.sources``) build it from an exporter config or a live
exporter; ``jumpstarter_robot.render`` turns it into a client resource.

``path`` is the full name path from the export root (``["dut", "power"]``),
the same path ``Session.resolve`` takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

NodeKind = Literal["driver", "composite", "opaque"]
PYTHON = "python"

COMPOSITE_CLIENT = "jumpstarter_driver_composite.client.CompositeClient"


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


@dataclass
class DeviceTree:
    """A resolved device: its export roots plus where they came from."""

    device_name: str
    nodes: list[DeviceNode]
    source: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def walk(self) -> Iterator[DeviceNode]:
        for node in self.nodes:
            yield from node.walk()

    def find(self, path: str | list[str]) -> DeviceNode | None:
        wanted = path.split(".") if isinstance(path, str) else list(path)
        return next((node for node in self.walk() if node.path == wanted), None)

