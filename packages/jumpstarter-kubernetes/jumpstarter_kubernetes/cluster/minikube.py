"""Minikube cluster management operations."""

import json
import os
import shlex
import shutil
from pathlib import Path
from typing import List, Optional

from ..callbacks import OutputCallback, SilentCallback
from ..exceptions import (
    CertificateError,
    ClusterAlreadyExistsError,
    ClusterOperationError,
    ToolNotInstalledError,
)
from .common import run_command, run_command_with_output
from jumpstarter.common.ipaddr import get_minikube_ip


def minikube_installed(minikube: str) -> bool:
    """Check if Minikube is installed and available in the PATH."""
    return shutil.which(minikube) is not None


async def minikube_cluster_exists(minikube: str, cluster_name: str) -> bool:  # noqa: C901
    """Check if a Minikube cluster exists.

    Uses 'minikube profile list' to distinguish between stopped and non-existent clusters.
    A stopped cluster still exists and will be listed in the profile list.
    """
    if not minikube_installed(minikube):
        return False

    try:
        # Use profile list to check if cluster exists (works for both running and stopped clusters)
        returncode, stdout, stderr = await run_command([minikube, "profile", "list", "-o", "json"])

        if returncode == 0:
            # Parse JSON output to find the profile
            try:
                profiles = json.loads(stdout)
                # The output structure is {"valid": [...], "invalid": [...]}
                if isinstance(profiles, dict):
                    valid_profiles = profiles.get("valid", [])
                    if isinstance(valid_profiles, list):
                        for profile in valid_profiles:
                            if isinstance(profile, dict) and profile.get("Name") == cluster_name:
                                return True
            except (json.JSONDecodeError, KeyError, TypeError):
                pass

        # Fallback: check status output for "profile not found" message
        returncode, stdout, stderr = await run_command([minikube, "status", "-p", cluster_name])

        # If status succeeds, cluster exists (running)
        if returncode == 0:
            return True

        # Check if the error indicates profile doesn't exist
        combined_output = (stdout + stderr).lower()
        if "profile" in combined_output and "not found" in combined_output:
            return False

        # Non-zero exit but not "not found" means cluster exists but may be stopped
        return True

    except RuntimeError as e:
        # Check if the error message indicates profile not found
        error_msg = str(e).lower()
        if "profile" in error_msg and "not found" in error_msg:
            return False
        # Other errors may indicate the cluster exists but has issues
        return True


async def delete_minikube_cluster(minikube: str, cluster_name: str, callback: OutputCallback = None) -> bool:
    """Delete a Minikube cluster."""
    if callback is None:
        callback = SilentCallback()

    if not minikube_installed(minikube):
        raise ToolNotInstalledError("minikube")

    if not await minikube_cluster_exists(minikube, cluster_name):
        return True  # Already deleted, consider it successful

    callback.progress(f'Deleting Minikube cluster "{cluster_name}"...')
    returncode = await run_command_with_output([minikube, "delete", "-p", cluster_name])

    if returncode == 0:
        callback.success(f'Successfully deleted Minikube cluster "{cluster_name}"')
        return True
    else:
        raise ClusterOperationError(
            "delete", cluster_name, "minikube", RuntimeError(f"Failed to delete Minikube cluster '{cluster_name}'")
        )


async def create_minikube_cluster(  # noqa: C901
    minikube: str,
    cluster_name: str,
    extra_args: Optional[List[str]] = None,
    force_recreate: bool = False,
    callback: OutputCallback = None,
) -> bool:
    """Create a Minikube cluster."""
    if extra_args is None:
        extra_args = []
    if callback is None:
        callback = SilentCallback()

    if not minikube_installed(minikube):
        raise ToolNotInstalledError("minikube")

    # Check if cluster already exists
    cluster_exists = await minikube_cluster_exists(minikube, cluster_name)

    if cluster_exists:
        if not force_recreate:
            callback.progress(f'Minikube cluster "{cluster_name}" already exists, continuing...')
            return True
        else:
            if not await delete_minikube_cluster(minikube, cluster_name, callback):
                return False

    has_cpus_flag = any(a == "--cpus" or a.startswith("--cpus=") for a in extra_args)
    if not has_cpus_flag:
        try:
            rc, out, _ = await run_command([minikube, "config", "get", "cpus"])
            has_config_cpus = rc == 0 and out.strip().isdigit() and int(out.strip()) > 0
        except RuntimeError:
            # If we cannot query minikube (e.g., not installed in test env), default CPUs
            has_config_cpus = False
        if not has_config_cpus:
            extra_args.append("--cpus=4")

    command = [
        minikube,
        "start",
        "--profile",
        cluster_name,
        "--extra-config=apiserver.service-node-port-range=30000-32767",
    ]
    command.extend(extra_args)

    returncode = await run_command_with_output(command)

    if returncode == 0:
        action_past = "recreated" if force_recreate else "created"
        callback.success(f'Successfully {action_past} Minikube cluster "{cluster_name}"')
        return True
    else:
        action = "recreate" if force_recreate else "create"
        raise ClusterOperationError(
            action, cluster_name, "minikube", RuntimeError(f"Failed to {action} Minikube cluster '{cluster_name}'")
        )


async def list_minikube_clusters(minikube: str) -> List[str]:
    """List all Minikube clusters."""
    if not minikube_installed(minikube):
        return []

    try:
        returncode, stdout, _ = await run_command([minikube, "profile", "list", "-o", "json"])
        if returncode == 0:
            data = json.loads(stdout)
            valid_profiles = data.get("valid", [])
            return [profile["Name"] for profile in valid_profiles]
        return []
    except (RuntimeError, json.JSONDecodeError, KeyError):
        return []


async def get_minikube_cluster_ip(minikube: str, cluster_name: str) -> str:
    """Get the IP address of a Minikube cluster."""
    return await get_minikube_ip(cluster_name, minikube)


async def prepare_certificates(extra_certs: str, callback: OutputCallback = None) -> None:
    """Prepare custom certificates for Minikube."""
    if callback is None:
        callback = SilentCallback()

    # Expand ~ and environment variables before making absolute
    expanded_path = os.path.expanduser(os.path.expandvars(extra_certs))
    extra_certs_path = os.path.abspath(expanded_path)

    if not os.path.exists(extra_certs_path):
        raise CertificateError(f"Extra certificates file not found: {extra_certs_path}", extra_certs_path)

    # Create .minikube/certs directory if it doesn't exist
    minikube_certs_dir = Path.home() / ".minikube" / "certs"
    minikube_certs_dir.mkdir(parents=True, exist_ok=True)

    # Copy the certificate file to minikube certs directory
    cert_dest = minikube_certs_dir / "ca.crt"

    # If ca.crt already exists, append to it
    if cert_dest.exists():
        with open(extra_certs_path, "r") as src, open(cert_dest, "a") as dst:
            dst.write("\n")
            dst.write(src.read())
    else:
        shutil.copy2(extra_certs_path, cert_dest)

    callback.success(f"Prepared custom certificates for Minikube: {cert_dest}")


async def create_minikube_cluster_with_options(
    minikube: str,
    cluster_name: str,
    minikube_extra_args: str,
    force_recreate_cluster: bool,
    extra_certs: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Create a Minikube cluster with optional certificate preparation."""
    if callback is None:
        callback = SilentCallback()

    if not minikube_installed(minikube):
        raise ToolNotInstalledError("minikube")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    callback.progress(f'{cluster_action} Minikube cluster "{cluster_name}"...')
    extra_args_list = shlex.split(minikube_extra_args) if minikube_extra_args.strip() else []

    # Prepare custom certificates for Minikube if provided
    if extra_certs:
        await prepare_certificates(extra_certs, callback)
        # Always add --embed-certs for container drivers
        if "--embed-certs" not in extra_args_list:
            extra_args_list.append("--embed-certs")

    try:
        await create_minikube_cluster(minikube, cluster_name, extra_args_list, force_recreate_cluster, callback)

    except ClusterAlreadyExistsError as e:
        if not force_recreate_cluster:
            callback.progress(f'Minikube cluster "{cluster_name}" already exists, continuing...')
        else:
            raise ClusterOperationError("recreate", cluster_name, "minikube", e) from e
    except Exception as e:
        action = "recreate" if force_recreate_cluster else "create"
        raise ClusterOperationError(action, cluster_name, "minikube", e) from e


async def delete_minikube_cluster_with_feedback(
    minikube: str, cluster_name: str, callback: OutputCallback = None
) -> None:
    """Delete a Minikube cluster with user feedback."""
    if callback is None:
        callback = SilentCallback()

    if not minikube_installed(minikube):
        raise ToolNotInstalledError("minikube")

    try:
        await delete_minikube_cluster(minikube, cluster_name, callback)
    except Exception as e:
        raise ClusterOperationError("delete", cluster_name, "minikube", e) from e
