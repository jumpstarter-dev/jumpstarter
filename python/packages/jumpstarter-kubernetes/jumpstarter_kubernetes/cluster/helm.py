"""Helm chart management operations."""

from typing import Optional

from ..callbacks import OutputCallback, SilentCallback
from ..install import install_helm_chart


async def install_jumpstarter_helm_chart(
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
    callback: OutputCallback = None,
    values_files: Optional[list[str]] = None,
) -> None:
    """Install Jumpstarter Helm chart."""
    if callback is None:
        callback = SilentCallback()

    callback.progress(f'Installing Jumpstarter service v{version} in namespace "{namespace}" with Helm\n')
    callback.progress(f"Chart URI: {chart}")
    callback.progress(f"Chart Version: {version}")
    callback.progress(f"IP Address: {ip}")
    callback.progress(f"Basedomain: {basedomain}")
    callback.progress(f"Service Endpoint: {grpc_endpoint}")
    callback.progress(f"Router Endpoint: {router_endpoint}")
    callback.progress(f"gRPC Mode: {mode}\n")

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

    callback.success(f'Installed Helm release "{name}" in namespace "{namespace}"')
