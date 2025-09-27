"""Common utilities and types for cluster operations."""

import os
from typing import Literal, Optional

import click


ClusterType = Literal["kind"] | Literal["minikube"]


def validate_cluster_type(
    kind: Optional[str], minikube: Optional[str]
) -> Optional[ClusterType]:
    """Validate cluster type selection - returns None if neither is specified."""
    if kind and minikube:
        raise click.ClickException('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        return None


def get_extra_certs_path(extra_certs: Optional[str]) -> Optional[str]:
    """Get the absolute path to extra certificates file if provided."""
    if extra_certs is None:
        return None
    return os.path.abspath(extra_certs)


def format_cluster_name(cluster_name: str) -> str:
    """Format cluster name for consistent display."""
    return cluster_name.strip()


def validate_cluster_name(cluster_name: str) -> str:
    """Validate and format cluster name."""
    if not cluster_name or not cluster_name.strip():
        raise click.ClickException("Cluster name cannot be empty")
    return format_cluster_name(cluster_name)