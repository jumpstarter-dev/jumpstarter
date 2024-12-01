import logging
from typing import Optional

import click
from kubernetes import config
from kubernetes.client.exceptions import ApiException

from jumpstarter.k8s import ClientsApi, ExportersApi

from .util import (
    AliasedGroup,
    handle_k8s_api_exception,
    make_table,
    opt_context,
    opt_kubeconfig,
    opt_log_level,
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

@get.command("client")
@click.argument("name", type=str, required=False, default=None)
@click.option("-n", "--namespace", type=str, help="The namespace to get clients in", default="default")
@opt_kubeconfig
@opt_context
def get_client(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str
):
    """Get the client objects in a Kubernetes cluster"""
    config.load_kube_config(config_file=kubeconfig, context=context)
    api = ClientsApi()
    columns = ["NAME", "ENDPOINT", "AGE"]

    def make_row(c: dict):
        creationTimestamp = c["metadata"]["creationTimestamp"]
        return {
            "NAME": c["metadata"]["name"],
            "ENDPOINT": c["status"]["endpoint"],
            "AGE": time_since(creationTimestamp),
        }

    try:
        if name is not None:
            # Get a single client in a namespace
            client = api.get_namespaced_client(namespace, name)
            click.echo(make_table(columns, [make_row(client)]))
        else:
            # List clients in a namespace
            clients = api.get_namespaced_clients(namespace)
            if len(clients) == 0:
                click.echo(f'No resources found in "{namespace}" namespace')
            else:
                rows = list(map(make_row, clients))
                click.echo(make_table(columns, rows))
    except ApiException as e:
        handle_k8s_api_exception(e)

EXPORTER_COLUMNS = ["NAME", "ENDPOINT", "DEVICES", "AGE"]
DEVICE_COLUMNS = ["NAME", "ENDPOINT", "AGE", "LABELS", "UUID"]

def make_exporter_row(c: dict):
    """Make an exporter row to print"""
    creationTimestamp = c["metadata"]["creationTimestamp"]
    return {
        "NAME": c["metadata"]["name"],
        "ENDPOINT": c["status"]["endpoint"],
        "DEVICES": str(len(c["status"]["devices"])),
        "AGE": time_since(creationTimestamp),
    }

def get_device_rows(exporters: list[dict]):
    """Get the device rows to print from the exporters"""
    devices = []
    for e in exporters:
        creationTimestamp = e["metadata"]["creationTimestamp"]
        for d in e["status"]["devices"]:
            labels = []
            for label in d["labels"]:
                labels.append(f"{label}:{str(d["labels"][label])}")
            devices.append({
                "NAME": e["metadata"]["name"],
                "ENDPOINT": e["status"]["endpoint"],
                "AGE": time_since(creationTimestamp),
                "LABELS": ",".join(labels),
                "UUID": d["uuid"],
            })
    return devices

@get.command("exporter")
@click.argument("name", type=str, required=False, default=None)
@click.option("-n", "--namespace", type=str, help="The namespace to get exporters in", default="default")
@click.option("-d", "--devices", is_flag=True, help="Display the devices hosted by the exporter(s)")
@opt_kubeconfig
@opt_context
def get_exporter(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str,
    devices: bool
):
    """Get the exporter objects in a Kubernetes cluster"""
    config.load_kube_config(config_file=kubeconfig, context=context)
    api = ExportersApi()

    try:
        if name is not None:
            # Get a single client in a namespace
            exporter = api.get_namespaced_exporter(namespace, name)
            if devices:
                # Print the devices for the exporter
                click.echo(make_table(DEVICE_COLUMNS, get_device_rows([exporter])))
            else:
                # Print the exporter
                click.echo(make_table(EXPORTER_COLUMNS, [make_exporter_row(exporter)]))
        else:
            # List clients in a namespace
            exporters = api.get_namespaced_exporters(namespace)
            if len(exporters) == 0:
                click.echo(f'No resources found in "{namespace}" namespace')
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
