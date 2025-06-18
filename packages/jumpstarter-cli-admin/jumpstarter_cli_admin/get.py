from typing import Optional

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import (
    OutputType,
    opt_context,
    opt_kubeconfig,
    opt_namespace,
    opt_output_all,
)
from jumpstarter_cli_common.print import model_print
from jumpstarter_kubernetes import (
    ClientsV1Alpha1Api,
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
)
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .k8s import (
    handle_k8s_api_exception,
    handle_k8s_config_exception,
)


@click.group(cls=AliasedGroup)
def get():
    """Get Jumpstarter Kubernetes objects"""


@get.command("client")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def get_client(
    name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str, output: OutputType
):
    """Get the client objects in a Kubernetes cluster"""
    try:
        async with ClientsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                client = await api.get_client(name)
                model_print(client, output, namespace=namespace)
            else:
                clients = await api.list_clients()
                model_print(clients, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@get.command("exporter")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@click.option("-d", "--devices", is_flag=True, help="Display the devices hosted by the exporter(s)")
@blocking
async def get_exporter(
    name: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    namespace: str,
    devices: bool,
    output: OutputType,
):
    """Get the exporter objects in a Kubernetes cluster"""
    try:
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                exporter = await api.get_exporter(name)
                model_print(exporter, output, devices=devices, namespace=namespace)
            else:
                exporters = await api.list_exporters()
                model_print(exporters, output, devices=devices, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@get.command("lease")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def get_lease(
    name: Optional[str], kubeconfig: Optional[str], context: Optional[str], namespace: str, output: OutputType
):
    """Get the lease objects in a Kubernetes cluster"""
    try:
        async with LeasesV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                lease = await api.get_lease(name)
                model_print(lease, output, namespace=namespace)
            else:
                leases = await api.list_leases()
                model_print(leases, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)
