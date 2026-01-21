from typing import Literal, Optional

import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import opt_context, opt_kubeconfig
from jumpstarter_cli_common.version import get_client_version
from jumpstarter_kubernetes import (
    get_latest_compatible_controller_version,
    helm_installed,
    install_helm_chart,
    minikube_installed,
    uninstall_helm_chart,
)

from jumpstarter.common.ipaddr import get_ip_address, get_minikube_ip


def _validate_cluster_type(
    kind: Optional[str], minikube: Optional[str]
) -> Optional[Literal["kind"] | Literal["minikube"]]:
    """Validate cluster type selection - returns None if neither is specified"""
    if kind and minikube:
        raise click.ClickException('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        return None


def _validate_prerequisites(helm: str) -> None:
    if helm_installed(helm) is False:
        raise click.ClickException(
            "helm is not installed (or not in your PATH), please specify a helm executable with --helm <EXECUTABLE>"
        )


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
    values_files: Optional[list[str]] = None,
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
        chart,
        name,
        namespace,
        basedomain,
        grpc_endpoint,
        router_endpoint,
        mode,
        version,
        kubeconfig,
        context,
        helm,
        values_files,
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
@click.option("-v", "--version", help="The version of the service to install", default=None)
@click.option(
    "-f",
    "--values-file",
    "values_files",
    type=click.Path(exists=True, readable=True, dir_okay=False, resolve_path=True),
    multiple=True,
    help="Path to custom helm values file (can be specified multiple times)",
)
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
    version: str,
    values_files: tuple[str, ...],
    kubeconfig: Optional[str],
    context: Optional[str],
):
    """Install the Jumpstarter service in a Kubernetes cluster"""
    _validate_prerequisites(helm)

    cluster_type = _validate_cluster_type(kind, minikube)

    ip, basedomain, grpc_endpoint, router_endpoint = await _configure_endpoints(
        cluster_type, minikube or "minikube", "jumpstarter-lab", ip, basedomain, grpc_endpoint, router_endpoint
    )

    if version is None:
        version = await get_latest_compatible_controller_version(get_client_version())

    await _install_jumpstarter_helm_chart(
        chart,
        name,
        namespace,
        basedomain,
        grpc_endpoint,
        router_endpoint,
        mode,
        version,
        kubeconfig,
        context,
        helm,
        ip,
        list(values_files) if values_files else None,
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
@opt_kubeconfig
@opt_context
@blocking
async def uninstall(
    helm: str,
    name: str,
    namespace: str,
    kubeconfig: Optional[str],
    context: Optional[str],
):
    """Uninstall the Jumpstarter service in a Kubernetes cluster"""
    _validate_prerequisites(helm)

    click.echo(f'Uninstalling Jumpstarter service in namespace "{namespace}" with Helm')

    await uninstall_helm_chart(name, namespace, kubeconfig, context, helm)

    click.echo(f'Uninstalled Helm release "{name}" from namespace "{namespace}"')
