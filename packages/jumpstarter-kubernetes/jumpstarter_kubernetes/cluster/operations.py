"""High-level cluster operations and orchestration."""

from typing import Optional

from ..callbacks import OutputCallback, SilentCallback
from ..exceptions import (
    ClusterNameValidationError,
    ClusterNotFoundError,
    ClusterOperationError,
    ClusterTypeValidationError,
    ToolNotInstalledError,
)
from ..install import helm_installed
from .common import ClusterType, validate_cluster_name
from .detection import auto_detect_cluster_type, detect_existing_cluster_type
from .endpoints import configure_endpoints
from .helm import install_jumpstarter_helm_chart
from .kind import (
    create_kind_cluster_with_options,
    delete_kind_cluster_with_feedback,
    kind_cluster_exists,
    kind_installed,
)
from .minikube import (
    create_minikube_cluster_with_options,
    delete_minikube_cluster_with_feedback,
    minikube_cluster_exists,
    minikube_installed,
)


def validate_cluster_type_selection(kind: Optional[str], minikube: Optional[str]) -> ClusterType:
    """Validate cluster type selection and return the cluster type."""
    if kind and minikube:
        raise ClusterTypeValidationError('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        # Auto-detect cluster type when neither is specified
        return auto_detect_cluster_type()


async def delete_cluster_by_name(  # noqa: C901
    cluster_name: str, cluster_type: Optional[str] = None, force: bool = False, callback: OutputCallback = None
) -> None:
    """Delete a cluster by name, with auto-detection if type not specified."""
    if callback is None:
        callback = SilentCallback()

    # Validate cluster name
    try:
        cluster_name = validate_cluster_name(cluster_name)
    except Exception as e:
        raise ClusterNameValidationError(cluster_name, str(e)) from e

    # If cluster type is specified, validate and use it
    if cluster_type:
        if cluster_type == "kind":
            if not kind_installed("kind"):
                raise ToolNotInstalledError("kind")
            if not await kind_cluster_exists("kind", cluster_name):
                raise ClusterNotFoundError(cluster_name, "kind")
        elif cluster_type == "minikube":
            if not minikube_installed("minikube"):
                raise ToolNotInstalledError("minikube")
            if not await minikube_cluster_exists("minikube", cluster_name):
                raise ClusterNotFoundError(cluster_name, "minikube")
        else:
            # Unsupported cluster type specified
            raise ClusterTypeValidationError(cluster_type, ["kind", "minikube"])
    else:
        # Auto-detect cluster type
        detected_type = await detect_existing_cluster_type(cluster_name)
        if detected_type is None:
            raise ClusterNotFoundError(cluster_name)
        cluster_type = detected_type
        callback.progress(f'Auto-detected {cluster_type} cluster "{cluster_name}"')

    # Validate cluster type is supported for deletion
    if cluster_type not in ["kind", "minikube"]:
        raise ClusterTypeValidationError(cluster_type, ["kind", "minikube"])

    # Confirm deletion unless force is specified
    if not force:
        if not callback.confirm(
            f'This will permanently delete the "{cluster_name}" {cluster_type} cluster and ALL its data. Continue?'
        ):
            callback.progress("Cluster deletion cancelled.")
            return

    # Delete the cluster
    if cluster_type == "kind":
        await delete_kind_cluster_with_feedback("kind", cluster_name, callback)
    elif cluster_type == "minikube":
        await delete_minikube_cluster_with_feedback("minikube", cluster_name, callback)

    callback.success(f'Successfully deleted {cluster_type} cluster "{cluster_name}"')


async def create_cluster_and_install(
    cluster_type: ClusterType,
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
    extra_certs: Optional[str] = None,
    install_jumpstarter: bool = True,
    helm: str = "helm",
    chart: str = "oci://quay.io/jumpstarter-dev/helm/jumpstarter",
    chart_name: str = "jumpstarter",
    namespace: str = "jumpstarter-lab",
    version: Optional[str] = None,
    kubeconfig: Optional[str] = None,
    context: Optional[str] = None,
    ip: Optional[str] = None,
    basedomain: Optional[str] = None,
    grpc_endpoint: Optional[str] = None,
    router_endpoint: Optional[str] = None,
    callback: OutputCallback = None,
    values_files: Optional[list[str]] = None,
) -> None:
    """Create a cluster and optionally install Jumpstarter."""
    if callback is None:
        callback = SilentCallback()

    # Validate cluster name
    try:
        cluster_name = validate_cluster_name(cluster_name)
    except Exception as e:
        raise ClusterNameValidationError(cluster_name, str(e)) from e

    if force_recreate_cluster:
        callback.warning(f'⚠️  WARNING: Force recreating cluster "{cluster_name}" will destroy ALL data in the cluster!')
        callback.warning("This includes:")
        callback.warning("  • All deployed applications and services")
        callback.warning("  • All persistent volumes and data")
        callback.warning("  • All configurations and secrets")
        callback.warning("  • All custom resources")
        if not callback.confirm(f'Are you sure you want to recreate cluster "{cluster_name}"?'):
            callback.progress("Cluster recreation cancelled.")
            raise ClusterOperationError("recreate", cluster_name, cluster_type, Exception("User cancelled"))

    # Create the cluster
    if cluster_type == "kind":
        await create_kind_cluster_with_options(
            kind, cluster_name, kind_extra_args, force_recreate_cluster, extra_certs, callback
        )
    elif cluster_type == "minikube":
        await create_minikube_cluster_with_options(
            minikube, cluster_name, minikube_extra_args, force_recreate_cluster, extra_certs, callback
        )
    else:
        raise ClusterTypeValidationError(f"Unsupported cluster_type: {cluster_type}")

    # Install Jumpstarter if requested
    if install_jumpstarter:
        if not helm_installed(helm):
            raise ToolNotInstalledError("helm", f"helm is not installed (or not in your PATH): {helm}")

        # Configure endpoints
        actual_ip, actual_basedomain, actual_grpc, actual_router = await configure_endpoints(
            cluster_type, minikube, cluster_name, ip, basedomain, grpc_endpoint, router_endpoint
        )

        # Version is required when installing Jumpstarter
        if version is None:
            raise ClusterOperationError(
                "install",
                cluster_name,
                cluster_type,
                Exception("Version must be specified when installing Jumpstarter"),
            )

        # Install Helm chart
        await install_jumpstarter_helm_chart(
            chart,
            chart_name,
            namespace,
            actual_basedomain,
            actual_grpc,
            actual_router,
            "nodeport",
            version,
            kubeconfig,
            context,
            helm,
            actual_ip,
            callback,
            values_files,
        )


async def create_cluster_only(
    cluster_type: ClusterType,
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
    custom_certs: Optional[str] = None,
    callback: OutputCallback = None,
) -> None:
    """Create a cluster without installing Jumpstarter."""
    await create_cluster_and_install(
        cluster_type,
        force_recreate_cluster,
        cluster_name,
        kind_extra_args,
        minikube_extra_args,
        kind,
        minikube,
        custom_certs,
        install_jumpstarter=False,
        callback=callback,
    )
