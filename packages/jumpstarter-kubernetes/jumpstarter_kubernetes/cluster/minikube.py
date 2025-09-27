"""Minikube cluster management operations."""

import asyncio
import shutil
from typing import List, Optional

from jumpstarter.common.ipaddr import get_minikube_ip


def minikube_installed(minikube: str) -> bool:
    """Check if Minikube is installed and available in the PATH."""
    return shutil.which(minikube) is not None


async def run_command(cmd: list[str]) -> tuple[int, str, str]:
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


async def create_minikube_cluster(
    minikube: str, cluster_name: str, extra_args: Optional[List[str]] = None, force_recreate: bool = False
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
        return True
    else:
        raise RuntimeError(f"Failed to create Minikube cluster '{cluster_name}'")


async def list_minikube_clusters(minikube: str) -> List[str]:
    """List all Minikube clusters."""
    if not minikube_installed(minikube):
        return []

    try:
        returncode, stdout, _ = await run_command([minikube, "profile", "list", "-o", "json"])
        if returncode == 0:
            import json
            data = json.loads(stdout)
            valid_profiles = data.get("valid", [])
            return [profile["Name"] for profile in valid_profiles]
        return []
    except (RuntimeError, json.JSONDecodeError, KeyError):
        return []


async def get_minikube_cluster_ip(minikube: str, cluster_name: str) -> str:
    """Get the IP address of a Minikube cluster."""
    return await get_minikube_ip(cluster_name, minikube)