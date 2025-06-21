import shutil
from typing import Literal, Optional

import click
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.opt import opt_context, opt_kubeconfig
from jumpstarter_kubernetes import helm_installed, install_helm_chart, uninstall_helm_chart

from .controller import get_latest_compatible_controller_version
from jumpstarter.common.ipaddr import get_ip_address, get_minikube_ip


def minikube_installed() -> bool:
    return shutil.which("minikube") is not None


async def get_ip_generic(cluster_type: Optional[str]) -> str:
    if cluster_type == "minikube":
        if not minikube_installed():
            raise click.ClickException("minikube is not installed (or not in your PATH)")
        try:
            ip = await get_minikube_ip()
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
@click.option("--kind", "cluster_type", flag_value="kind", help="Use default settings for a local Kind cluster")
@click.option(
    "--minikube", "cluster_type", flag_value="minikube", help="Use default settings for a local Minikube cluster"
)
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
    cluster_type: Optional[Literal["kind"] | Literal["minikube"]],
    version: str,
    kubeconfig: Optional[str],
    context: Optional[str],
):
    """Install the Jumpstarter service in a Kubernetes cluster"""
    # Check if helm is installed
    if helm_installed(helm) is False:
        raise click.ClickException(
            "helm is not installed (or not in your PATH), please specify a helm executable with --helm <EXECUTABLE>"
        )

    # Get the system IP address and hostnames
    if ip is None:
        ip = await get_ip_generic(cluster_type)
    if basedomain is None:
        basedomain = f"jumpstarter.{ip}.nip.io"
    if grpc_endpoint is None:
        grpc_endpoint = f"grpc.{basedomain}:8082"

    if router_endpoint is None:
        router_endpoint = f"router.{basedomain}:8083"

    if version is None:
        version = await get_latest_compatible_controller_version()

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


@click.command
@click.option("--kind", "cluster_type", flag_value="kind", help="Use default settings for a local Kind cluster")
@click.option(
    "--minikube", "cluster_type", flag_value="minikube", help="Use default settings for a local Minikube cluster"
)
@blocking
async def ip(
    cluster_type: Optional[Literal["kind"] | Literal["minikube"]],
):
    """Attempt to determine the IP address of your computer"""
    ip = await get_ip_generic(cluster_type)
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
    # Check if helm is installed
    if helm_installed(helm) is False:
        raise click.ClickException(
            "helm is not installed (or not in your PATH), please specify a helm executable with --helm <EXECUTABLE>"
        )

    click.echo(f'Uninstalling Jumpstarter service in namespace "{namespace}" with Helm')

    await uninstall_helm_chart(name, namespace, kubeconfig, context, helm)

    click.echo(f'Uninstalled Helm release "{name}" from namespace "{namespace}"')
