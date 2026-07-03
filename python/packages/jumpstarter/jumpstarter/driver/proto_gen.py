"""
Generate driver interface .proto sources from Python interface classes.

Runnable as ``python -m jumpstarter.driver.proto_gen`` with two subcommands:

    generate      one interface (dotted path) -> stdout or a file
    generate-all  every interface in the given packages -> an output dir

``generate-all`` writes each interface to a nested path mirroring its proto
package: for package ``jumpstarter.interfaces.<name>.v1`` it writes
``<output-dir>/jumpstarter/interfaces/<name>/v1/<name>.proto``.

These are the language-agnostic interface contracts. The Python driver packages
do NOT vendor them — regenerate into the dedicated ``interfaces/`` package with
``make -C interfaces generate``.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import os
import sys

from .descriptor_builder import build_file_descriptor
from .proto_render import render_proto_source


def _load_interface_class(interface: str) -> type:
    """Load an interface class from a dotted import path.

    Accepts either "package.module.ClassName" or "package.module:ClassName".
    """
    if ":" in interface:
        module_path, class_name = interface.rsplit(":", 1)
    elif "." in interface:
        module_path, class_name = interface.rsplit(".", 1)
    else:
        raise SystemExit(
            f"Invalid interface path '{interface}'. "
            "Use 'package.module.ClassName' or 'package.module:ClassName'."
        )

    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise SystemExit(f"Could not import module '{module_path}': {e}") from e

    cls = getattr(module, class_name, None)
    if cls is None:
        raise SystemExit(f"Class '{class_name}' not found in module '{module_path}'.")
    return cls


def _is_interface_class(obj: object, module_name: str) -> bool:
    """Whether ``obj`` is a driver interface class defined in ``module_name``.

    An interface is the abstract per-interface contract: a class with a
    ``client()`` classmethod that is NOT a concrete ``Driver`` subclass and is
    defined directly in the imported module (not merely imported into it). This
    selects e.g. ``PowerInterface`` while excluding the base ``Driver`` and the
    concrete drivers (``MockPower``) that implement the interface.
    """
    from .base import Driver

    if not isinstance(obj, type):
        return False
    if getattr(obj, "__module__", None) != module_name:
        return False
    client = inspect.getattr_static(obj, "client", None)
    if not isinstance(client, classmethod):
        return False
    if issubclass(obj, Driver):
        return False
    return True


def _discover_interfaces(packages: list[str]) -> dict[str, type]:
    """Import the given packages and collect their interface classes.

    Returns a mapping of fully-qualified class name -> class, de-duplicated so a
    class re-exported from several modules is only emitted once.
    """
    found: dict[str, type] = {}
    for pkg in packages:
        try:
            module = importlib.import_module(pkg)
        except ImportError as e:
            raise SystemExit(f"Could not import '{pkg}': {e}") from e

        for obj in vars(module).values():
            if _is_interface_class(obj, module.__name__):
                key = f"{obj.__module__}.{obj.__name__}"
                found.setdefault(key, obj)
    return found


def _proto_path_for(fd) -> str:
    """Nested proto path mirroring the descriptor's package.

    For package ``jumpstarter.interfaces.<name>.v1`` returns
    ``jumpstarter/interfaces/<name>/v1/<name>.proto``. ``fd.name`` is already the
    bare ``<name>.proto`` produced by build_file_descriptor.
    """
    parts = fd.package.split(".")
    return os.path.join(*parts, fd.name)


def _discover_drivers(packages: list[str]) -> dict[str, type]:
    """Import the given modules and collect their CONCRETE ``Driver`` subclasses.

    These are the classes exporter configs name in ``type:`` — the registry's key space.
    Returns fully-qualified class path -> class, deduplicated, module-defined classes only.
    """
    from .base import Driver

    found: dict[str, type] = {}
    for pkg in packages:
        try:
            module = importlib.import_module(pkg)
        except ImportError as e:
            raise SystemExit(f"Could not import '{pkg}': {e}") from e

        for obj in vars(module).values():
            if not isinstance(obj, type) or getattr(obj, "__module__", None) != module.__name__:
                continue
            if not issubclass(obj, Driver) or inspect.isabstract(obj):
                continue
            found.setdefault(f"{obj.__module__}.{obj.__name__}", obj)
    return found


def _interfaces_for_export(packages: list[str]) -> dict[str, type]:
    """The full set of interface classes to export as .proto contracts.

    The union of the explicit interface ABCs (:func:`_discover_interfaces`) and each concrete
    driver's resolved interface — ``resolve_interface_class_of(cls) or cls`` — which is exactly
    the surface the exporter host advertises at runtime, so every registry FQN has a committed
    proto. Proto-first interfaces (generated ``ProtoInterface`` bases) are skipped: their
    committed ``.proto`` IS the source of truth, not a re-export target.
    """
    from .descriptor_builder import resolve_interface_class_of
    from .proto_interface import ProtoInterface

    found = _discover_interfaces(packages)
    for cls in _discover_drivers(packages).values():
        iface = resolve_interface_class_of(cls) or cls
        if issubclass(iface, ProtoInterface):
            continue
        found.setdefault(f"{iface.__module__}.{iface.__name__}", iface)
    return found


def _client_language(label: str) -> str:
    """The language a `jumpstarter.dev/client` label targets, by its prefix convention."""
    if label.startswith("rust:"):
        return "rust"
    if label.startswith("jvm:"):
        return "jvm"
    return "python"


def build_registry(drivers: dict[str, type], version: str | None = None) -> dict:
    """Build the driver-registry data for :func:`render_registry_yaml`.

    INTERFACE-keyed (the interface is the source of truth): one entry per proto service, carrying
    its proto path and the driver ``type:`` strings implementing it. Per driver: derived exactly
    the way the exporter host advertises at runtime (``resolve_interface_class_of(cls) or cls`` →
    ``build_file_descriptor``), plus the advertised custom client. Labels pointing into a
    ``._generated.`` module are the codegen DEFAULT client, not a custom one — omitted, so
    device codegen emits its own typed client instead of importing the driver package's copy.
    """
    from .descriptor_builder import resolve_interface_class_of

    interfaces: dict[str, dict] = {}
    for type_path, cls in sorted(drivers.items()):
        interface_cls = resolve_interface_class_of(cls) or cls
        try:
            fd = build_file_descriptor(interface_cls, version=version)
        except Exception as e:  # noqa: BLE001 — skip un-introspectable drivers, keep exporting
            print(f"Skipping {type_path}: cannot build descriptor ({e})", file=sys.stderr)
            continue
        if not fd.service:
            print(f"Skipping {type_path}: no service in descriptor", file=sys.stderr)
            continue
        fqn = f"{fd.package}.{fd.service[0].name}"
        entry = interfaces.setdefault(
            fqn,
            {"name": fqn, "proto": _proto_path_for(fd).replace(os.sep, "/"), "drivers": []},
        )

        try:
            label = cls.client()
        except Exception:  # noqa: BLE001 — e.g. Proxy.client() raises by design
            label = None
        if label and "._generated." not in label:
            entry["drivers"].append({"name": type_path, "clients": {_client_language(label): label}})
        else:
            entry["drivers"].append(type_path)

    return {"version": 1, "interfaces": [interfaces[k] for k in sorted(interfaces)]}


def render_registry_yaml(registry: dict) -> str:
    """Render the registry deterministically as YAML (no pyyaml dependency).

    Interface entries are a list (``- name:``), so no value ever needs to be a map key — plain
    scalars throughout (driver types like ``rust:power`` are safe as values).
    """
    lines = [
        "# @generated by `python -m jumpstarter.driver.proto_gen generate-all --registry-out`.",
        "# DO NOT EDIT — regenerate with `make -C interfaces generate`.",
        f"version: {registry['version']}",
        "interfaces:",
    ]
    for entry in registry["interfaces"]:
        lines.append(f"  - name: {entry['name']}")
        lines.append(f"    proto: {entry['proto']}")
        if entry["drivers"]:
            lines.append("    drivers:")
        for driver in entry["drivers"]:
            if isinstance(driver, str):
                lines.append(f"      - {driver}")
                continue
            lines.append(f"      - name: {driver['name']}")
            lines.append("        clients:")
            for lang, label in sorted(driver["clients"].items()):
                lines.append(f"          {lang}: {label}")
    return "\n".join(lines) + "\n"


def _cmd_generate(args: argparse.Namespace) -> int:
    cls = _load_interface_class(args.interface)
    fd = build_file_descriptor(cls, version=args.version)
    proto_source = render_proto_source(fd)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w") as f:
            f.write(proto_source)
        print(f"Generated {args.output}")
    else:
        sys.stdout.write(proto_source)
    return 0


def _cmd_generate_all(args: argparse.Namespace) -> int:
    interfaces = _interfaces_for_export(list(args.import_package))

    if not interfaces:
        print(
            "No interface classes found. Pass --import-package for each driver "
            "module to scan (e.g. jumpstarter_driver_power.driver).",
            file=sys.stderr,
        )
        return 1

    written: dict[str, tuple[str, str]] = {}  # rel_path -> (defining class, source)
    for key, cls in sorted(interfaces.items()):
        fd = build_file_descriptor(cls, version=args.version)
        proto_source = render_proto_source(fd)

        rel_path = _proto_path_for(fd)
        if rel_path in written:
            prev_key, prev_source = written[rel_path]
            if proto_source != prev_source:
                # Two DIFFERENT classes claim the same proto package+service with different
                # contracts — the committed proto can only match one of them. Keep the first
                # (sorted, deterministic) and surface the conflict loudly.
                print(
                    f"WARNING: {key} conflicts with {prev_key} for {rel_path}; "
                    f"keeping {prev_key}'s contract",
                    file=sys.stderr,
                )
            continue
        written[rel_path] = (key, proto_source)
        filepath = os.path.join(args.output_dir, rel_path)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(proto_source)
        print(f"Generated {filepath} ({key})")

    if args.registry_out:
        drivers = _discover_drivers(list(args.import_package))
        registry = build_registry(drivers, version=args.version)
        os.makedirs(os.path.dirname(os.path.abspath(args.registry_out)), exist_ok=True)
        with open(args.registry_out, "w") as f:
            f.write(render_registry_yaml(registry))
        count = sum(len(e["drivers"]) for e in registry["interfaces"])
        print(f"Generated {args.registry_out} ({count} drivers, {len(registry['interfaces'])} interfaces)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jumpstarter.driver.proto_gen",
        description="Generate driver interface .proto sources from Python interface classes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "generate",
        help="Generate a .proto from a single interface class.",
    )
    gen.add_argument(
        "--interface",
        "-i",
        required=True,
        help="Dotted import path of the interface class "
        "(e.g. jumpstarter_driver_power.driver.PowerInterface).",
    )
    gen.add_argument(
        "--version",
        "-v",
        default="v1",
        help="Version string for the proto package (default: v1).",
    )
    gen.add_argument(
        "--output",
        "-o",
        default=None,
        help="Output file path. Writes to stdout if not specified.",
    )
    gen.set_defaults(func=_cmd_generate)

    gen_all = sub.add_parser(
        "generate-all",
        help="Generate .proto files for every interface in the given packages.",
    )
    gen_all.add_argument(
        "--output-dir",
        "-d",
        required=True,
        help="Output directory. Files are written under a nested package path.",
    )
    gen_all.add_argument(
        "--version",
        "-v",
        default="v1",
        help="Version string for the proto package (default: v1).",
    )
    gen_all.add_argument(
        "--import-package",
        "-p",
        action="append",
        default=[],
        help="Python module to import and scan for interfaces "
        "(repeatable, e.g. jumpstarter_driver_power.driver).",
    )
    gen_all.add_argument(
        "--registry-out",
        default=None,
        help="Also emit the driver registry (exporter-config `type:` -> interface FQN, proto "
        "path, and advertised client) to this YAML file — consumed by "
        "`jumpstarter-codegen --kind device` for proto-only resolution.",
    )
    gen_all.set_defaults(func=_cmd_generate_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
