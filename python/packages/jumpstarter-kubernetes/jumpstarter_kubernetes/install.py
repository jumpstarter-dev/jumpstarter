import asyncio
import shutil
from typing import Literal, Optional


def helm_installed(name: str) -> bool:
    return shutil.which(name) is not None


async def install_helm_chart(
    chart: str,
    name: str,
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: Literal["nodeport"] | Literal["ingress"] | Literal["route"],
    version: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    helm: Optional[str] = "helm",
):
    grpc_port = grpc_endpoint.split(":")[1]
    router_port = router_endpoint.split(":")[1]
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
        f"jumpstarter-controller.grpc.nodeport.enabled={'true' if mode == 'nodeport' else 'false'}",
        "--set",
        f"jumpstarter-controller.grpc.nodeport.port={grpc_port}",
        "--set",
        f"jumpstarter-controller.grpc.nodeport.routerPort={router_port}",
        "--set",
        f"jumpstarter-controller.grpc.mode={mode}",
        "--version",
        version,
        "--wait",
    ]

    if kubeconfig is not None:
        args.append("--kubeconfig")
        args.append(kubeconfig)

    if context is not None:
        args.append("--kube-context")
        args.append(context)

    # Attempt to install Jumpstarter using Helm
    process = await asyncio.create_subprocess_exec(*args)
    await process.wait()


async def uninstall_helm_chart(
    name: str, namespace: str, kubeconfig: Optional[str], context: Optional[str], helm: Optional[str] = "helm"
):
    args = [
        helm,
        "uninstall",
        name,
        "--namespace",
        namespace,
        "--wait",
    ]

    if kubeconfig is not None:
        args.append("--kubeconfig")
        args.append(kubeconfig)

    if context is not None:
        args.append("--kube-context")
        args.append(context)

    # Attempt to install Jumpstarter using Helm
    process = await asyncio.create_subprocess_exec(*args)
    await process.wait()
