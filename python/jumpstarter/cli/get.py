import logging
import pprint
from typing import Optional

import asyncclick as click
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from jumpstarter.k8s import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
    V1Alpha1Client,
    V1Alpha1Exporter,
)

from .util import (
    AliasedGroup,
    handle_k8s_api_exception,
    handle_k8s_config_exception,
    make_table,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
    opt_namespace,
    time_since,
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


LEASE_COLUMNS = ["NAME", "CLIENT", "EXPORTER", "STATUS", "REASON", "BEGIN", "END", "DURATION", "AGE"]


def make_lease_row(lease: dict):
    creationTimestamp = lease["metadata"]["creationTimestamp"]
    status = lease["status"]["conditions"][-1]["reason"] if len(lease["status"]["conditions"]) > 0 else "Unknown"
    return {
        "NAME": lease["metadata"]["name"],
        "CLIENT": lease["spec"]["clientRef"]["name"] if "clientRef" in lease["spec"] else "",
        "EXPORTER": lease["status"]["exporterRef"]["name"] if "exporterRef" in lease["status"] else "",
        "DURATION": lease["spec"]["duration"],
        "STATUS": "Ended" if lease["status"]["ended"] else "InProgress",
        "REASON": status,
        "BEGIN": lease["status"]["beginTime"] if "beginTime" in lease["status"] else "",
        "END": lease["status"]["endTime"] if "endTime" in lease["status"] else "",
        "AGE": time_since(creationTimestamp),
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
                pprint.pp(lease)
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
