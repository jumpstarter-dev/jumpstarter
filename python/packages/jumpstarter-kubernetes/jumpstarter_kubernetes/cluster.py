import asyncio
import shutil
from typing import Optional, Tuple


def minikube_installed(minikube: str) -> bool:
    """Check if Minikube is installed and available in the PATH."""
    return shutil.which(minikube) is not None


def kind_installed(kind: str) -> bool:
    """Check if Kind is installed and available in the PATH."""
    return shutil.which(kind) is not None


async def run_command(cmd: list[str]) -> Tuple[int, str, str]:
    """Run a command and return exit code, stdout, stderr"""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return process.returncode, stdout.decode().strip(), stderr.decode().strip()
    except FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]}") from e


async def run_command_with_output(cmd: list[str]) -> int:
    """Run a command with real-time output streaming and return exit code"""
    try:
        process = await asyncio.create_subprocess_exec(*cmd)
        return await process.wait()
    except FileNotFoundError as e:
        raise RuntimeError(f"Command not found: {cmd[0]}") from e


async def minikube_cluster_exists(minikube: str, cluster_name: str) -> bool:
    """Check if a Minikube cluster exists."""
    if not minikube_installed(minikube):
        return False

    try:
        returncode, _, _ = await run_command([minikube, "status", "-p", cluster_name])
        return returncode == 0
    except RuntimeError:
        return False


async def kind_cluster_exists(kind: str, cluster_name: str) -> bool:
    """Check if a Kind cluster exists."""
    if not kind_installed(kind):
        return False

    try:
        returncode, _, _ = await run_command([kind, "get", "kubeconfig", "--name", cluster_name])
        return returncode == 0
    except RuntimeError:
        return False


async def delete_minikube_cluster(minikube: str, cluster_name: str) -> bool:
    """Delete a Minikube cluster."""
    if not minikube_installed(minikube):
        raise RuntimeError(f"{minikube} is not installed or not found in PATH.")

    if not await minikube_cluster_exists(minikube, cluster_name):
        return True  # Already deleted, consider it successful

    returncode = await run_command_with_output([minikube, "delete", "-p", cluster_name])

    if returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to delete Minikube cluster '{cluster_name}'")


async def delete_kind_cluster(kind: str, cluster_name: str) -> bool:
    """Delete a Kind cluster."""
    if not kind_installed(kind):
        raise RuntimeError(f"{kind} is not installed or not found in PATH.")

    if not await kind_cluster_exists(kind, cluster_name):
        return True  # Already deleted, consider it successful

    returncode = await run_command_with_output([kind, "delete", "cluster", "--name", cluster_name])

    if returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to delete Kind cluster '{cluster_name}'")


async def create_minikube_cluster(
    minikube: str, cluster_name: str, extra_args: Optional[list[str]] = None, force_recreate: bool = False
) -> bool:
    """Create a Minikube cluster."""
    if extra_args is None:
        extra_args = []

    if not minikube_installed(minikube):
        raise RuntimeError(f"{minikube} is not installed or not found in PATH.")

    # Check if cluster already exists
    cluster_exists = await minikube_cluster_exists(minikube, cluster_name)

    if cluster_exists:
        if not force_recreate:
            raise RuntimeError(f"Minikube cluster '{cluster_name}' already exists.")
        else:
            if not await delete_minikube_cluster(minikube, cluster_name):
                return False

    command = [
        minikube,
        "start",
        "--profile",
        cluster_name,
        "--extra-config=apiserver.service-node-port-range=8000-9000",
    ]
    command.extend(extra_args)

    returncode = await run_command_with_output(command)

    if returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to create Minikube cluster '{cluster_name}'")


async def create_kind_cluster(
    kind: str, cluster_name: str, extra_args: Optional[list[str]] = None, force_recreate: bool = False
) -> bool:
    """Create a Kind cluster."""
    if extra_args is None:
        extra_args = []

    if not kind_installed(kind):
        raise RuntimeError(f"{kind} is not installed or not found in PATH.")

    # Check if cluster already exists
    cluster_exists = await kind_cluster_exists(kind, cluster_name)

    if cluster_exists:
        if not force_recreate:
            raise RuntimeError(f"Kind cluster '{cluster_name}' already exists.")
        else:
            if not await delete_kind_cluster(kind, cluster_name):
                return False

    cluster_config = """kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      "service-node-port-range": "3000-32767"
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 80
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011
    hostPort: 8083
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
"""

    command = [kind, "create", "cluster", "--name", cluster_name, "--config=/dev/stdin"]
    command.extend(extra_args)

    kind_process = await asyncio.create_subprocess_exec(
        *command, stdin=asyncio.subprocess.PIPE
    )

    await kind_process.communicate(input=cluster_config.encode())

    if kind_process.returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to create Kind cluster '{cluster_name}'")
