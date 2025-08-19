from typing import Literal, Optional

import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import opt_context, opt_kubeconfig
from jumpstarter_kubernetes import (
    create_kind_cluster,
    create_minikube_cluster,
    delete_kind_cluster,
    delete_minikube_cluster,
    helm_installed,
    install_helm_chart,
    kind_installed,
    minikube_installed,
    uninstall_helm_chart,
)

from .controller import get_latest_compatible_controller_version
from jumpstarter.common.ipaddr import get_ip_address, get_minikube_ip


def _validate_prerequisites(helm: str) -> None:
    if helm_installed(helm) is False:
        raise click.ClickException(
            "helm is not installed (or not in your PATH), please specify a helm executable with --helm <EXECUTABLE>"
        )


def _validate_cluster_type(
    kind: Optional[str], minikube: Optional[str]
) -> Optional[Literal["kind"] | Literal["minikube"]]:
    if kind and minikube:
        raise click.ClickException('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    return None


async def _configure_endpoints(
    cluster_type: Optional[str],
    minikube: str,
    cluster_name: str,
    ip: Optional[str],
    basedomain: Optional[str],
    grpc_endpoint: Optional[str],
    router_endpoint: Optional[str],
) -> tuple[str, str, str, str]:
    if ip is None:
        ip = await get_ip_generic(cluster_type, minikube, cluster_name)
    if basedomain is None:
        basedomain = f"jumpstarter.{ip}.nip.io"
    if grpc_endpoint is None:
        grpc_endpoint = f"grpc.{basedomain}:8082"
    if router_endpoint is None:
        router_endpoint = f"router.{basedomain}:8083"

    return ip, basedomain, grpc_endpoint, router_endpoint


async def _handle_cluster_creation(
    create_cluster: bool,
    cluster_type: Optional[str],
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
) -> None:
    if not create_cluster:
        return

    if cluster_type is None:
        raise click.ClickException("--create-cluster requires either --kind or --minikube to be specified")

    if force_recreate_cluster:
        click.echo(f'⚠️  WARNING: Force recreating cluster "{cluster_name}" will destroy ALL data in the cluster!')
        click.echo("This includes:")
        click.echo("  • All deployed applications and services")
        click.echo("  • All persistent volumes and data")
        click.echo("  • All configurations and secrets")
        click.echo("  • All custom resources")
        if not click.confirm(f'Are you sure you want to recreate cluster "{cluster_name}"?'):
            click.echo("Cluster recreation cancelled.")
            raise click.Abort()

    if cluster_type == "kind":
        await _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster)
    elif cluster_type == "minikube":
        await _create_minikube_cluster(minikube, cluster_name, minikube_extra_args, force_recreate_cluster)


async def _create_kind_cluster(
    kind: str, cluster_name: str, kind_extra_args: str, force_recreate_cluster: bool
) -> None:
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Kind cluster "{cluster_name}"...')
    extra_args_list = kind_extra_args.split() if kind_extra_args.strip() else []
    try:
        await create_kind_cluster(kind, cluster_name, extra_args_list, force_recreate_cluster)
        if force_recreate_cluster:
            click.echo(f'Successfully recreated Kind cluster "{cluster_name}"')
        else:
            click.echo(f'Successfully created Kind cluster "{cluster_name}"')
    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Kind cluster "{cluster_name}" already exists, continuing...')
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Kind cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Kind cluster: {e}") from e


async def _create_minikube_cluster(
    minikube: str, cluster_name: str, minikube_extra_args: str, force_recreate_cluster: bool
) -> None:
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Minikube cluster "{cluster_name}"...')
    extra_args_list = minikube_extra_args.split() if minikube_extra_args.strip() else []
    try:
        await create_minikube_cluster(minikube, cluster_name, extra_args_list, force_recreate_cluster)
        if force_recreate_cluster:
            click.echo(f'Successfully recreated Minikube cluster "{cluster_name}"')
        else:
            click.echo(f'Successfully created Minikube cluster "{cluster_name}"')
    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Minikube cluster "{cluster_name}" already exists, continuing...')
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Minikube cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Minikube cluster: {e}") from e


async def _delete_kind_cluster(kind: str, cluster_name: str) -> None:
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    click.echo(f'Deleting Kind cluster "{cluster_name}"...')
    try:
        await delete_kind_cluster(kind, cluster_name)
        click.echo(f'Successfully deleted Kind cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Kind cluster: {e}") from e


async def _delete_minikube_cluster(minikube: str, cluster_name: str) -> None:
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    click.echo(f'Deleting Minikube cluster "{cluster_name}"...')
    try:
        await delete_minikube_cluster(minikube, cluster_name)
        click.echo(f'Successfully deleted Minikube cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Minikube cluster: {e}") from e


async def _handle_cluster_deletion(kind: Optional[str], minikube: Optional[str], cluster_name: str) -> None:
    cluster_type = _validate_cluster_type(kind, minikube)

    if cluster_type is None:
        return

    if not click.confirm(
        f'⚠️  WARNING: This will permanently delete the "{cluster_name}" {cluster_type} cluster and ALL its data. Continue?'  # noqa: E501
    ):
        click.echo("Cluster deletion cancelled.")
        return

    if cluster_type == "kind":
        await _delete_kind_cluster(kind or "kind", cluster_name)
    elif cluster_type == "minikube":
        await _delete_minikube_cluster(minikube or "minikube", cluster_name)


async def _install_jumpstarter_helm_chart(
    chart: str,
    name: str,
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: str,
    version: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    helm: str,
    ip: str,
) -> None:
    click.echo(f'Installing Jumpstarter service v{version} in namespace "{namespace}" with Helm\n')
    click.echo(f"Chart URI: {chart}")
    click.echo(f"Chart Version: {version}")
    click.echo(f"IP Address: {ip}")
    click.echo(f"Basedomain: {basedomain}")
    click.echo(f"Service Endpoint: {grpc_endpoint}")
    click.echo(f"Router Endpoint: {router_endpoint}")
    click.echo(f"gPRC Mode: {mode}\n")

    await install_helm_chart(
        chart, name, namespace, basedomain, grpc_endpoint, router_endpoint, mode, version, kubeconfig, context, helm
    )

    click.echo(f'Installed Helm release "{name}" in namespace "{namespace}"')


async def get_ip_generic(cluster_type: Optional[str], minikube: str, cluster_name: str) -> str:
    if cluster_type == "minikube":
        if not minikube_installed(minikube):
            raise click.ClickException("minikube is not installed (or not in your PATH)")
        try:
            ip = await get_minikube_ip(cluster_name, minikube)
        except Exception as e:
            raise click.ClickException(f"Could not determine Minikube IP address.\n{e}") from e
    else:
        ip = get_ip_address()
        if ip == "0.0.0.0":
            raise click.ClickException("Could not determine IP address, use --ip <IP> to specify an IP address")

    return ip


@click.command
@click.option("--helm", type=str, help="Path or name of a helm executable", default="helm")
@click.option("--name", type=str, help="The name of the chart installation", default="jumpstarter")
@click.option(
    "-c",
    "--chart",
    type=str,
    help="The URL of a Jumpstarter helm chart to install",
    default="oci://quay.io/jumpstarter-dev/helm/jumpstarter",
)
@click.option(
    "-n", "--namespace", type=str, help="Namespace to install Jumpstarter components in", default="jumpstarter-lab"
)
@click.option("-i", "--ip", type=str, help="IP address of your host machine", default=None)
@click.option("-b", "--basedomain", type=str, help="Base domain of the Jumpstarter service", default=None)
@click.option("-g", "--grpc-endpoint", type=str, help="The gRPC endpoint to use for the Jumpstarter API", default=None)
@click.option("-r", "--router-endpoint", type=str, help="The gRPC endpoint to use for the router", default=None)
@click.option("--nodeport", "mode", flag_value="nodeport", help="Use Nodeport routing (recommended)", default=True)
@click.option("--ingress", "mode", flag_value="ingress", help="Use a Kubernetes ingress")
@click.option("--route", "mode", flag_value="route", help="Use an OpenShift route")
@click.option(
    "--kind", is_flag=False, flag_value="kind", default=None, help="Use default settings for a local Kind cluster"
)
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Use default settings for a local Minikube cluster",
)
@click.option("--create-cluster", is_flag=True, help="Create a local Kind or Minikube cluster if it does not exist")
@click.option(
    "--force-recreate-cluster",
    is_flag=True,
    help="Force recreate the cluster if it already exists (WARNING: This will destroy all data in the cluster)",
)
@click.option("--cluster-name", type=str, help="The name of the local cluster to create", default="jumpstarter-lab")
@click.option("--kind-extra-args", type=str, help="Extra arguments for the Kind cluster creation", default="")
@click.option("--minikube-extra-args", type=str, help="Extra arguments for the Minikube cluster creation", default="")
@click.option("-v", "--version", help="The version of the service to install", default=None)
@opt_kubeconfig
@opt_context
@blocking
async def install(
    helm: str,
    chart: str,
    name: str,
    namespace: str,
    ip: Optional[str],
    basedomain: Optional[str],
    grpc_endpoint: Optional[str],
    router_endpoint: Optional[str],
    mode: Literal["nodeport"] | Literal["ingress"] | Literal["route"],
    kind: Optional[str],
    minikube: Optional[str],
    create_cluster: bool,
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    version: str,
    kubeconfig: Optional[str],
    context: Optional[str],
):
    """Install the Jumpstarter service in a Kubernetes cluster"""
    _validate_prerequisites(helm)

    cluster_type = _validate_cluster_type(kind, minikube)

    await _handle_cluster_creation(
        create_cluster,
        cluster_type,
        force_recreate_cluster,
        cluster_name,
        kind_extra_args,
        minikube_extra_args,
        kind or "kind",
        minikube or "minikube",
    )

    ip, basedomain, grpc_endpoint, router_endpoint = await _configure_endpoints(
        cluster_type, minikube or "minikube", cluster_name, ip, basedomain, grpc_endpoint, router_endpoint
    )

    if version is None:
        version = await get_latest_compatible_controller_version()

    await _install_jumpstarter_helm_chart(
        chart, name, namespace, basedomain, grpc_endpoint, router_endpoint, mode, version, kubeconfig, context, helm, ip
    )


@click.command
@click.option(
    "--kind", is_flag=False, flag_value="kind", default=None, help="Use default settings for a local Kind cluster"
)
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Use default settings for a local Minikube cluster",
)
@click.option("--cluster-name", type=str, help="The name of the cluster", default="jumpstarter-lab")
@blocking
async def ip(
    kind: Optional[str],
    minikube: Optional[str],
    cluster_name: str,
):
    """Attempt to determine the IP address of your computer"""
    cluster_type = _validate_cluster_type(kind, minikube)
    minikube_binary = minikube or "minikube"
    ip = await get_ip_generic(cluster_type, minikube_binary, cluster_name)
    click.echo(ip)


@click.command
@click.option("--helm", type=str, help="Path or name of a helm executable", default="helm")
@click.option("--name", type=str, help="The name of the chart installation", default="jumpstarter")
@click.option(
    "-n", "--namespace", type=str, help="Namespace to install Jumpstarter components in", default="jumpstarter-lab"
)
@click.option("--delete-cluster", is_flag=True, help="Delete the local cluster after uninstalling")
@click.option(
    "--kind", is_flag=False, flag_value="kind", default=None, help="Delete the local Kind cluster after uninstalling"
)
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Delete the local Minikube cluster after uninstalling",
)
@click.option("--cluster-name", type=str, help="The name of the local cluster to delete", default="jumpstarter-lab")
@opt_kubeconfig
@opt_context
@blocking
async def uninstall(
    helm: str,
    name: str,
    namespace: str,
    delete_cluster: bool,
    kind: Optional[str],
    minikube: Optional[str],
    cluster_name: str,
    kubeconfig: Optional[str],
    context: Optional[str],
):
    """Uninstall the Jumpstarter service in a Kubernetes cluster"""
    _validate_prerequisites(helm)

    click.echo(f'Uninstalling Jumpstarter service in namespace "{namespace}" with Helm')

    await uninstall_helm_chart(name, namespace, kubeconfig, context, helm)

    click.echo(f'Uninstalled Helm release "{name}" from namespace "{namespace}"')

    if delete_cluster:
        await _handle_cluster_deletion(kind, minikube, cluster_name)
