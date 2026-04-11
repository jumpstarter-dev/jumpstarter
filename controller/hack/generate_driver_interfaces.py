#!/usr/bin/env python3
"""Generate DriverInterface YAML manifests for all bundled driver interfaces.

Discovers all registered DriverInterface classes (populated via entry points
or explicit imports), calls build_file_descriptor() for each, and writes
Kubernetes DriverInterface YAML manifests to the specified output directories.

Usage:
    python generate_driver_interfaces.py [--output-dir DIR]...

If no --output-dir is given, YAML is written to stdout.
"""

import argparse
import base64
import importlib
import os
import re
import sys

# Ensure the jumpstarter packages are importable
# (assumes uv or pip install in the python/ workspace)


def _to_snake_case(name: str) -> str:
    """Convert PascalCase to snake_case."""
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


def _interface_crd_name(pascal_name: str, version: str) -> str:
    """Derive the DriverInterface CRD name from the interface name and version.

    E.g., "Power" + "v1" -> "dev-jumpstarter-power-v1"
    """
    snake = _to_snake_case(pascal_name).replace("_", "-")
    return f"dev-jumpstarter-{snake}-{version}"


def _find_driver_package(interface_class: type) -> str | None:
    """Find the pip package name for a driver interface class.

    Uses the module path to guess the package name, e.g.
    jumpstarter_driver_power.driver -> jumpstarter-driver-power
    """
    module = interface_class.__module__
    # e.g. "jumpstarter_driver_power.driver" -> "jumpstarter_driver_power"
    top_level = module.split(".")[0]
    return top_level.replace("_", "-")


def _find_driver_classes(interface_class: type) -> list[str]:
    """Find concrete Driver subclasses that implement this interface.

    Searches the same module for classes that inherit from both
    the interface and Driver.
    """
    from jumpstarter.driver.base import Driver

    module_name = interface_class.__module__
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return []

    driver_classes = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (
            isinstance(attr, type)
            and issubclass(attr, interface_class)
            and issubclass(attr, Driver)
            and attr is not interface_class
        ):
            driver_classes.append(f"{attr.__module__}:{attr.__qualname__}")
    return sorted(driver_classes)


def generate_driverinterface_yaml(interface_class: type) -> str:
    """Generate a DriverInterface YAML manifest for a single interface class."""
    from jumpstarter.driver.descriptor_builder import build_file_descriptor

    # Get interface metadata
    pascal_name = getattr(interface_class, "__interface_name__", None)
    if not pascal_name:
        pascal_name = interface_class.__name__
        if pascal_name.endswith("Interface"):
            pascal_name = pascal_name[: -len("Interface")]

    version = getattr(interface_class, "__interface_version__", None) or "v1"

    # Build the file descriptor
    fd = build_file_descriptor(interface_class, version=version)
    descriptor_bytes = fd.SerializeToString()
    descriptor_b64 = base64.b64encode(descriptor_bytes).decode("ascii")

    # CRD name and proto package
    crd_name = _interface_crd_name(pascal_name, version)
    proto_package = fd.package

    # Find the client class
    client_path = interface_class.client()

    # Find driver package info
    driver_package = _find_driver_package(interface_class)
    driver_classes = _find_driver_classes(interface_class)

    # Build YAML
    lines = [
        "apiVersion: jumpstarter.dev/v1alpha1",
        "kind: DriverInterface",
        "metadata:",
        f"  name: {crd_name}",
        "spec:",
        "  proto:",
        f"    package: {proto_package}",
        f"    descriptor: {descriptor_b64}",
        "  drivers:",
        "    - language: python",
        f"      package: {driver_package}",
        f'      clientClass: "{client_path}"',
    ]

    if driver_classes:
        lines.append("      driverClasses:")
        for dc in driver_classes:
            lines.append(f'        - "{dc}"')

    return "\n".join(lines) + "\n"


def discover_interfaces() -> dict[str, type]:
    """Discover all registered DriverInterface classes.

    First tries entry points (jumpstarter.drivers), then falls back
    to the DriverInterfaceMeta registry.
    """
    # Import known bundled driver modules to populate the registry
    _BUNDLED_DRIVER_MODULES = [
        "jumpstarter_driver_power.driver",
        "jumpstarter_driver_network.driver",
        "jumpstarter_driver_opendal.driver",
        "jumpstarter_driver_composite.driver",
        "jumpstarter_driver_adb.driver",
        "jumpstarter_driver_corellium.driver",
    ]

    for mod in _BUNDLED_DRIVER_MODULES:
        try:
            importlib.import_module(mod)
        except ImportError:
            # Driver not installed, skip
            pass

    from jumpstarter.driver.interface import DriverInterfaceMeta

    return dict(DriverInterfaceMeta._registry)


def main():
    parser = argparse.ArgumentParser(
        description="Generate DriverInterface YAML manifests for bundled drivers."
    )
    parser.add_argument(
        "--output-dir",
        action="append",
        default=[],
        help="Output directory for generated YAML files. Can be specified multiple times.",
    )
    args = parser.parse_args()

    interfaces = discover_interfaces()
    if not interfaces:
        print("No registered DriverInterface classes found.", file=sys.stderr)
        print("Are the bundled driver packages installed?", file=sys.stderr)
        sys.exit(1)

    generated = []
    for key, cls in sorted(interfaces.items()):
        try:
            yaml_content = generate_driverinterface_yaml(cls)
        except Exception as e:
            print(f"Warning: failed to generate YAML for {key}: {e}", file=sys.stderr)
            continue

        # Derive filename from CRD name
        pascal_name = getattr(cls, "__interface_name__", None)
        if not pascal_name:
            pascal_name = cls.__name__
            if pascal_name.endswith("Interface"):
                pascal_name = pascal_name[: -len("Interface")]
        version = getattr(cls, "__interface_version__", None) or "v1"
        crd_name = _interface_crd_name(pascal_name, version)
        filename = f"driverinterface-{crd_name}.yaml"

        if args.output_dir:
            for out_dir in args.output_dir:
                os.makedirs(out_dir, exist_ok=True)
                filepath = os.path.join(out_dir, filename)
                with open(filepath, "w") as f:
                    f.write(yaml_content)
                print(f"Generated {filepath} ({key})")
        else:
            print(f"---")
            print(f"# {key}")
            print(yaml_content)

        generated.append(key)

    if not generated:
        print("No DriverInterface YAML files generated.", file=sys.stderr)
        sys.exit(1)

    print(f"\nGenerated {len(generated)} DriverInterface YAML manifests.", file=sys.stderr)


if __name__ == "__main__":
    main()
