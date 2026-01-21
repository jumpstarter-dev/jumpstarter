"""Kind cluster management operations."""

import os
import shlex
import shutil
import tempfile
from typing import List, Optional

from ..callbacks import OutputCallback, SilentCallback
from ..exceptions import (
    CertificateError,
    ClusterAlreadyExistsError,
    ClusterOperationError,
    ToolNotInstalledError,
)
from .common import run_command, run_command_with_output


def kind_installed(kind: str) -> bool:
    """Check if Kind is installed and available in the PATH."""
    return shutil.which(kind) is not None


async def kind_cluster_exists(kind: str, cluster_name: str) -> bool:
    """Check if a Kind cluster exists."""
    if not kind_installed(kind):
        return False

    try:
        returncode, _, _ = await run_command([kind, "get", "kubeconfig", "--name", cluster_name])
        return returncode == 0
    except RuntimeError:
        return False


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


async def create_kind_cluster(
    kind: str, cluster_name: str, extra_args: Optional[List[str]] = None, force_recreate: bool = False
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
            raise ClusterAlreadyExistsError(cluster_name, "kind")
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
  - containerPort: 80 # ingress controller
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010 # grpc nodeport
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011 # grpc router nodeport
    hostPort: 8083
    protocol: TCP
  - containerPort: 32000 # dex nodeport
    hostPort: 5556
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
"""

    # Write the cluster config to a temporary file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(cluster_config)
        config_file = f.name

    try:
        command = [kind, "create", "cluster", "--name", cluster_name, "--config", config_file]
        command.extend(extra_args)

        returncode = await run_command_with_output(command)

        if returncode == 0:
            return True
        else:
            raise RuntimeError(f"Failed to create Kind cluster '{cluster_name}'")
    finally:
        # Clean up the temporary config file
        try:
            os.unlink(config_file)
        except OSError:
            pass


async def list_kind_clusters(kind: str) -> List[str]:
    """List all Kind clusters."""
    if not kind_installed(kind):
        return []

    try:
        returncode, stdout, _ = await run_command([kind, "get", "clusters"])
        if returncode == 0:
            clusters = [line.strip() for line in stdout.split("\n") if line.strip()]
            return clusters
        return []
    except RuntimeError:
        return []


async def inject_certificates(extra_certs: str, cluster_name: str, callback: OutputCallback = None) -> None:
    """Inject custom certificates into a Kind cluster."""
    if callback is None:
        callback = SilentCallback()

    # Expand ~ and environment variables before making absolute
    expanded_path = os.path.expanduser(os.path.expandvars(extra_certs))
    extra_certs_path = os.path.abspath(expanded_path)

    if not os.path.exists(extra_certs_path):
        raise CertificateError(f"Extra certificates file not found: {extra_certs_path}", extra_certs_path)

    # Detect Kind provider info
    from .detection import detect_kind_provider

    runtime, node_name = await detect_kind_provider(cluster_name)

    callback.progress(f"Injecting certificates from {extra_certs_path} into Kind cluster...")

    # Copy certificates into the Kind node
    copy_cmd = [runtime, "cp", extra_certs_path, f"{node_name}:/usr/local/share/ca-certificates/extra-certs.crt"]

    returncode = await run_command_with_output(copy_cmd)

    if returncode != 0:
        raise CertificateError(f"Failed to copy certificates to Kind node: {node_name}")

    # Update ca-certificates in the node
    update_cmd = [runtime, "exec", node_name, "update-ca-certificates"]

    returncode = await run_command_with_output(update_cmd)

    if returncode != 0:
        raise CertificateError("Failed to update certificates in Kind node")

    callback.success("Successfully injected custom certificates into Kind cluster")


async def create_kind_cluster_with_options(
    kind: str,
    cluster_name: str,
    kind_extra_args: str,
    force_recreate_cluster: bool,
    extra_certs: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Create a Kind cluster with optional certificate injection."""
    if callback is None:
        callback = SilentCallback()

    if not kind_installed(kind):
        raise ToolNotInstalledError("kind")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    callback.progress(f'{cluster_action} Kind cluster "{cluster_name}"...')
    extra_args_list = shlex.split(kind_extra_args) if kind_extra_args.strip() else []

    try:
        await create_kind_cluster(kind, cluster_name, extra_args_list, force_recreate_cluster)

        # Inject custom certificates if provided
        if extra_certs:
            await inject_certificates(extra_certs, cluster_name, callback)

    except ClusterAlreadyExistsError as e:
        if not force_recreate_cluster:
            callback.progress(f'Kind cluster "{cluster_name}" already exists, continuing...')
            # Still inject certificates if cluster exists and extra_certs provided
            if extra_certs:
                await inject_certificates(extra_certs, cluster_name, callback)
        else:
            raise ClusterOperationError("recreate", cluster_name, "kind", e) from e
    except Exception as e:
        action = "recreate" if force_recreate_cluster else "create"
        raise ClusterOperationError(action, cluster_name, "kind", e) from e


async def delete_kind_cluster_with_feedback(kind: str, cluster_name: str, callback: OutputCallback = None) -> None:
    """Delete a Kind cluster with user feedback."""
    if callback is None:
        callback = SilentCallback()

    if not kind_installed(kind):
        raise ToolNotInstalledError("kind")

    try:
        await delete_kind_cluster(kind, cluster_name)
    except Exception as e:
        raise ClusterOperationError("delete", cluster_name, "kind", e) from e
