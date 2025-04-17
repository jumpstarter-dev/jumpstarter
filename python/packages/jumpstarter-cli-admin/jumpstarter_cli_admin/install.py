from typing import Literal, Optional

import asyncclick as click
from jumpstarter_cli_common.opt import opt_context, opt_kubeconfig
from jumpstarter_cli_common.version import get_client_version
from jumpstarter_kubernetes import get_ip_address, helm_installed, install_helm_chart


def get_chart_version() -> str:
    client_version = get_client_version()
    parts = client_version.split(".")
    return f"{parts[0].replace('v', '')}.{parts[1]}.{parts[2]}"


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
@click.option("-v", "--version", help="The version of the service to install", default=get_chart_version())
@opt_kubeconfig
@opt_context
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
        ip = get_ip_address()
    if basedomain is None:
        basedomain = f"jumpstarter.{ip}.nip.io"
    if grpc_endpoint is None:
        grpc_endpoint = f"grpc.{basedomain}:8082"

    if router_endpoint is None:
        router_endpoint = f"router.{basedomain}:8083"

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
