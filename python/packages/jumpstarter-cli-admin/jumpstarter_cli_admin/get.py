
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
    ExporterClaimsV1Alpha1Api,
    ExportersV1Alpha1Api,
    LeasesV1Alpha1Api,
    LeaseTemplatesV1Alpha1Api,
    get_cluster_info,
    list_clusters,
)
from kubernetes_asyncio.client.exceptions import ApiException
from kubernetes_asyncio.config.config_exception import ConfigException

from .alias import add_aliases
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
    name: str | None, kubeconfig: str | None, context: str | None, namespace: str, output: OutputType
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
    name: str | None,
    kubeconfig: str | None,
    context: str | None,
    namespace: str,
    devices: bool,
    output: OutputType,
):
    """Get the exporter objects in a Kubernetes cluster"""
    try:
        async with ExportersV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                exporter = await api.get_exporter(name)
                # The GROUP column appears only where an exclusion group is in use.
                groups = bool(exporter.exclusion_group)
                # The PORTS column appears only where an exporter declares ports.
                ports = exporter.has_ports()
                model_print(exporter, output, devices=devices, groups=groups, ports=ports, namespace=namespace)
            else:
                exporters = await api.list_exporters()
                groups = exporters.has_exclusion_groups()
                ports = exporters.has_ports()
                model_print(exporters, output, devices=devices, groups=groups, ports=ports, namespace=namespace)
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
    name: str | None, kubeconfig: str | None, context: str | None, namespace: str, output: OutputType
):
    """Get the lease objects in a Kubernetes cluster"""
    try:
        async with LeasesV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                lease = await api.get_lease(name)
                # The FORWARDS column appears only where a lease has forwards.
                model_print(lease, output, forwards=lease.has_forwards(), namespace=namespace)
            else:
                leases = await api.list_leases()
                model_print(leases, output, forwards=leases.has_forwards(), namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


@get.command("leasetemplate")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def get_lease_template(
    name: str | None, kubeconfig: str | None, context: str | None, namespace: str, output: OutputType
):
    """Get the lease template objects in a Kubernetes cluster"""
    try:
        async with LeaseTemplatesV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                template = await api.get_lease_template(name)
                model_print(template, output, forwards=template.has_forwards(), namespace=namespace)
            else:
                templates = await api.list_lease_templates()
                model_print(templates, output, forwards=templates.has_forwards(), namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


add_aliases(get, get_lease_template, "leasetemplates", "lease-template", "lease-templates")


@get.command("exporterclaim")
@click.argument("name", type=str, required=False, default=None)
@opt_namespace
@opt_kubeconfig
@opt_context
@opt_output_all
@blocking
async def get_exporter_claim(
    name: str | None, kubeconfig: str | None, context: str | None, namespace: str, output: OutputType
):
    """Get the exporter claim objects in a Kubernetes cluster

    A claim shows which lease holds an exporter or an exclusion group.
    """
    try:
        async with ExporterClaimsV1Alpha1Api(namespace, kubeconfig, context) as api:
            if name is not None:
                claim = await api.get_exporter_claim(name)
                model_print(claim, output, namespace=namespace)
            else:
                claims = await api.list_exporter_claims()
                model_print(claims, output, namespace=namespace)
    except ApiException as e:
        handle_k8s_api_exception(e)
    except ConfigException as e:
        handle_k8s_config_exception(e)


add_aliases(get, get_exporter_claim, "exporterclaims", "exporter-claim", "exporter-claims")


@get.command("cluster")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--type", type=click.Choice(["kind", "minikube", "remote", "all"]), default="all", help="Filter clusters by type"
)
@click.option("--kubectl", type=str, help="Path or name of kubectl executable", default="kubectl")
@click.option("--minikube", type=str, help="Path or name of minikube executable", default="minikube")
@opt_output_all
@blocking
async def get_cluster(
    name: str | None, type: str, kubectl: str, minikube: str, output: OutputType
):
    """Get information about a specific cluster or list all clusters"""
    try:
        if name is not None:
            # Get specific cluster by context name
            cluster_info = await get_cluster_info(name, kubectl, minikube)

            # Check if the cluster context was found
            if cluster_info.error and "not found" in cluster_info.error:
                raise click.ClickException(f'Kubernetes context "{name}" not found')

            model_print(cluster_info, output)
        else:
            # List all clusters if no name provided
            cluster_list = await list_clusters(type, kubectl, minikube)
            model_print(cluster_list, output)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error getting cluster info: {e}") from e


@get.command("clusters")
@click.option(
    "--type", type=click.Choice(["kind", "minikube", "remote", "all"]), default="all", help="Filter clusters by type"
)
@click.option("--kubectl", type=str, help="Path or name of kubectl executable", default="kubectl")
@click.option("--minikube", type=str, help="Path or name of minikube executable", default="minikube")
@opt_output_all
@blocking
async def get_clusters(type: str, kubectl: str, minikube: str, output: OutputType):
    """List all Kubernetes clusters with Jumpstarter status"""
    try:
        cluster_list = await list_clusters(type, kubectl, minikube)

        # Use model_print for all output formats
        model_print(cluster_list, output)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error listing clusters: {e}") from e
