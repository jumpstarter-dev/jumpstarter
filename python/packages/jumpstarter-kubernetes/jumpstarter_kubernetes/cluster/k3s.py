"""k3s cluster management operations."""

import os
import re
import shutil

from ..callbacks import OutputCallback, SilentCallback
from ..exceptions import ClusterOperationError
from .common import extract_host_from_ssh, run_command, run_command_with_output


def _k3s_kubeconfig_path(host_ip: str) -> str:
    """Build the local kubeconfig path for a k3s cluster by host IP."""
    safe_host = host_ip.replace(".", "-").replace(":", "-")
    return os.path.join(os.path.expanduser("~/.kube"), f"k3s-{safe_host}.yaml")


def k3s_installed() -> bool:
    """Check if k3s is installed locally (for local k3s deployments)."""
    return shutil.which("k3s") is not None


async def k3s_reachable(ssh_host: str) -> bool:
    """Check if a k3s cluster is reachable via SSH."""
    returncode, _, _ = await run_command(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new", ssh_host, "command -v k3s"]
    )
    return returncode == 0


async def k3s_cluster_exists(ssh_host: str) -> bool:
    """Check if a k3s cluster is running on the remote host."""
    returncode, _, _ = await run_command(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new", ssh_host,
         "sudo k3s kubectl get nodes"]
    )
    return returncode == 0


async def _ssh_run(ssh_host: str, command: str) -> tuple[int, str, str]:
    """Run a command on a remote host via SSH."""
    return await run_command(["ssh", "-o", "StrictHostKeyChecking=accept-new", ssh_host, command])


async def _ssh_run_with_output(ssh_host: str, command: str) -> int:
    """Run a command on a remote host via SSH with real-time output."""
    return await run_command_with_output(["ssh", "-o", "StrictHostKeyChecking=accept-new", ssh_host, command])


async def create_k3s_cluster(
    ssh_host: str,
    force_recreate: bool = False,
    callback: OutputCallback = None,
) -> str:
    """Create a k3s cluster on a remote host via SSH.

    Returns the path to the local kubeconfig file.
    """
    if callback is None:
        callback = SilentCallback()

    # Check if k3s is already installed
    already_installed = await k3s_reachable(ssh_host)

    if already_installed and not force_recreate:
        callback.progress(f"k3s is already installed on {ssh_host}")
    else:
        if already_installed and force_recreate:
            callback.progress(f"Reinstalling k3s on {ssh_host}...")
            await _ssh_run_with_output(ssh_host, "sudo /usr/local/bin/k3s-uninstall.sh 2>/dev/null || true")

        # Install k3s
        # NOTE: On Raspberry Pi, cgroup_memory must be enabled before k3s can run.
        # If installation fails with "Failed to find memory cgroup", add to /boot/firmware/cmdline.txt:
        #   cgroup_memory=1 cgroup_enable=memory
        # Then reboot the host and retry.
        callback.progress(f"Installing k3s on {ssh_host}...")
        returncode = await _ssh_run_with_output(
            ssh_host,
            "curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC='--write-kubeconfig-mode 644' sh -",
        )
        if returncode != 0:
            raise ClusterOperationError("create", "k3s", "k3s", Exception("Failed to install k3s"))

        # Wait for node to be ready
        callback.progress("Waiting for k3s node to be ready...")
        returncode = await _ssh_run_with_output(
            ssh_host,
            "sudo k3s kubectl wait --for=condition=ready node --all --timeout=120s",
        )
        if returncode != 0:
            raise ClusterOperationError("create", "k3s", "k3s", Exception("k3s node did not become ready"))

    callback.success(f"k3s cluster is running on {ssh_host}")

    # Copy kubeconfig locally
    kubeconfig_path = await fetch_k3s_kubeconfig(ssh_host, callback)
    return kubeconfig_path


async def fetch_k3s_kubeconfig(ssh_host: str, callback: OutputCallback = None) -> str:
    """Fetch the k3s kubeconfig from a remote host and save it locally.

    Replaces 127.0.0.1 with the SSH host IP so it's usable from the local machine.
    """
    if callback is None:
        callback = SilentCallback()

    host_ip = extract_host_from_ssh(ssh_host)

    returncode, kubeconfig_content, stderr = await _ssh_run(ssh_host, "cat /etc/rancher/k3s/k3s.yaml")
    if returncode != 0:
        raise ClusterOperationError(
            "fetch-kubeconfig", "k3s", "k3s", Exception(f"Failed to read k3s kubeconfig: {stderr}")
        )

    # Replace localhost with the actual host IP only in server URLs
    kubeconfig_content = re.sub(r"(server:\s*https?://)127\.0\.0\.1", rf"\g<1>{host_ip}", kubeconfig_content)

    kubeconfig_path = _k3s_kubeconfig_path(host_ip)
    os.makedirs(os.path.dirname(kubeconfig_path), exist_ok=True)

    # Write with restrictive permissions since kubeconfig contains credentials
    fd = os.open(kubeconfig_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(kubeconfig_content)

    callback.progress(f"k3s kubeconfig saved to {kubeconfig_path}")
    return kubeconfig_path


async def delete_k3s_cluster(ssh_host: str, callback: OutputCallback = None) -> None:
    """Uninstall k3s from a remote host via SSH."""
    if callback is None:
        callback = SilentCallback()

    if not await k3s_reachable(ssh_host):
        callback.progress(f"k3s is not installed on {ssh_host}, nothing to delete")
        return

    callback.progress(f"Uninstalling k3s from {ssh_host}...")
    returncode = await _ssh_run_with_output(ssh_host, "sudo /usr/local/bin/k3s-uninstall.sh")
    if returncode != 0:
        raise ClusterOperationError("delete", "k3s", "k3s", Exception("Failed to uninstall k3s"))

    # Clean up local kubeconfig
    host_ip = extract_host_from_ssh(ssh_host)
    kubeconfig_path = _k3s_kubeconfig_path(host_ip)
    try:
        os.unlink(kubeconfig_path)
        callback.progress(f"Removed local kubeconfig {kubeconfig_path}")
    except FileNotFoundError:
        pass

    callback.success(f"k3s uninstalled from {ssh_host}")


async def create_k3s_cluster_with_options(
    ssh_host: str,
    cluster_name: str,
    force_recreate: bool = False,
    callback: OutputCallback = None,
) -> str:
    """Create a k3s cluster with options, matching the interface of kind/minikube helpers.

    Returns the path to the local kubeconfig file.
    """
    if callback is None:
        callback = SilentCallback()

    cluster_action = "Recreating" if force_recreate else "Creating"
    callback.progress(f'{cluster_action} k3s cluster on {ssh_host}...')

    try:
        return await create_k3s_cluster(ssh_host, force_recreate, callback)
    except Exception as e:
        action = "recreate" if force_recreate else "create"
        raise ClusterOperationError(action, cluster_name, "k3s", e) from e


async def delete_k3s_cluster_with_feedback(ssh_host: str, cluster_name: str, callback: OutputCallback = None) -> None:
    """Delete a k3s cluster with user feedback."""
    if callback is None:
        callback = SilentCallback()

    try:
        await delete_k3s_cluster(ssh_host, callback)
    except Exception as e:
        raise ClusterOperationError("delete", cluster_name, "k3s", e) from e
