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

from .cluster import list_clusters
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


@get.command("clusters")
@click.option(
    "--type", type=click.Choice(["kind", "minikube", "remote", "all"]), default="all", help="Filter clusters by type"
)
@click.option("--check-connectivity", is_flag=True, help="Test Jumpstarter connectivity (slower)")
@click.option("--kubectl", type=str, help="Path or name of kubectl executable", default="kubectl")
@click.option("--helm", type=str, help="Path or name of helm executable", default="helm")
@click.option("--kind", type=str, help="Path or name of kind executable", default="kind")
@click.option("--minikube", type=str, help="Path or name of minikube executable", default="minikube")
@opt_output_all
@blocking
async def get_clusters(
    type: str, check_connectivity: bool, kubectl: str, helm: str, kind: str, minikube: str, output: OutputType
):
    """List all Kubernetes clusters with Jumpstarter status"""
    try:
        cluster_list = await list_clusters(type, kubectl, helm, kind, minikube)

        # Add connectivity check if requested
        if check_connectivity:
            for cluster_info in cluster_list.clusters:
                if cluster_info.accessible and cluster_info.jumpstarter.installed:
                    # TODO: Add connectivity test here
                    pass

        if output is None:
            # Table format
            if not cluster_list.clusters:
                click.echo("No clusters found.")
                return

            # Print header (kubectl style)
            header = (
                f"{'CURRENT':<8} {'NAME':<25} {'TYPE':<10} {'STATUS':<12} "
                f"{'JUMPSTARTER':<12} {'VERSION':<10} {'NAMESPACE'}"
            )
            click.echo(header)

            for info in cluster_list.clusters:
                # Current indicator
                current = "*" if info.is_current else ""
                current = current[:7]

                name = info.name[:24]
                cluster_type = info.type[:9]
                status = "Running" if info.accessible else "Stopped"
                status = status[:11]

                jumpstarter = "Yes" if info.jumpstarter.installed else "No"
                if info.jumpstarter.error:
                    jumpstarter = "Error"
                jumpstarter = jumpstarter[:11]

                version = info.jumpstarter.version or "-"
                version = version[:9]

                namespace = info.jumpstarter.namespace or "-"
                # Don't truncate namespace - let it display fully

                row = (
                    f"{current:<8} {name:<25} {cluster_type:<10} {status:<12} "
                    f"{jumpstarter:<12} {version:<10} {namespace}"
                )
                click.echo(row)
        else:
            # JSON/YAML format using model_print
            model_print(cluster_list, output)
    except Exception as e:
        click.echo(f"Error listing clusters: {e}", err=True)
