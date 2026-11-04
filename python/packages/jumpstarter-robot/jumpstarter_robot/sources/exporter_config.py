"""Device trees from a local exporter config, offline.

Each ``export:`` entry names a driver class; its ``client()`` classmethod
names the client class, so the tree comes from the installed packages without
constructing a single driver (which would open ports or start tools).
Children that a driver builds itself are not in the config and need a live
source (``--capture``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..configs import load_exporter_config
from ..tree import COMPOSITE_CLIENT, COMPOSITE_DRIVER, DeviceNode, DeviceTree, pascal_case
from ._common import class_path, driver_node, opaque_node
from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1DriverInstance,
    ExporterConfigV1Alpha1DriverInstanceBase,
    ExporterConfigV1Alpha1DriverInstanceComposite,
    ExporterConfigV1Alpha1DriverInstanceProxy,
)

Export = dict[str, ExporterConfigV1Alpha1DriverInstance]


def _is_named(cls: type, name: str) -> bool:
    return any(k.__name__ == name and k.__module__.startswith("jumpstarter") for k in cls.__mro__)


def _lookup_ref(export: Export, reference: str) -> Any:
    """Follow a dotted ``ref:`` from the export root through ``children:``."""
    segments = [s for s in reference.split(".") if s]
    if not segments or segments[0] not in export:
        return None
    node = export[segments[0]].root
    for segment in segments[1:]:
        children = getattr(node, "children", None)
        if not children or segment not in children:
            return None
        node = children[segment].root
    return node


class _Walker:
    def __init__(self, export: Export):
        self.export = export
        self.warnings: list[str] = []

    def node(self, path: list[str], instance: Any, seen: tuple[str, ...] = ()) -> DeviceNode:
        match instance:
            case ExporterConfigV1Alpha1DriverInstanceBase():
                return self.base(path, instance)
            case ExporterConfigV1Alpha1DriverInstanceComposite():
                return driver_node(
                    path,
                    COMPOSITE_CLIENT,
                    kind="composite",
                    driver_type=COMPOSITE_DRIVER,
                    children=self.children(path, instance.children),
                )
            case ExporterConfigV1Alpha1DriverInstanceProxy():
                return self.proxy(path, instance.ref, seen)
        return opaque_node(path, f"unknown export entry {type(instance).__name__}", self.warnings)

    def children(self, path: list[str], children: Export) -> list[DeviceNode]:
        return [self.node([*path, name], child.root) for name, child in children.items()]

    def base(self, path: list[str], instance: ExporterConfigV1Alpha1DriverInstanceBase) -> DeviceNode:
        from jumpstarter.common.exceptions import MissingDriverError
        from jumpstarter.common.importlib import import_class

        try:
            driver_class = import_class(instance.type, [], True)
        except (MissingDriverError, ImportError, AttributeError) as e:
            return opaque_node(path, f"driver {instance.type} cannot be imported here ({e})", self.warnings)
        try:
            client = class_path(str(driver_class.client()))
        except Exception as e:  # noqa: BLE001 - any failure leaves the node untyped, with the reason
            return opaque_node(path, f"{instance.type}.client() failed: {e}", self.warnings)
        methods = list((instance.config.get("methods") or {}).keys()) if _is_named(driver_class, "Shell") else []
        kind = "composite" if _is_named(driver_class, "Composite") and client == COMPOSITE_CLIENT else "driver"
        return driver_node(
            path,
            client,
            kind=kind,
            methods=methods,
            driver_type=instance.type,
            children=self.children(path, instance.children),
        )

    def proxy(self, path: list[str], reference: str, seen: tuple[str, ...]) -> DeviceNode:
        """A ``ref:`` proxy is its target under the proxy's name (the runtime report delegates to it)."""
        if reference in seen:
            return opaque_node(path, f"proxy ref {reference!r} is circular", self.warnings)
        target = _lookup_ref(self.export, reference)
        if target is None:
            return opaque_node(path, f"proxy ref {reference!r} not found in the export tree", self.warnings)
        return self.node(path, target, (*seen, reference))


def from_exporter_config(value: str | Path, *, base_dir: Path | None = None) -> DeviceTree:
    """Read the tree of an exporter config, by alias (``jmp config exporter list``) or path.

    ``ref:`` proxies resolve to their target, ``children:`` under drivers are
    kept, children-only entries and ``Composite`` drivers are composite
    nodes, and a shell driver's configured ``methods`` become its methods.
    Drivers that are not installed become opaque nodes, with a warning.
    """
    config = load_exporter_config(str(value), base_dir=base_dir)
    walker = _Walker(config.export)
    nodes = walker.children([], config.export)
    return DeviceTree(
        device_name=pascal_case(config.metadata.name or "device") or "Device",
        nodes=nodes,
        source={"kind": "exporter-config", "ref": str(value)},
        warnings=walker.warnings,
    )
