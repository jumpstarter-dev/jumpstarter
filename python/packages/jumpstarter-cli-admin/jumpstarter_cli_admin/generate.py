import base64
import importlib
import re

import click
import yaml
from jumpstarter_cli_common.alias import AliasedGroup

from jumpstarter.driver.descriptor_builder import build_file_descriptor


def _to_crd_name(package: str) -> str:
    """Convert a proto package name to a CRD name.

    e.g., jumpstarter.interfaces.power.v1 -> dev-jumpstarter-power-v1
    """
    # Strip the common prefix and rebuild as a DNS-compatible name
    parts = package.split(".")
    # jumpstarter.interfaces.<name>.<version> -> dev-jumpstarter-<name>-<version>
    if len(parts) >= 4 and parts[0] == "jumpstarter" and parts[1] == "interfaces":
        return f"dev-jumpstarter-{'-'.join(parts[2:])}"
    # Fallback: replace dots with dashes
    return re.sub(r"[^a-z0-9-]", "-", package.replace(".", "-"))


def _resolve_class(class_path: str) -> type:
    """Resolve a fully qualified class path like 'module.path.ClassName' or 'module.path:ClassName'."""
    if ":" in class_path:
        module_path, class_name = class_path.rsplit(":", 1)
    elif "." in class_path:
        module_path, class_name = class_path.rsplit(".", 1)
    else:
        raise click.ClickException(f"Invalid class path '{class_path}'. Use 'module.path.ClassName' or 'module:Class'.")

    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise click.ClickException(f"Cannot import module '{module_path}': {e}") from e

    cls = getattr(module, class_name, None)
    if cls is None:
        raise click.ClickException(f"Class '{class_name}' not found in module '{module_path}'")

    return cls


@click.group(cls=AliasedGroup)
def generate():
    """Generate Jumpstarter resource YAML from installed drivers"""


@generate.command("driverinterface")
@click.argument("interface_class", type=str)
@click.option("--name", type=str, default=None, help="Override the CRD metadata name")
@click.option("--namespace", "-n", type=str, default=None, help="Set the namespace in the generated YAML")
@click.option(
    "--driver-package", type=str, default=None,
    help="Python package name for the driver (e.g., jumpstarter-driver-power)",
)
@click.option("--driver-version", type=str, default=None, help="Version constraint for the driver package")
@click.option("--driver-index", type=str, default=None, help="Package index URL for the driver")
def generate_driverinterface(
    interface_class: str,
    name: str | None,
    namespace: str | None,
    driver_package: str | None,
    driver_version: str | None,
    driver_index: str | None,
):
    """Generate a DriverInterface YAML from an installed driver interface class.

    INTERFACE_CLASS is the fully qualified Python class path, e.g.:
    jumpstarter_driver_power.driver.PowerInterface
    """
    cls = _resolve_class(interface_class)

    # Build the FileDescriptorProto
    fd = build_file_descriptor(cls)

    # Serialize and base64-encode the descriptor
    descriptor_bytes = fd.SerializeToString()
    descriptor_b64 = base64.b64encode(descriptor_bytes).decode("ascii")

    # Build CRD name from proto package
    crd_name = name or _to_crd_name(fd.package)

    # Get client class if available
    client_class_path = None
    if hasattr(cls, "client") and callable(cls.client):
        try:
            client_class_path = cls.client()
        except Exception:
            pass

    # Build the DriverInterface YAML
    di = {
        "apiVersion": "jumpstarter.dev/v1alpha1",
        "kind": "DriverInterface",
        "metadata": {"name": crd_name},
        "spec": {
            "proto": {
                "package": fd.package,
                "descriptor": descriptor_b64,
            },
        },
    }

    if namespace:
        di["metadata"]["namespace"] = namespace

    # Add driver info if available
    drivers = []
    driver_entry = {"language": "python"}
    if driver_package:
        driver_entry["package"] = driver_package
    if driver_version:
        driver_entry["version"] = driver_version
    if driver_index:
        driver_entry["index"] = driver_index
    if client_class_path:
        driver_entry["clientClass"] = client_class_path

    if len(driver_entry) > 1:  # More than just "language"
        drivers.append(driver_entry)
        di["spec"]["drivers"] = drivers

    click.echo(yaml.safe_dump(di, default_flow_style=False, sort_keys=False))
