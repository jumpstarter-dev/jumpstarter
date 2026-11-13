"""Device trees from an ExporterClass and its DriverInterfaces.

Two shapes are read:

- the flat jep-12/14 prototype: ``ExporterClass.spec.interfaces[]`` naming
  DriverInterfaces whose ``spec.drivers[]`` entry for ``language: python``
  carries a ``clientClass`` (dotted or ``module:Class``)
- JEP-0015: the same root ``interfaces[]``, with composite interfaces
  declaring their children in ``DriverInterface.spec.children`` (recursively)
  and ExporterClasses inheriting root entries through ``spec.extends``

JEP-0015 leaves the client class to a later registry, so a DriverInterface
without a Python ``clientClass`` hint needs its class from the committed
``nodes`` mapping (``clients=``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from ..tree import COMPOSITE_CLIENT, DeviceNode, DeviceTree, pascal_case
from ._common import SourceError, class_path, driver_node

EXPORTER_CLASS = "ExporterClass"
DRIVER_INTERFACE = "DriverInterface"


class ExporterClassError(SourceError):
    """An ExporterClass or one of its DriverInterfaces cannot be resolved."""


def _documents(path: Path) -> list[dict[str, Any]]:
    try:
        return [doc for doc in yaml.safe_load_all(path.read_text()) if isinstance(doc, dict)]
    except yaml.YAMLError as e:
        raise ExporterClassError(f"{path}: not valid YAML: {e}") from None


def _yaml_files(directory: Path, recursive: bool = True) -> list[Path]:
    if not directory.is_dir():
        raise ExporterClassError(f"driver interface directory {directory} does not exist")
    found = directory.rglob("*") if recursive else directory.glob("*")
    return sorted(p for p in found if p.suffix in (".yaml", ".yml") and p.is_file())


def _name(doc: dict[str, Any]) -> str:
    return str((doc.get("metadata") or {}).get("name") or "")


def _python_client(spec: Mapping[str, Any]) -> str | None:
    """The ``clientClass`` hint for Python: ``spec.drivers[]`` (a list or a language-keyed mapping)."""
    drivers = spec.get("drivers") or []
    if isinstance(drivers, Mapping):
        drivers = [{"language": language, **(entry or {})} for language, entry in drivers.items()]
    for entry in drivers:
        if isinstance(entry, Mapping) and entry.get("language", "python") == "python" and entry.get("clientClass"):
            return class_path(str(entry["clientClass"]))
    if spec.get("clientClass"):
        return class_path(str(spec["clientClass"]))
    return None


class _Catalog:
    """Every ExporterClass and DriverInterface found, by ``metadata.name``."""

    def __init__(self, files: Iterable[Path], optional: Iterable[Path] = ()):
        self.classes: dict[str, dict[str, Any]] = {}
        self.interfaces: dict[str, dict[str, Any]] = {}
        self.searched: list[str] = []
        skip_errors = set(optional)
        for path in files:
            self.searched.append(str(path))
            try:
                docs = _documents(path)
            except ExporterClassError:
                if path in skip_errors:
                    continue  # an unrelated YAML next to the ExporterClass file
                raise
            for doc in docs:
                kind = doc.get("kind")
                if kind == EXPORTER_CLASS:
                    self.classes.setdefault(_name(doc), doc)
                elif kind == DRIVER_INTERFACE:
                    self.interfaces.setdefault(_name(doc), doc)

    def interface(self, name: str, path: list[str]) -> dict[str, Any]:
        try:
            return self.interfaces[name]
        except KeyError:
            raise ExporterClassError(
                f"node '{'.'.join(path)}': DriverInterface '{name}' not found; "
                "pass the directory holding its YAML with --driver-interface-dir"
            ) from None

    def merged_interfaces(self, name: str, chain: tuple[str, ...] = ()) -> list[dict[str, Any]]:
        """Root ``interfaces[]`` after merging the ``extends`` chain by entry name (child wins)."""
        if name in chain:
            raise ExporterClassError(f"ExporterClass extends chain is circular: {' -> '.join((*chain, name))}")
        try:
            spec = self.classes[name].get("spec") or {}
        except KeyError:
            raise ExporterClassError(f"ExporterClass '{name}' not found in {', '.join(self.searched)}") from None
        merged: dict[str, dict[str, Any]] = {}
        if spec.get("extends"):
            for entry in self.merged_interfaces(str(spec["extends"]), (*chain, name)):
                merged[entry["name"]] = entry
        for entry in spec.get("interfaces") or []:
            merged[str(entry["name"])] = _merge_entry(name, merged.get(str(entry["name"])), entry)
        return list(merged.values())


def _merge_entry(owner: str, parent: dict[str, Any] | None, entry: Mapping[str, Any]) -> dict[str, Any]:
    if parent is None:
        return dict(entry)
    if entry.get("interfaceRef") and parent.get("interfaceRef") and entry["interfaceRef"] != parent["interfaceRef"]:
        raise ExporterClassError(
            f"ExporterClass '{owner}': entry '{entry['name']}' is {entry['interfaceRef']}, "
            f"but the class it extends declares {parent['interfaceRef']}"
        )
    if parent.get("required") and entry.get("required") is False:
        raise ExporterClassError(
            f"ExporterClass '{owner}': entry '{entry['name']}' cannot make a required entry optional"
        )
    return {**parent, **dict(entry)}


class _Resolver:
    def __init__(self, catalog: _Catalog, clients: Mapping[str, str], client_name: str | None):
        self.catalog = catalog
        self.clients = {k: class_path(v) for k, v in clients.items()}
        self.client_name = client_name
        self.interfaces: dict[str, Any] = {}

    def node(self, path: list[str], entry: Mapping[str, Any], required: bool, depth: int = 0) -> DeviceNode:
        if depth > 32:
            raise ExporterClassError(f"node '{'.'.join(path)}': DriverInterface children nest too deeply (a cycle?)")
        ref = str(entry.get("interfaceRef") or "")
        if not ref:
            raise ExporterClassError(f"node '{'.'.join(path)}' has no interfaceRef")
        doc = self.catalog.interface(ref, path)
        spec = doc.get("spec") or {}
        package = (spec.get("proto") or {}).get("package")
        if package:
            self.interfaces.setdefault(package, {"proto_package": package, "driver_interface": ref})
        required = required and bool(entry.get("required", True))
        children = [
            self.node([*path, str(child["name"])], child, required, depth + 1) for child in spec.get("children") or []
        ]
        client = self.clients.get(".".join(path)) or _python_client(spec)
        if client is None and not children:
            raise ExporterClassError(self._missing_client(path, ref))
        return driver_node(
            path,
            client or COMPOSITE_CLIENT,
            kind="composite" if client in (None, COMPOSITE_CLIENT) else "driver",
            required=required,
            interface=package,
            children=children,
        )

    def _missing_client(self, path: list[str], ref: str) -> str:
        dotted = ".".join(path)
        table = f"[tool.jumpstarter.client.{self.client_name or '<name>'}.nodes]"
        return (
            f"node '{dotted}': DriverInterface '{ref}' has no Python clientClass hint "
            "(JEP-0015 leaves the client mapping to a later registry). Add the client class to the "
            f"committed nodes mapping, e.g. {table} \"{dotted}\" = \"package.module.Client\", "
            "or record the mapping from a live exporter with `generate --capture`"
        )


def _pick_class(catalog: _Catalog, own: list[str], name: str | None, file: Path) -> str:
    if name:
        return name
    extended = {str((catalog.classes[c].get("spec") or {}).get("extends") or "") for c in own}
    leaves = [c for c in own if c not in extended]
    if len(leaves) != 1:
        found = ", ".join(own) or "none"
        raise ExporterClassError(f"{file}: name the ExporterClass to use (found: {found}), e.g. {file}#<name>")
    return leaves[0]


def from_exporter_class(
    exporter_class: str | Path,
    driver_interface_dirs: Iterable[str | Path] = (),
    *,
    name: str | None = None,
    clients: Mapping[str, str] | None = None,
    client_name: str | None = None,
) -> DeviceTree:
    """Read the tree an ExporterClass describes.

    ``exporter_class`` is a YAML file (one or more documents; ``file#name``
    picks one ExporterClass). DriverInterfaces and the classes it
    ``extends`` are looked up in that file, its directory and
    ``driver_interface_dirs``. ``clients`` maps dotted paths to client
    classes for interfaces that carry no ``clientClass`` hint.
    """
    text = str(exporter_class)
    if name is None and "#" in text:
        text, name = text.rsplit("#", 1)
    file = Path(text)
    if not file.is_file():
        raise ExporterClassError(f"ExporterClass file {file} does not exist")
    own = [_name(doc) for doc in _documents(file) if doc.get("kind") == EXPORTER_CLASS]
    siblings = [p.resolve() for p in _yaml_files(file.parent, recursive=False) if p.resolve() != file.resolve()]
    files = [file.resolve(), *siblings]
    for directory in driver_interface_dirs:
        files.extend(p.resolve() for p in _yaml_files(Path(directory)))
    catalog = _Catalog(dict.fromkeys(files), optional=siblings)
    chosen = _pick_class(catalog, own, name, file)
    resolver = _Resolver(catalog, clients or {}, client_name)
    nodes = [
        resolver.node([str(entry["name"])], entry, bool(entry.get("required", True)))
        for entry in catalog.merged_interfaces(chosen)
    ]
    return DeviceTree(
        device_name=pascal_case(chosen) or "Device",
        nodes=nodes,
        source={"kind": "exporter-class", "ref": chosen},
        interfaces=resolver.interfaces,
    )
