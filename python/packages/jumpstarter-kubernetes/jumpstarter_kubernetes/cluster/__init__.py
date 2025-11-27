"""Cluster management module for Jumpstarter Kubernetes operations.

This module provides comprehensive cluster management functionality including:
- Kind and Minikube cluster operations
- Helm chart management
- Kubectl operations
- Cluster detection and endpoint configuration
- High-level orchestration operations

For backward compatibility, all functions from the original cluster.py are re-exported here.
"""


# Re-export all public functions for backward compatibility
# Common utilities and types
from .common import (
    ClusterType,
    format_cluster_name,
    get_extra_certs_path,
    run_command,
    run_command_with_output,
    validate_cluster_name,
    validate_cluster_type,
)

# Detection and endpoints
from .detection import auto_detect_cluster_type, detect_cluster_type, detect_existing_cluster_type
from .endpoints import configure_endpoints, get_ip_generic

# Helm operations
from .helm import install_jumpstarter_helm_chart

# Kind cluster operations
from .kind import (
    create_kind_cluster,
    delete_kind_cluster,
    kind_cluster_exists,
    kind_installed,
    list_kind_clusters,
)

# Kubectl operations
from .kubectl import (
    check_jumpstarter_installation,
    check_kubernetes_access,
    get_cluster_info,
    get_kubectl_contexts,
    list_clusters,
)
from .kubectl import get_kubectl_contexts as list_kubectl_contexts

# Minikube cluster operations
from .minikube import (
    create_minikube_cluster,
    delete_minikube_cluster,
    get_minikube_cluster_ip,
    list_minikube_clusters,
    minikube_cluster_exists,
    minikube_installed,
)

# High-level operations
from .operations import (
    create_cluster_and_install,
    create_cluster_only,
    delete_cluster_by_name,
    validate_cluster_type_selection,
)

# All module functions are imported above and available through clean re-exports

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
    # Utility functions
    "run_command",
    "run_command_with_output",
]
