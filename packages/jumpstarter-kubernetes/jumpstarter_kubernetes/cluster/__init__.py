"""Cluster management module for Jumpstarter Kubernetes operations.

This module provides comprehensive cluster management functionality including:
- Kind and Minikube cluster operations
- Helm chart management
- Kubectl operations
- Cluster detection and endpoint configuration
- High-level orchestration operations

For backward compatibility, all functions from the original cluster.py are re-exported here.
"""

import click

# Re-export all public functions for backward compatibility

# Common utilities and types
from .common import ClusterType, format_cluster_name, get_extra_certs_path, validate_cluster_name, validate_cluster_type

# Kind cluster operations
from .kind import (
    create_kind_cluster,
    delete_kind_cluster,
    kind_cluster_exists,
    kind_installed,
    list_kind_clusters,
)

# Minikube cluster operations
from .minikube import (
    create_minikube_cluster,
    delete_minikube_cluster,
    get_minikube_cluster_ip,
    list_minikube_clusters,
    minikube_cluster_exists,
    minikube_installed,
)

# Helm operations
from .helm import install_jumpstarter_helm_chart

# Kubectl operations
from .kubectl import check_jumpstarter_installation, check_kubernetes_access, get_cluster_info, get_kubectl_contexts, list_clusters

# Detection and endpoints
from .detection import auto_detect_cluster_type, detect_cluster_type, detect_existing_cluster_type
from .endpoints import configure_endpoints, get_ip_generic

# High-level operations
from .operations import (
    _handle_cluster_creation,
    _handle_cluster_deletion,
    create_cluster_and_install,
    create_cluster_only,
    delete_cluster_by_name,
    validate_cluster_type_selection,
)

# Backward compatibility - maintain all original function names

# Some functions need aliasing for exact backward compatibility
_validate_cluster_type = validate_cluster_type_selection
_configure_endpoints = configure_endpoints
_install_jumpstarter_helm_chart = install_jumpstarter_helm_chart
_detect_existing_cluster_type = detect_existing_cluster_type
_auto_detect_cluster_type = auto_detect_cluster_type

# Create the expected _create/_delete functions that match test expectations
async def _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster, extra_certs=None):
    """Backward compatibility function for tests."""
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    click.echo(f'{"Recreating" if force_recreate_cluster else "Creating"} Kind cluster "{cluster_name}"...')

    # Convert string args to list for the low-level function
    extra_args_list = kind_extra_args.split() if kind_extra_args.strip() else []

    try:
        await create_kind_cluster(kind, cluster_name, extra_args_list, force_recreate_cluster)
        if force_recreate_cluster:
            click.echo(f'Successfully recreated Kind cluster "{cluster_name}"')
        else:
            click.echo(f'Successfully created Kind cluster "{cluster_name}"')

        # Inject custom certificates if provided
        if extra_certs:
            from .operations import inject_certs_in_kind
            await inject_certs_in_kind(extra_certs, cluster_name)

    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Kind cluster "{cluster_name}" already exists, continuing...')
            # Still inject certificates if cluster exists and custom_certs provided
            if extra_certs:
                from .operations import inject_certs_in_kind
                await inject_certs_in_kind(extra_certs, cluster_name)
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Kind cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Kind cluster: {e}") from e

async def _create_minikube_cluster(minikube, cluster_name, minikube_extra_args, force_recreate_cluster, extra_certs=None):
    """Backward compatibility function for tests."""
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    click.echo(f'{"Recreating" if force_recreate_cluster else "Creating"} Minikube cluster "{cluster_name}"...')

    # Convert string args to list for the low-level function
    extra_args_list = minikube_extra_args.split() if minikube_extra_args.strip() else []

    # Prepare custom certificates for Minikube if provided
    if extra_certs:
        from .operations import prepare_minikube_certs
        await prepare_minikube_certs(extra_certs)
        # Always add --embed-certs for container drivers
        if "--embed-certs" not in extra_args_list:
            extra_args_list.append("--embed-certs")

    try:
        await create_minikube_cluster(minikube, cluster_name, extra_args_list, force_recreate_cluster)
        if force_recreate_cluster:
            click.echo(f'Successfully recreated Minikube cluster "{cluster_name}"')
        else:
            click.echo(f'Successfully created Minikube cluster "{cluster_name}"')

    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Minikube cluster "{cluster_name}" already exists, continuing...')
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Minikube cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Minikube cluster: {e}") from e

async def _delete_kind_cluster(kind, cluster_name):
    """Backward compatibility function for tests."""
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    click.echo(f'Deleting Kind cluster "{cluster_name}"...')
    try:
        await delete_kind_cluster(kind, cluster_name)
        click.echo(f'Successfully deleted Kind cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Kind cluster: {e}") from e

async def _delete_minikube_cluster(minikube, cluster_name):
    """Backward compatibility function for tests."""
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    click.echo(f'Deleting Minikube cluster "{cluster_name}"...')
    try:
        await delete_minikube_cluster(minikube, cluster_name)
        click.echo(f'Successfully deleted Minikube cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Minikube cluster: {e}") from e

# Import list functions that might be referenced
from .kubectl import get_kubectl_contexts as list_kubectl_contexts

# For complete backward compatibility, we need to ensure run_command is available
from .kind import run_command, run_command_with_output

# Re-export all functions that were available in the original cluster.py
__all__ = [
    # Types
    "ClusterType",

    # Common utilities
    "validate_cluster_name",
    "validate_cluster_type",
    "format_cluster_name",
    "get_extra_certs_path",

    # Kind operations
    "kind_installed",
    "kind_cluster_exists",
    "create_kind_cluster",
    "delete_kind_cluster",
    "list_kind_clusters",

    # Minikube operations
    "minikube_installed",
    "minikube_cluster_exists",
    "create_minikube_cluster",
    "delete_minikube_cluster",
    "list_minikube_clusters",
    "get_minikube_cluster_ip",

    # Helm operations
    "install_jumpstarter_helm_chart",

    # Kubectl operations
    "check_kubernetes_access",
    "get_kubectl_contexts",
    "list_kubectl_contexts",
    "check_jumpstarter_installation",
    "get_cluster_info",
    "list_clusters",

    # Detection and configuration
    "auto_detect_cluster_type",
    "detect_cluster_type",
    "detect_existing_cluster_type",
    "get_ip_generic",
    "configure_endpoints",

    # High-level operations
    "create_cluster_and_install",
    "create_cluster_only",
    "delete_cluster_by_name",
    "validate_cluster_type_selection",
    "_handle_cluster_creation",
    "_handle_cluster_deletion",

    # Utility functions
    "run_command",
    "run_command_with_output",

    # Backward compatibility aliases
    "_validate_cluster_type",
    "_configure_endpoints",
    "_install_jumpstarter_helm_chart",
    "_detect_existing_cluster_type",
    "_auto_detect_cluster_type",
    "_create_kind_cluster",
    "_create_minikube_cluster",
    "_delete_kind_cluster",
    "_delete_minikube_cluster",
]