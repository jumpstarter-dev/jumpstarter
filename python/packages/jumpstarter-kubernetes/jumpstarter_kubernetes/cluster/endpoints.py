"""Endpoint configuration for cluster management."""

from typing import Optional

from ..exceptions import EndpointConfigurationError, ToolNotInstalledError
from .common import GRPC_NODEPORT, KIND_GRPC_HOST_PORT, KIND_ROUTER_HOST_PORT, ROUTER_NODEPORT
from .minikube import minikube_installed
from jumpstarter.common.ipaddr import get_ip_address, get_minikube_ip


async def get_ip_generic(cluster_type: Optional[str], minikube: str, cluster_name: str) -> str:
    """Get IP address for the cluster."""
    if cluster_type == "minikube":
        if not minikube_installed(minikube):
            raise ToolNotInstalledError("minikube")
        try:
            ip = await get_minikube_ip(cluster_name, minikube)
        except Exception as e:
            raise EndpointConfigurationError(f"Could not determine Minikube IP address.\n{e}", "minikube") from e
    else:
        ip = get_ip_address()
        if ip == "0.0.0.0":
            raise EndpointConfigurationError("Could not determine IP address, use --ip <IP> to specify an IP address")

    return ip


async def configure_endpoints(
    cluster_type: Optional[str],
    minikube: str,
    cluster_name: str,
    ip: Optional[str],
    basedomain: Optional[str],
    grpc_endpoint: Optional[str],
    router_endpoint: Optional[str],
) -> tuple[str, str, str, str]:
    """Configure endpoints for Jumpstarter installation."""
    if ip is None:
        ip = await get_ip_generic(cluster_type, minikube, cluster_name)
    if basedomain is None:
        basedomain = f"jumpstarter.{ip}.nip.io"
    if grpc_endpoint is None:
        # k3s exposes NodePorts directly on the host; kind maps them through extraPortMappings
        grpc_port = GRPC_NODEPORT if cluster_type == "k3s" else KIND_GRPC_HOST_PORT
        grpc_endpoint = f"grpc.{basedomain}:{grpc_port}"
    if router_endpoint is None:
        router_port = ROUTER_NODEPORT if cluster_type == "k3s" else KIND_ROUTER_HOST_PORT
        router_endpoint = f"router.{basedomain}:{router_port}"

    return ip, basedomain, grpc_endpoint, router_endpoint
