import asyncio
import shutil
import socket
from typing import Literal, Optional

import asyncclick as click

from .util import opt_context, opt_kubeconfig
from .version import get_client_version


def get_ip_address() -> str:
    """Get the IP address of the host machine"""
    # Try to get the IP address using the hostname
    hostname = socket.gethostname()
    address = socket.gethostbyname(hostname)
    # If it returns a bogus address, do it the hard way
    if not address or address.startswith("127."):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("1.1.1.1", 0))
        address = s.getsockname()[0]
    return address


def helm_installed(name: str) -> bool:
    return shutil.which(name) is not None


def get_chart_version() -> str:
    client_version = get_client_version()
    parts = client_version.split(".")
    return f"{parts[0].replace("v", "")}.{parts[1]}.{parts[2]}"


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
    mode: Literal["nodeport"] | Literal["ingress"],
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
    grpc_port = grpc_endpoint.split(":")[1]
    if router_endpoint is None:
        router_endpoint = f"router.{basedomain}:8083"
    router_port = router_endpoint.split(":")[1]

    click.echo(f'Installing Jumpstarter service v{version} in namespace "{namespace}" with Helm\n')
    click.echo(f"Chart URI: {chart}")
    click.echo(f"Chart Version: {version}")
    click.echo(f"IP Address: {ip}")
    click.echo(f"Basedomain: {basedomain}")
    click.echo(f"Service Endpoint: {grpc_endpoint}")
    click.echo(f"Router Endpoint: {router_endpoint}")
    click.echo(f"gPRC Mode: {mode}\n")

    args = [
        helm,
        "upgrade",
        name,
        "--install",
        chart,
        "--create-namespace",
        "--namespace",
        namespace,
        "--set",
        f"global.baseDomain={basedomain}",
        "--set",
        f"jumpstarter-controller.grpc.endpoint={grpc_endpoint}",
        "--set",
        f"jumpstarter-controller.grpc.routerEndpoint={router_endpoint}",
        "--set",
        "global.metrics.enabled=false",
        "--set",
        f"jumpstarter-controller.grpc.nodeport.enabled={"true" if mode == "nodeport" else "false"}",
        "--set",
        f"jumpstarter-controller.grpc.nodeport.port={grpc_port}",
        "--set",
        f"jumpstarter-controller.grpc.nodeport.routerPort={router_port}",
        "--set",
        f"jumpstarter-controller.grpc.mode={mode}",
        "--version",
        version,
        "--wait"
    ]

    # click.echo(str.join(" ", args) + "\n")

    if kubeconfig is not None:
        args.append("--kubeconfig")
        args.append(kubeconfig)

    if context is not None:
        args.append("--kube-context")
        args.append(context)

    # Attempt to install Jumpstarter using Helm
    process = await asyncio.create_subprocess_exec(*args)
    await process.wait()
