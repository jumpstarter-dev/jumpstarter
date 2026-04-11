import importlib

import click
import grpc
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.config import opt_config
from jumpstarter_protocol.jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc


@click.group(cls=AliasedGroup)
def validate():
    """Validate exporter and client configurations"""


def _format_interface_line(iface):
    """Format a single interface validation result as a display line."""
    if iface.found and iface.structurally_compatible:
        return f"  v {iface.interface_name:<16} ({iface.interface_ref})"

    if not iface.found:
        marker = "x" if iface.required else "o"
        tag = "   -> MISSING" if iface.required else " [optional]   -> not found"
        return f"  {marker} {iface.interface_name:<16} ({iface.interface_ref}){tag}"

    suffix = f"   -> {iface.error_message}" if iface.error_message else "   -> incompatible"
    return f"  x {iface.interface_name:<16} ({iface.interface_ref}){suffix}"


def _print_validation_result(result):
    """Print a single ExporterClassValidationResult and return (required, found, optional, opt_found)."""
    click.echo(f"\nExporterClass: {result.exporter_class_name}")
    req_count = req_found = opt_count = opt_found = 0

    for iface in result.interfaces:
        is_ok = iface.found and iface.structurally_compatible
        if iface.required:
            req_count += 1
            req_found += int(is_ok)
        else:
            opt_count += 1
            opt_found += int(is_ok)
        click.echo(_format_interface_line(iface))

    status = "SATISFIED" if result.satisfied else "NOT SATISFIED"
    click.echo(f"\nResult: {status} ({req_found}/{req_count} required, {opt_found}/{opt_count} optional)")
    return result.satisfied


async def _create_channel(endpoint, tls, kind, metadata, token, grpc_options):
    """Create an authenticated gRPC channel."""
    from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
    from jumpstarter.config.grpc import call_credentials

    credentials = grpc.composite_channel_credentials(
        await ssl_channel_credentials(endpoint, tls),
        call_credentials(kind, metadata, token),
    )
    return aio_secure_channel(endpoint, credentials, grpc_options)


@validate.command("exporter")
@opt_config(client=False)
@blocking
async def validate_exporter(config):
    """Validate an exporter config against all matching ExporterClasses.

    Loads the exporter config, introspects the driver tree, and calls the
    ValidateExporter RPC on the controller.

    Use --exporter NAME for a saved config or --exporter-config PATH for
    an ad-hoc config file.
    """
    from jumpstarter.config.exporter import ExporterConfigV1Alpha1

    if not isinstance(config, ExporterConfigV1Alpha1):
        raise click.ClickException("An exporter config is required for 'jmp validate exporter'")

    if config.endpoint is None or config.token is None:
        raise click.ClickException("Exporter config must have endpoint and token set")

    # Introspect the driver tree to build reports without hardware init
    click.echo("Introspecting driver tree...")
    try:
        reports = _get_driver_reports(config)
    except Exception as e:
        raise click.ClickException(f"Failed to introspect driver tree: {e}") from e

    # Connect to the controller and call ValidateExporter
    click.echo("Connecting to controller...")
    try:
        channel = await _create_channel(
            config.endpoint, config.tls, "Exporter", config.metadata, config.token, config.grpcOptions
        )
        try:
            stub = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
            response = await stub.ValidateExporter(
                jumpstarter_pb2.ValidateExporterRequest(labels={}, reports=reports)
            )
        finally:
            await channel.close()
    except grpc.aio.AioRpcError as e:
        raise click.ClickException(f"RPC failed: {e.details()}") from e

    # Display results
    if not response.results:
        click.echo("No matching ExporterClasses found.")
        return

    all_satisfied = all(_print_validation_result(r) for r in response.results)

    if all_satisfied:
        click.echo("\nAll ExporterClasses satisfied.")
    else:
        raise click.ClickException("One or more ExporterClasses not satisfied.")


def _get_driver_reports(config):
    """Introspect the exporter config's driver tree to build DriverInstanceReports."""
    reports = []
    for name, driver_instance in config.export.items():
        result = _build_report_for_instance(name, driver_instance)
        if result is not None:
            if isinstance(result, list):
                reports.extend(result)
            else:
                reports.append(result)
    return reports


def _build_report_for_instance(name, driver_instance):
    """Build a DriverInstanceReport for a driver instance config entry."""
    from jumpstarter.common.importlib import import_class
    from jumpstarter.config.exporter import (
        ExporterConfigV1Alpha1DriverInstanceBase,
        ExporterConfigV1Alpha1DriverInstanceComposite,
    )
    from jumpstarter.driver.descriptor_builder import build_file_descriptor

    root = driver_instance.root

    if isinstance(root, ExporterConfigV1Alpha1DriverInstanceComposite):
        return _build_composite_report(name, root)

    if not isinstance(root, ExporterConfigV1Alpha1DriverInstanceBase):
        return None

    return _build_base_driver_report(name, root, import_class, build_file_descriptor)


def _build_composite_report(name, root):
    """Build report for a composite driver instance."""
    import uuid as uuid_mod

    parent_uuid = str(uuid_mod.uuid4())
    reports = [jumpstarter_pb2.DriverInstanceReport(uuid=parent_uuid, labels={})]
    for child_name, child_instance in root.children.items():
        child_reports = _build_report_for_instance(child_name, child_instance)
        if child_reports is not None:
            if isinstance(child_reports, list):
                for r in child_reports:
                    if not r.HasField("parent_uuid"):
                        r.parent_uuid = parent_uuid
                reports.extend(child_reports)
            else:
                child_reports.parent_uuid = parent_uuid
                reports.append(child_reports)
    return reports


def _build_base_driver_report(name, root, import_class, build_file_descriptor):
    """Build report for a base driver instance."""
    import uuid as uuid_mod

    try:
        driver_class = import_class(root.type, [], True)
    except Exception:
        return None

    fd_proto = _get_file_descriptor_bytes(driver_class, build_file_descriptor)
    labels = _get_driver_labels(driver_class)
    labels["jumpstarter.dev/name"] = name

    parent_uuid = str(uuid_mod.uuid4())
    report = jumpstarter_pb2.DriverInstanceReport(uuid=parent_uuid, labels=labels)
    if fd_proto is not None:
        report.file_descriptor_proto = fd_proto

    # Collect child reports as flat list with parent_uuid
    all_reports = [report]
    for child_name, child_instance in root.children.items():
        child_report = _build_report_for_instance(child_name, child_instance)
        if child_report is not None:
            if isinstance(child_report, list):
                for r in child_report:
                    if not r.HasField("parent_uuid"):
                        r.parent_uuid = parent_uuid
                all_reports.extend(child_report)
            else:
                child_report.parent_uuid = parent_uuid
                all_reports.append(child_report)
    return all_reports


def _get_file_descriptor_bytes(driver_class, build_file_descriptor):
    """Try to build and serialize a FileDescriptorProto for a driver class."""
    from jumpstarter.driver.interface import DriverInterface, DriverInterfaceMeta

    # Find the interface class (same logic as Driver._get_interface_class)
    interface_class = None
    for cls in driver_class.__mro__:
        if (
            cls is not DriverInterface
            and isinstance(cls, DriverInterfaceMeta)
            and "client" in cls.__dict__
            and not getattr(cls.client, "__isabstractmethod__", False)
        ):
            interface_class = cls
            break

    if interface_class is None:
        return None

    try:
        fd = build_file_descriptor(interface_class)
        return fd.SerializeToString()
    except Exception:
        return None


def _get_driver_labels(driver_class):
    """Extract driver labels (client class path) from a driver class."""
    labels = {}
    if hasattr(driver_class, "client") and callable(driver_class.client):
        try:
            labels["jumpstarter.dev/client"] = driver_class.client()
        except Exception:
            pass
    return labels



@validate.command("client")
@click.argument("exporter_class")
@opt_config(exporter=False)
@blocking
async def validate_client(exporter_class, config):
    """Validate that installed client packages match an ExporterClass's requirements.

    EXPORTER_CLASS is the name of the ExporterClass to validate against.

    Calls the GetExporterClassInfo RPC and checks locally installed driver
    client packages against the DriverInterface definitions for that ExporterClass.

    Use --client NAME for a saved config or --client-config PATH for
    an ad-hoc config file. If neither is specified, the default client config is used.
    """
    from jumpstarter.config.client import ClientConfigV1Alpha1

    if not isinstance(config, ClientConfigV1Alpha1):
        raise click.ClickException("A client config is required for 'jmp validate client'")

    if config.endpoint is None or config.token is None:
        raise click.ClickException("Client config must have endpoint and token set")

    # Connect to the controller and call GetExporterClassInfo
    click.echo(f"Validating client packages against ExporterClass '{exporter_class}'...")
    try:
        channel = await _create_channel(
            config.endpoint, config.tls, "Client", config.metadata, config.token, config.grpcOptions
        )
        try:
            stub = jumpstarter_pb2_grpc.ControllerServiceStub(channel)
            response = await stub.GetExporterClassInfo(
                jumpstarter_pb2.GetExporterClassInfoRequest(exporter_class_name=exporter_class)
            )
        finally:
            await channel.close()
    except grpc.aio.AioRpcError as e:
        raise click.ClickException(f"RPC failed: {e.details()}") from e

    if not response.driver_interfaces:
        click.echo("No DriverInterface definitions found on the server.")
        return

    all_ok = _check_all_driver_interfaces(response.driver_interfaces)

    if all_ok:
        click.echo("\nAll client packages are correctly installed.")
    else:
        raise click.ClickException("Some client packages are missing or have version mismatches.")


def _check_all_driver_interfaces(driver_interfaces):
    """Check all driver interfaces and print results. Returns True if all OK."""
    all_ok = True
    for di in driver_interfaces:
        click.echo(f"\nDriverInterface: {di.name} ({di.package})")
        for drv in di.drivers:
            if drv.lang != "python":
                continue
            if not _check_and_print_driver(drv):
                all_ok = False
    return all_ok


def _check_and_print_driver(drv):
    """Check a single driver entry and print result. Returns True if OK."""
    pkg_ok = _check_package_installed(drv.package)
    ver_ok = _check_package_version(drv.package, drv.version) if pkg_ok else False
    cls_ok = _check_class_importable(drv.client_class) if pkg_ok else False

    if pkg_ok and ver_ok and cls_ok:
        click.echo(f"  v {drv.package} ({drv.version}) - {drv.client_class}")
        return True

    issues = []
    if not pkg_ok:
        issues.append("not installed")
    elif not ver_ok:
        issues.append("version mismatch")
    if pkg_ok and not cls_ok:
        issues.append(f"cannot import {drv.client_class}")
    click.echo(f"  x {drv.package} ({drv.version}) - {', '.join(issues)}")
    return False


def _check_package_installed(package_name: str) -> bool:
    """Check if a Python package is installed."""
    try:
        from importlib.metadata import distribution

        distribution(package_name)
        return True
    except Exception:
        return False


def _check_package_version(package_name: str, expected_version: str) -> bool:
    """Check if the installed package version matches the expected version."""
    if not expected_version:
        return True
    try:
        from importlib.metadata import distribution

        dist = distribution(package_name)
        return dist.version == expected_version
    except Exception:
        return False


def _check_class_importable(class_path: str) -> bool:
    """Check if a class can be imported from the given path."""
    if not class_path:
        return True
    try:
        if ":" in class_path:
            module_path, class_name = class_path.rsplit(":", 1)
        elif "." in class_path:
            module_path, class_name = class_path.rsplit(".", 1)
        else:
            return False
        module = importlib.import_module(module_path)
        return hasattr(module, class_name)
    except Exception:
        return False
