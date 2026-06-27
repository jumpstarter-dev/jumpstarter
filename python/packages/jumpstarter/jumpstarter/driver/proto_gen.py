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
    interfaces = _discover_interfaces(list(args.import_package))

    if not interfaces:
        print(
            "No interface classes found. Pass --import-package for each driver "
            "module to scan (e.g. jumpstarter_driver_power.driver).",
            file=sys.stderr,
        )
        return 1

    for key, cls in sorted(interfaces.items()):
        fd = build_file_descriptor(cls, version=args.version)
        proto_source = render_proto_source(fd)

        rel_path = _proto_path_for(fd)
        filepath = os.path.join(args.output_dir, rel_path)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            f.write(proto_source)
        print(f"Generated {filepath} ({key})")
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
    gen_all.set_defaults(func=_cmd_generate_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
