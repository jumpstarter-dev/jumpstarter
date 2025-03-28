import re
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import opt_output_all
from jumpstarter_cli_common.exceptions import handle_exceptions
from jumpstarter_cli_common.opt import OutputMode, OutputType
from jumpstarter_cli_common.table import make_table

from ..opt import opt_adapters, opt_driver_clients, opt_drivers, opt_inspect
from ..repository import (
    LocalDriverRepository,
    V1Alpha1AdapterEntryPointList,
    V1Alpha1DriverClientEntryPointList,
    V1Alpha1DriverEntryPointList,
    V1Alpha1DriverPackageList,
)

MAX_SUMMARY_LENGTH = 100


def clean_truncate_summary(summary: Optional[str]):
    if summary is None:
        return ""
    # Get only the first line
    first_line = summary.split("\n")[0].strip()
    # Strip markdown formatting
    cleaned_summary = re.sub(r"[#*_~`\[\]\(\)\{}]", "", first_line)  # Remove markdown characters
    # Truncate if necessary
    truncated_summary = cleaned_summary[:MAX_SUMMARY_LENGTH] + (
        "..." if len(cleaned_summary) > MAX_SUMMARY_LENGTH else ""
    )
    return truncated_summary


def print_packages(packages: V1Alpha1DriverPackageList, output: OutputType):
    match output:
        case OutputMode.JSON:
            click.echo(packages.dump_json())
        case OutputMode.YAML:
            click.echo(packages.dump_yaml())
        case OutputMode.NAME:
            for package in packages.items:
                click.echo(f"package.jumpstarter.dev/{package.name}")
        case _:
            if output == OutputMode.WIDE:
                columns = ["NAME", "VERSION", "INSTALLED", "CATEGORIES", "LICENSE", "SUMMARY"]
            else:
                columns = ["NAME", "VERSION", "INSTALLED", "CATEGORIES"]
            driver_rows = []
            for package in packages.items:
                driver_rows.append(
                    {
                        "INSTALLED": "Yes" if package.installed else "No",
                        "NAME": package.name,
                        "VERSION": package.version,
                        "CATEGORIES": ",".join(package.categories),
                        "LICENSE": package.license if package.license else "Unspecified",
                        "SUMMARY": clean_truncate_summary(package.summary),
                    }
                )
            click.echo(make_table(columns, driver_rows))


def print_drivers(packages: V1Alpha1DriverPackageList, output: OutputType, inspect: bool):
    drivers = V1Alpha1DriverEntryPointList()
    for package in packages.items:
        for driver in package.drivers.items:
            drivers.items.append(driver)
    match output:
        case OutputMode.JSON:
            click.echo(drivers.dump_json())
        case OutputMode.YAML:
            click.echo(drivers.dump_yaml())
        case OutputMode.NAME:
            for driver in drivers.items:
                click.echo(f"driver.jumpstarter.dev/{driver.package}/{driver.name}")
        case _:
            if output == OutputMode.WIDE and inspect:
                columns = ["NAME", "PACKAGE", "TYPE", "CLIENT", "SUMMARY"]
            else:
                columns = ["NAME", "PACKAGE", "TYPE"]
            driver_rows = []
            for driver in drivers.items:
                driver_rows.append(
                    {
                        "NAME": driver.name,
                        "PACKAGE": driver.package,
                        "TYPE": driver.type,
                        "CLIENT": driver.client_type if driver.client_type else "",
                        "SUMMARY": clean_truncate_summary(driver.summary),
                    }
                )
            click.echo(make_table(columns, driver_rows))


def print_driver_clients(packages: V1Alpha1DriverPackageList, output: OutputType):
    driver_clients = V1Alpha1DriverClientEntryPointList()
    for package in packages.items:
        for client in package.driver_clients.items:
            driver_clients.items.append(client)
    match output:
        case OutputMode.JSON:
            click.echo(driver_clients.dump_json())
        case OutputMode.YAML:
            click.echo(driver_clients.dump_yaml())
        case OutputMode.NAME:
            for client in driver_clients.items:
                click.echo(f"client.jumpstarter.dev/{client.package}/{client.name}")
        case _:
            if output == OutputMode.WIDE:
                columns = ["NAME", "PACKAGE", "TYPE", "SUMMARY"]
            else:
                columns = ["NAME", "PACKAGE", "TYPE"]
            driver_client_rows = []
            for client in driver_clients.items:
                driver_client_rows.append(
                    {
                        "NAME": client.name,
                        "PACKAGE": client.package,
                        "TYPE": client.type,
                        "SUMMARY": clean_truncate_summary(client.summary),
                    }
                )
            click.echo(make_table(columns, driver_client_rows))


def print_adapters(packages: V1Alpha1DriverPackageList, output: OutputType):
    adapters = V1Alpha1AdapterEntryPointList()
    for package in packages.items:
        for adapter in package.adapters.items:
            adapters.items.append(adapter)
    match output:
        case OutputMode.JSON:
            click.echo(adapters.dump_json())
        case OutputMode.YAML:
            click.echo(adapters.dump_yaml())
        case OutputMode.NAME:
            for adapter in adapters.items:
                click.echo(f"adapter.jumpstarter.dev/{adapter.package}/{adapter.name}")
        case _:
            if output == OutputMode.WIDE:
                columns = ["NAME", "PACKAGE", "TYPE"]
            else:
                columns = ["NAME", "PACKAGE", "TYPE"]
            adapter_rows = []
            for adapter in adapters.items:
                adapter_rows.append({"NAME": adapter.name, "PACKAGE": adapter.package, "TYPE": adapter.type})
            click.echo(make_table(columns, adapter_rows))


@click.command("list")
@opt_drivers
@opt_driver_clients
@opt_adapters
@opt_inspect
@opt_output_all
@handle_exceptions
def list(output: OutputType, drivers: bool, driver_clients: bool, adapters: bool, inspect: bool):
    """List available Jumpstarter packages."""
    # Add validation to ensure only one flag is set
    if sum([drivers, driver_clients, adapters]) > 1:
        raise click.UsageError("Only one of --drivers, --driver-clients, or --adapters can be specified.")
    # Print loading message for text outputs
    if output is None or output == OutputMode.WIDE:
        click.echo("Fetching local packages for current Python environment")
    # Load the packages from the local environment
    local_repo = LocalDriverRepository.from_venv()
    local_packages = local_repo.list_packages(inspect)
    # Print specified output
    if drivers:
        print_drivers(local_packages, output, inspect)
    elif driver_clients:
        print_driver_clients(local_packages, output)
    elif adapters:
        print_adapters(local_packages, output)
    else:
        print_packages(local_packages, output)
