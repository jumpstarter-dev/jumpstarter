"""Cluster detection and type identification logic."""

import json
import re
import shutil
from typing import Literal, Optional

from ..exceptions import ToolNotInstalledError
from .common import run_command
from .kind import kind_cluster_exists, kind_installed
from .minikube import minikube_cluster_exists, minikube_installed


def detect_container_runtime() -> str:
    """Detect available container runtime for Kind."""
    if shutil.which("docker"):
        return "docker"
    elif shutil.which("podman"):
        return "podman"
    elif shutil.which("nerdctl"):
        return "nerdctl"
    else:
        raise ToolNotInstalledError(
            "container runtime",
            "No supported container runtime found in PATH. Kind requires docker, podman, or nerdctl."
        )


async def detect_kind_provider(cluster_name: str) -> tuple[str, str]:
    """Detect Kind provider and return (runtime, node_name)."""
    runtime = detect_container_runtime()

    # Try to detect the actual node name by listing containers/pods
    possible_names = [
        f"{cluster_name}-control-plane",
        f"kind-{cluster_name}-control-plane",
        f"{cluster_name}-worker",
        f"kind-{cluster_name}-worker",
    ]

    # Special case for default cluster name
    if cluster_name == "kind":
        possible_names.insert(0, "kind-control-plane")

    for node_name in possible_names:
        try:
            # Check if container/node exists
            check_cmd = [runtime, "inspect", node_name]
            returncode, _, _ = await run_command(check_cmd)
            if returncode == 0:
                return runtime, node_name
        except RuntimeError:
            continue

    # Default fallback
    return runtime, f"{cluster_name}-control-plane"


async def detect_existing_cluster_type(cluster_name: str) -> Optional[Literal["kind"] | Literal["minikube"]]:
    """Detect which type of cluster exists with the given name."""
    kind_exists = False
    minikube_exists = False

    # Check if Kind cluster exists
    if kind_installed("kind"):
        try:
            kind_exists = await kind_cluster_exists("kind", cluster_name)
        except RuntimeError:
            kind_exists = False

    # Check if Minikube cluster exists
    if minikube_installed("minikube"):
        try:
            minikube_exists = await minikube_cluster_exists("minikube", cluster_name)
        except RuntimeError:
            minikube_exists = False

    if kind_exists and minikube_exists:
        from ..exceptions import ClusterOperationError
        raise ClusterOperationError(
            "detect",
            cluster_name,
            "multiple",
            Exception(
                f'Both Kind and Minikube clusters named "{cluster_name}" exist. '
                "Please specify --kind or --minikube to choose which one to delete."
            )
        )
    elif kind_exists:
        return "kind"
    elif minikube_exists:
        return "minikube"
    else:
        return None


def auto_detect_cluster_type() -> Literal["kind"] | Literal["minikube"]:
    """Auto-detect available cluster type, preferring Kind over Minikube."""
    if kind_installed("kind"):
        return "kind"
    elif minikube_installed("minikube"):
        return "minikube"
    else:
        raise ToolNotInstalledError(
            "kind or minikube",
            "Neither Kind nor Minikube is installed. Please install one of them:\n"
            "  • Kind: https://kind.sigs.k8s.io/docs/user/quick-start/\n"
            "  • Minikube: https://minikube.sigs.k8s.io/docs/start/"
        )


async def detect_cluster_type(context_name: str, server_url: str, minikube: str = "minikube") -> str:
    """Detect if cluster is Kind, Minikube, or Remote."""
    # Check for Kind cluster
    if "kind-" in context_name or context_name.startswith("kind"):
        return "kind"

    # Check for minikube in context name first
    if "minikube" in context_name.lower():
        return "minikube"

    # Check for localhost/127.0.0.1 which usually indicates Kind
    if any(host in server_url.lower() for host in ["localhost", "127.0.0.1", "0.0.0.0"]):
        return "kind"

    # Check for minikube VM IP ranges (192.168.x.x, 172.x.x.x) and typical minikube ports
    minikube_pattern_1 = re.search(r"192\.168\.\d+\.\d+:(8443|443)", server_url)
    minikube_pattern_2 = re.search(r"172\.\d+\.\d+\.\d+:(8443|443)", server_url)
    if minikube_pattern_1 or minikube_pattern_2:
        # Try to verify it's actually minikube by checking if any minikube cluster exists
        try:
            # Get list of minikube profiles
            cmd = [minikube, "profile", "list", "-o", "json"]
            returncode, stdout, _ = await run_command(cmd)
            if returncode == 0:
                try:
                    profiles = json.loads(stdout)
                    # If we have any valid minikube profiles, this is likely minikube
                    if profiles.get("valid") and len(profiles["valid"]) > 0:
                        return "minikube"
                except (json.JSONDecodeError, KeyError):
                    pass
        except RuntimeError:
            pass

    # Everything else is remote
    return "remote"
