import logging
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import (
    AliasedGroup,
    make_table,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
    opt_namespace,
    time_since,
)
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
    V1Alpha1Client,
    V1Alpha1Exporter,
    V1Alpha1Lease,
)
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)


@click.group(cls=AliasedGroup)
@opt_log_level
def get(log_level: Optional[str]):
    """Get Jumpstarter Kubernetes objects"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


CLIENT_COLUMNS = ["NAME", "ENDPOINT", "AGE"]


def make_client_row(client: V1Alpha1Client):
    return {
        "NAME": client.metadata.name,
        "ENDPOINT": client.status.endpoint,
        "AGE": time_since(client.metadata.creation_timestamp),
    }


@get.command("client")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
async def get_client(name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str):
    """Get the client objects in a Kubernetes cluster"""
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                # Get a single client in a namespace
                client = await api.get_client(name)
                click.echo(make_table(CLIENT_COLUMNS, [make_client_row(client)]))
            else:
                # List clients in a namespace
                clients = await api.list_clients()
                if len(clients) == 0:
                    raise click.ClickException(f'No resources found in "{namespace}" namespace')
                else:
                    rows = list(map(make_client_row, clients))
                    click.echo(make_table(CLIENT_COLUMNS, rows))
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


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


@get.command("exporter")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@click.option("-d", "--devices", is_flag=True, help="Display the devices hosted by the exporter(s)")
async def get_exporter(
    name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str, devices: bool
):
    """Get the exporter objects in a Kubernetes cluster"""
    try:
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                # Get a single client in a namespace
                exporter = await api.get_exporter(name)
                if devices:
                    # Print the devices for the exporter
                    click.echo(make_table(DEVICE_COLUMNS, get_device_rows([exporter])))
                else:
                    # Print the exporter
                    click.echo(make_table(EXPORTER_COLUMNS, [make_exporter_row(exporter)]))
            else:
                # List clients in a namespace
                exporters = await api.list_exporters()
                if len(exporters) == 0:
                    raise click.ClickException(f'No resources found in "{namespace}" namespace')
                elif devices:
                    # Print the devices for each exporter
                    rows = get_device_rows(exporters)
                    click.echo(make_table(DEVICE_COLUMNS, rows))
                else:
                    # Print the exporters
                    rows = list(map(make_exporter_row, exporters))
                    click.echo(make_table(EXPORTER_COLUMNS, rows))
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


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


@get.command("lease")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
async def get_lease(name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str):
    """Get the lease objects in a Kubernetes cluster"""
    try:
        async with LeasesV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                # Get a single lease in a namespace
                lease = await api.get_lease(name)
                # Print the lease
                click.echo(make_table(LEASE_COLUMNS, [make_lease_row(lease)]))
            else:
                # List leases in a namespace
                leases = await api.list_leases()
                if len(leases) == 0:
                    raise click.ClickException(f'No resources found in "{namespace}" namespace')
                else:
                    # Print the leases
                    rows = list(map(make_lease_row, leases))
                    click.echo(make_table(LEASE_COLUMNS, rows))
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
