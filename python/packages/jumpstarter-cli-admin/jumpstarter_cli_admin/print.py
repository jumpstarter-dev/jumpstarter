from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import (
    OutputType,
    make_table,
    time_since,
)
from jumpstarter_kubernetes import (
    V1Alpha1Client,
    V1Alpha1Exporter,
    V1Alpha1Lease,
    V1Alpha1List,
)

CLIENT_COLUMNS = ["NAME", "ENDPOINT", "AGE"]


def make_client_row(client: V1Alpha1Client):
    return {
        "NAME": client.metadata.name,
        "ENDPOINT": client.status.endpoint if client.status is not None else "",
        "AGE": time_since(client.metadata.creation_timestamp),
    }


def print_client(client: V1Alpha1Client, output: Optional[OutputType]):
    if output == OutputType.JSON:
        click.echo(client.dump_json())
    elif output == OutputType.YAML:
        click.echo(client.dump_yaml())
    else:
        click.echo(make_table(CLIENT_COLUMNS, [make_client_row(client)]))


def print_clients(clients: V1Alpha1List[V1Alpha1Client], namespace: str, output: Optional[OutputType]):
    if output == OutputType.JSON:
        click.echo(clients.dump_json())
    elif output == OutputType.YAML:
        click.echo(clients.dump_yaml())
    elif len(clients.items) == 0:
        raise click.ClickException(f'No resources found in "{namespace}" namespace')
    else:
        click.echo(make_table(CLIENT_COLUMNS, list(map(make_client_row, clients.items))))


EXPORTER_COLUMNS = ["NAME", "ENDPOINT", "DEVICES", "AGE"]
DEVICE_COLUMNS = ["NAME", "ENDPOINT", "AGE", "LABELS", "UUID"]


def make_exporter_row(exporter: V1Alpha1Exporter):
    """Make an exporter row to print"""
    return {
        "NAME": exporter.metadata.name,
        "ENDPOINT": exporter.status.endpoint,
        "DEVICES": str(len(exporter.status.devices)),
        "AGE": time_since(exporter.metadata.creation_timestamp),
    }


def get_device_rows(exporters: list[V1Alpha1Exporter]):
    """Get the device rows to print from the exporters"""
    devices = []
    for e in exporters:
        for d in e.status.devices:
            labels = []
            for label in d.labels:
                labels.append(f"{label}:{str(d.labels[label])}")
            devices.append(
                {
                    "NAME": e.metadata.name,
                    "ENDPOINT": e.status.endpoint,
                    "AGE": time_since(e.metadata.creation_timestamp),
                    "LABELS": ",".join(labels),
                    "UUID": d.uuid,
                }
            )
    return devices


def print_exporter(exporter: V1Alpha1Exporter, devices: bool, output: Optional[OutputType]):
    if output == OutputType.JSON:
        click.echo(exporter.dump_json())
    elif output == OutputType.YAML:
        click.echo(exporter.dump_yaml())
    elif devices:
        # Print the devices for the exporter
        click.echo(make_table(DEVICE_COLUMNS, get_device_rows([exporter])))
    else:
        click.echo(make_table(EXPORTER_COLUMNS, [make_exporter_row(exporter)]))


def print_exporters(
    exporters: V1Alpha1List[V1Alpha1Exporter], namespace: str, devices: bool, output: Optional[OutputType]
):
    if output == OutputType.JSON:
        click.echo(exporters.dump_json())
    elif output == OutputType.YAML:
        click.echo(exporters.dump_yaml())
    elif len(exporters.items) == 0:
        raise click.ClickException(f'No resources found in "{namespace}" namespace')
    elif devices:
        # Print the devices for each exporter
        rows = get_device_rows(exporters)
        click.echo(make_table(DEVICE_COLUMNS, rows))
    else:
        click.echo(make_table(EXPORTER_COLUMNS, list(map(make_exporter_row, exporters.items))))


LEASE_COLUMNS = ["NAME", "CLIENT", "SELECTOR", "EXPORTER", "STATUS", "REASON", "BEGIN", "END", "DURATION", "AGE"]


def get_reason(lease: V1Alpha1Lease):
    condition = lease.status.conditions[-1] if len(lease.status.conditions) > 0 else None
    reason = condition.reason if condition is not None else "Unknown"
    status = condition.status if condition is not None else "False"
    if reason == "Ready":
        if status == "True":
            return "Ready"
        else:
            return "Waiting"
    elif reason == "Expired":
        if status == "True":
            return "Expired"
        else:
            return "Complete"


def make_lease_row(lease: V1Alpha1Lease):
    selectors = []
    for label in lease.spec.selector:
        selectors.append(f"{label}:{str(lease.spec.selector[label])}")
    return {
        "NAME": lease.metadata.name,
        "CLIENT": lease.spec.client.name if lease.spec.client is not None else "",
        "SELECTOR": ",".join(selectors) if len(selectors) > 0 else "*",
        "EXPORTER": lease.status.exporter.name if lease.status.exporter is not None else "",
        "DURATION": lease.spec.duration,
        "STATUS": "Ended" if lease.status.ended else "InProgress",
        "REASON": get_reason(lease),
        "BEGIN": lease.status.begin_time if lease.status.begin_time is not None else "",
        "END": lease.status.end_time if lease.status.end_time is not None else "",
        "AGE": time_since(lease.metadata.creation_timestamp),
    }


def print_lease(lease: V1Alpha1Lease, output: Optional[OutputType]):
    if output == OutputType.JSON:
        click.echo(lease.dump_json())
    elif output == OutputType.YAML:
        click.echo(lease.dump_yaml())
    else:
        click.echo(make_table(LEASE_COLUMNS, [make_lease_row(lease)]))


def print_leases(leases: V1Alpha1List[V1Alpha1Lease], namespace: str, output: Optional[OutputType]):
    if output == OutputType.JSON:
        click.echo(leases.dump_json())
    elif output == OutputType.YAML:
        click.echo(leases.dump_yaml())
    elif len(leases.items) == 0:
        raise click.ClickException(f'No resources found in "{namespace}" namespace')
    else:
        click.echo(make_table(LEASE_COLUMNS, list(map(make_lease_row, leases.items))))
