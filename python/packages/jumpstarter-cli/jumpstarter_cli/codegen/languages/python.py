"""Python language generator for jmp codegen.

Generates ExporterClass-typed device wrappers and pytest test fixtures.

For Python, per-interface clients already exist from `jmp proto generate` (JEP-11).
This generator adds the ExporterClass composition layer on top:
  - DevBoardDevice class composing PowerClient, SerialClient, etc.
  - DevBoardTest pytest base class extending JumpstarterTest
"""

from __future__ import annotations

import re

from ..engine import LanguageGenerator, register_language
from ..models import CodegenContext, DriverInterfaceRef, Optionality


def _snake_to_pascal(name: str) -> str:
    """Convert snake-case or kebab-case name to PascalCase.

    Examples:
        dev-board → DevBoard
        power_control → PowerControl
        serial → Serial
    """
    return "".join(part.capitalize() for part in re.split(r"[-_]", name))


def _resolve_python_import(interface: DriverInterfaceRef) -> tuple[str, str]:
    """Resolve the Python import path and client class name for an interface.

    Returns (import_path, class_name) — e.g.:
        ("jumpstarter_driver_power.client", "PowerClient")

    Resolution order:
    1. DriverInterface CRD driver hint for Python (clientClass + package)
    2. Convention: proto package → Python package + service name → client class
    """
    # Check for explicit Python driver hint from the DriverInterface CRD
    hint = interface.drivers.get("python")
    if hint and hint.client_class:
        # clientClass may be fully qualified (e.g., "jumpstarter_driver_power.client.PowerClient")
        # or just a class name (e.g., "PowerClient")
        client_class = hint.client_class
        if "." in client_class:
            # Split "jumpstarter_driver_power.client.PowerClient" → module + class
            last_dot = client_class.rindex(".")
            module_path = client_class[:last_dot]
            class_name = client_class[last_dot + 1:]
            return module_path, class_name
        elif hint.package:
            return f"{hint.package}.client", client_class

    # Convention-based: derive from proto package and service name
    # Proto package: jumpstarter.interfaces.power.v1
    # Service name: PowerInterface → PowerClient
    # Python package: jumpstarter_driver_power
    service_name = interface.service_name
    client_class = service_name.replace("Interface", "Client")

    # Derive Python package from proto package
    # jumpstarter.interfaces.power.v1 → jumpstarter_driver_power
    parts = interface.proto_package.split(".")
    if len(parts) >= 3 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        # e.g., jumpstarter.interfaces.power.v1 → jumpstarter_driver_power
        iface_name = parts[2]
        package = f"jumpstarter_driver_{iface_name}"
    else:
        # Fallback: use the full proto package with dots replaced
        package = interface.proto_package.replace(".", "_")

    return f"{package}.client", client_class


def _gen_device_wrapper(ctx: CodegenContext) -> str:
    """Generate the device wrapper class source code."""
    ec = ctx.exporter_class
    class_name = _snake_to_pascal(ec.name) + "Device"

    lines: list[str] = [
        '"""Auto-generated typed wrapper for ExporterClass '
        f'{ec.name}.',
        "",
        "Do not edit — regenerate with `jmp codegen` when the ExporterClass changes.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]

    # Collect imports for all interfaces
    imports: list[tuple[str, str]] = []  # (module_path, class_name)
    for iface in ec.interfaces:
        module_path, client_class = _resolve_python_import(iface)
        imports.append((module_path, client_class))

    # Deduplicate and sort imports
    seen: set[tuple[str, str]] = set()
    unique_imports: list[tuple[str, str]] = []
    for imp in imports:
        if imp not in seen:
            seen.add(imp)
            unique_imports.append(imp)
    unique_imports.sort()

    for module_path, client_class in unique_imports:
        lines.append(f"from {module_path} import {client_class}")

    lines.append("")
    lines.append("")

    # Class definition
    lines.append(f"class {class_name}:")
    doc_lines = [
        f'    """Typed device wrapper for ExporterClass {ec.name}.',
        "",
    ]
    if ec.extends:
        doc_lines.append(f"    Extends: {ec.extends}")
        doc_lines.append("")
    doc_lines.append(
        "    Composes per-interface clients into a single device object with"
    )
    doc_lines.append("    named, typed accessors. Required interfaces are non-nullable;")
    doc_lines.append("    optional interfaces may be None.")
    doc_lines.append("")
    doc_lines.append("    Do not edit — regenerate with `jmp codegen`.")
    doc_lines.append('    """')
    lines.extend(doc_lines)
    lines.append("")

    # Type annotations as class-level attributes
    for iface, (_, client_class) in zip(ec.interfaces, imports):
        if iface.optionality == Optionality.OPTIONAL:
            annotation = f"{client_class} | None"
            comment = "optional — may be None"
        else:
            annotation = client_class
            comment = "required — guaranteed by ExporterClass"
        if iface.doc_comment:
            # Use the proto doc comment if available
            comment = iface.doc_comment.strip().split("\n")[0]
        lines.append(f"    {iface.name}: {annotation}  # {comment}")

    lines.append("")

    # __init__ method
    lines.append("    def __init__(self, client):")
    lines.append(f'        """Initialize {class_name} from a connected Jumpstarter client.')
    lines.append("")
    lines.append("        Args:")
    lines.append("            client: The root DriverClient returned by env() or a lease.")
    lines.append("                Children are accessed by name from client.children.")
    lines.append('        """')
    for iface, (_, client_class) in zip(ec.interfaces, imports):
        if iface.optionality == Optionality.OPTIONAL:
            lines.append(
                f'        self.{iface.name} = client.children.get("{iface.name}")'
            )
        else:
            lines.append(
                f'        self.{iface.name} = client.children["{iface.name}"]'
            )

    lines.append("")
    return "\n".join(lines)


def _gen_test_fixture(ctx: CodegenContext) -> str:
    """Generate the pytest test fixture source code."""
    ec = ctx.exporter_class
    device_class_name = _snake_to_pascal(ec.name) + "Device"
    test_class_name = _snake_to_pascal(ec.name) + "Test"

    # Determine the device wrapper import path
    package_base = ctx.package_name or "jumpstarter_gen"
    device_module = f"{package_base}.devices.{ec.name.replace('-', '_')}"

    lines: list[str] = [
        '"""Auto-generated pytest base class for ExporterClass '
        f'{ec.name}.',
        "",
        "Do not edit — regenerate with `jmp codegen --test-fixtures`.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import pytest",
        "",
        "from jumpstarter_testing.pytest import JumpstarterTest",
        "",
        f"from {device_module} import {device_class_name}",
        "",
        "",
        f"class {test_class_name}(JumpstarterTest):",
        f'    """Base class for tests targeting ExporterClass {ec.name}.',
        "",
        "    Inherit from this class and use the `device` fixture for typed access.",
        "    Supports both `jmp shell` (via JUMPSTARTER_HOST) and lease acquisition",
        "    (via `selector` class variable).",
        '    """',
        "",
        f'    selector = "jumpstarter.dev/exporter-class={ec.name}"',
        "",
        '    @pytest.fixture(scope="class")',
        f"    def device(self, client) -> {device_class_name}:",
        f'        """Create a typed {device_class_name} from the connected client."""',
        f"        return {device_class_name}(client)",
        "",
    ]

    return "\n".join(lines)


class PythonLanguageGenerator(LanguageGenerator):
    """Python code generator for jmp codegen.

    For Python, per-interface clients already exist from JEP-11's `jmp proto generate`.
    This generator produces the ExporterClass composition layer:
      1. Device wrapper class composing existing per-interface clients
      2. pytest test fixture extending JumpstarterTest
    """

    @property
    def language_name(self) -> str:
        return "python"

    def generate_interface_client(
        self, ctx: CodegenContext, interface: DriverInterfaceRef,
    ) -> dict[str, str]:
        """Python per-interface clients are already generated by `jmp proto generate`.

        This is a no-op — the device wrapper imports existing client classes.
        """
        return {}

    def generate_device_wrapper(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate the ExporterClass device wrapper module."""
        ec = ctx.exporter_class
        package_base = ctx.package_name or "jumpstarter_gen"
        module_name = ec.name.replace("-", "_")

        source = _gen_device_wrapper(ctx)

        # Output: jumpstarter_gen/devices/{module_name}.py
        # Also generate an __init__.py for the devices subpackage
        rel_dir = package_base.replace(".", "/")
        files = {
            f"{rel_dir}/__init__.py": "",
            f"{rel_dir}/devices/__init__.py": "",
            f"{rel_dir}/devices/{module_name}.py": source,
        }
        return files

    def generate_test_fixture(self, ctx: CodegenContext) -> dict[str, str]:
        """Generate the pytest test fixture module."""
        ec = ctx.exporter_class
        package_base = ctx.package_name or "jumpstarter_gen"
        module_name = ec.name.replace("-", "_")

        source = _gen_test_fixture(ctx)

        rel_dir = package_base.replace(".", "/")
        files = {
            f"{rel_dir}/testing/__init__.py": "",
            f"{rel_dir}/testing/{module_name}.py": source,
        }
        return files


# Register with the engine
register_language("python", PythonLanguageGenerator)
