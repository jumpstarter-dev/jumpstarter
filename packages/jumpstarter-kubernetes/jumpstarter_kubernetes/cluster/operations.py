"""High-level cluster operations and orchestration."""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Literal, Optional

import click
from jumpstarter_cli_common.version import get_client_version

from ..controller import get_latest_compatible_controller_version
from ..install import helm_installed
from .common import ClusterType, validate_cluster_name
from .detection import auto_detect_cluster_type, detect_existing_cluster_type
from .endpoints import configure_endpoints
from .helm import install_jumpstarter_helm_chart
from .kind import create_kind_cluster, delete_kind_cluster, kind_cluster_exists, kind_installed
from .minikube import create_minikube_cluster, delete_minikube_cluster, minikube_cluster_exists, minikube_installed


async def inject_certs_in_kind(extra_certs: str, cluster_name: str) -> None:
    """Inject custom certificates into a Kind cluster."""
    extra_certs_path = os.path.abspath(extra_certs)

    if not os.path.exists(extra_certs_path):
        raise click.ClickException(f"Extra certificates file not found: {extra_certs_path}")

    # Detect Kind provider info
    from .detection import detect_kind_provider
    runtime, node_name = await detect_kind_provider(cluster_name)

    click.echo(f"Injecting certificates from {extra_certs_path} into Kind cluster...")

    # Copy certificates into the Kind node
    copy_cmd = [runtime, "cp", extra_certs_path, f"{node_name}:/usr/local/share/ca-certificates/extra-certs.crt"]

    process = await asyncio.create_subprocess_exec(*copy_cmd)
    returncode = await process.wait()

    if returncode != 0:
        raise click.ClickException(f"Failed to copy certificates to Kind node: {node_name}")

    # Update ca-certificates in the node
    update_cmd = [runtime, "exec", node_name, "update-ca-certificates"]

    process = await asyncio.create_subprocess_exec(*update_cmd)
    returncode = await process.wait()

    if returncode != 0:
        raise click.ClickException("Failed to update certificates in Kind node")

    click.echo("Successfully injected custom certificates into Kind cluster")


async def prepare_minikube_certs(extra_certs: str) -> None:
    """Prepare custom certificates for Minikube."""
    extra_certs_path = os.path.abspath(extra_certs)

    if not os.path.exists(extra_certs_path):
        raise click.ClickException(f"Extra certificates file not found: {extra_certs_path}")

    # Create .minikube/certs directory if it doesn't exist
    minikube_certs_dir = Path.home() / ".minikube" / "certs"
    minikube_certs_dir.mkdir(parents=True, exist_ok=True)

    # Copy the certificate file to minikube certs directory
    import shutil
    cert_dest = minikube_certs_dir / "ca.crt"

    # If ca.crt already exists, append to it
    if cert_dest.exists():
        with open(extra_certs_path, 'r') as src, open(cert_dest, 'a') as dst:
            dst.write('\n')
            dst.write(src.read())
    else:
        shutil.copy2(extra_certs_path, cert_dest)

    click.echo(f"Prepared custom certificates for Minikube: {cert_dest}")


async def create_kind_cluster_wrapper(
    kind: str, cluster_name: str, kind_extra_args: str, force_recreate_cluster: bool, extra_certs: Optional[str] = None
) -> None:
    """Create a Kind cluster with optional certificate injection."""
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Kind cluster "{cluster_name}"...')
    extra_args_list = kind_extra_args.split() if kind_extra_args.strip() else []
    try:
        await create_kind_cluster(kind, cluster_name, extra_args_list, force_recreate_cluster)
        if force_recreate_cluster:
            click.echo(f'Successfully recreated Kind cluster "{cluster_name}"')
        else:
            click.echo(f'Successfully created Kind cluster "{cluster_name}"')

        # Inject custom certificates if provided
        if extra_certs:
            await inject_certs_in_kind(extra_certs, cluster_name)

    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Kind cluster "{cluster_name}" already exists, continuing...')
            # Still inject certificates if cluster exists and custom_certs provided
            if extra_certs:
                await inject_certs_in_kind(extra_certs, cluster_name)
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Kind cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Kind cluster: {e}") from e


async def create_minikube_cluster_wrapper(
    minikube: str,
    cluster_name: str,
    minikube_extra_args: str,
    force_recreate_cluster: bool,
    extra_certs: Optional[str] = None,
) -> None:
    """Create a Minikube cluster with optional certificate preparation."""
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Minikube cluster "{cluster_name}"...')
    extra_args_list = minikube_extra_args.split() if minikube_extra_args.strip() else []

    # Prepare custom certificates for Minikube if provided
    if extra_certs:
        await prepare_minikube_certs(extra_certs)
        # Always add --embed-certs for container drivers, we'll detect actual driver later
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


async def delete_kind_cluster_wrapper(kind: str, cluster_name: str) -> None:
    """Delete a Kind cluster with user feedback."""
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    click.echo(f'Deleting Kind cluster "{cluster_name}"...')
    try:
        await delete_kind_cluster(kind, cluster_name)
        click.echo(f'Successfully deleted Kind cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Kind cluster: {e}") from e


async def delete_minikube_cluster_wrapper(minikube: str, cluster_name: str) -> None:
    """Delete a Minikube cluster with user feedback."""
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    click.echo(f'Deleting Minikube cluster "{cluster_name}"...')
    try:
        await delete_minikube_cluster(minikube, cluster_name)
        click.echo(f'Successfully deleted Minikube cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Minikube cluster: {e}") from e


def validate_cluster_type_selection(kind: Optional[str], minikube: Optional[str]) -> ClusterType:
    """Validate cluster type selection and return the cluster type."""
    if kind and minikube:
        raise click.ClickException('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        # Auto-detect cluster type when neither is specified
        return auto_detect_cluster_type()


async def delete_cluster_by_name(cluster_name: str, cluster_type: Optional[str] = None, force: bool = False) -> None:
    """Delete a cluster by name, with auto-detection if type not specified."""
    # Validate cluster name
    cluster_name = validate_cluster_name(cluster_name)

    # If cluster type is specified, validate and use it
    if cluster_type:
        if cluster_type == "kind":
            if not kind_installed("kind"):
                raise click.ClickException("Kind is not installed")
            if not await kind_cluster_exists("kind", cluster_name):
                raise click.ClickException(f'Kind cluster "{cluster_name}" does not exist')
        elif cluster_type == "minikube":
            if not minikube_installed("minikube"):
                raise click.ClickException("Minikube is not installed")
            if not await minikube_cluster_exists("minikube", cluster_name):
                raise click.ClickException(f'Minikube cluster "{cluster_name}" does not exist')
    else:
        # Auto-detect cluster type
        detected_type = await detect_existing_cluster_type(cluster_name)
        if detected_type is None:
            raise click.ClickException(f'No cluster named "{cluster_name}" found')
        cluster_type = detected_type
        click.echo(f'Auto-detected {cluster_type} cluster "{cluster_name}"')

    # Confirm deletion unless force is specified
    if not force:
        if not click.confirm(
            f'⚠️  WARNING: This will permanently delete the "{cluster_name}" {cluster_type} cluster and ALL its data. Continue?'
        ):
            click.echo("Cluster deletion cancelled.")
            return

    # Delete the cluster
    if cluster_type == "kind":
        await delete_kind_cluster_wrapper("kind", cluster_name)
    elif cluster_type == "minikube":
        await delete_minikube_cluster_wrapper("minikube", cluster_name)

    click.echo(f'Successfully deleted {cluster_type} cluster "{cluster_name}"')


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
) -> None:
    """Create a cluster and optionally install Jumpstarter."""
    # Validate cluster name
    cluster_name = validate_cluster_name(cluster_name)

    if force_recreate_cluster:
        click.echo(f'⚠️  WARNING: Force recreating cluster "{cluster_name}" will destroy ALL data in the cluster!')
        click.echo("This includes:")
        click.echo("  • All deployed applications and services")
        click.echo("  • All persistent volumes and data")
        click.echo("  • All configurations and secrets")
        click.echo("  • All custom resources")
        if not click.confirm(f'Are you sure you want to recreate cluster "{cluster_name}"?'):
            click.echo("Cluster recreation cancelled.")
            raise click.Abort()

    # Create the cluster
    if cluster_type == "kind":
        await create_kind_cluster_wrapper(kind, cluster_name, kind_extra_args, force_recreate_cluster, extra_certs)
    elif cluster_type == "minikube":
        await create_minikube_cluster_wrapper(minikube, cluster_name, minikube_extra_args, force_recreate_cluster, extra_certs)

    # Install Jumpstarter if requested
    if install_jumpstarter:
        if not helm_installed(helm):
            raise click.ClickException(f"helm is not installed (or not in your PATH): {helm}")

        # Configure endpoints
        actual_ip, actual_basedomain, actual_grpc, actual_router = await configure_endpoints(
            cluster_type, minikube, cluster_name, ip, basedomain, grpc_endpoint, router_endpoint
        )

        # Get version if not specified
        if version is None:
            version = await get_latest_compatible_controller_version(get_client_version())

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
    )


async def _handle_cluster_creation(
    create_cluster: bool,
    cluster_type: Optional[ClusterType],
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
    extra_certs: Optional[str] = None,
) -> None:
    """Handle conditional cluster creation logic."""
    if not create_cluster:
        return

    if cluster_type is None:
        raise click.ClickException("--create-cluster requires either --kind or --minikube")

    # Handle force recreation confirmation
    if force_recreate_cluster:
        # Import from admin module if available for test compatibility
        try:
            # This makes the patch at jumpstarter_cli_admin.install.click.confirm work
            import jumpstarter_cli_admin.install as admin_install
            confirm_func = admin_install.click.confirm
        except (ImportError, AttributeError):
            confirm_func = click.confirm

        if not confirm_func(f'Are you sure you want to recreate cluster "{cluster_name}"?'):
            raise click.Abort()

    if cluster_type == "kind":
        # Import here to avoid circular imports
        from jumpstarter_kubernetes.cluster import _create_kind_cluster
        await _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster, extra_certs)
    elif cluster_type == "minikube":
        # Import here to avoid circular imports
        from jumpstarter_kubernetes.cluster import _create_minikube_cluster
        await _create_minikube_cluster(minikube, cluster_name, minikube_extra_args, force_recreate_cluster, extra_certs)


async def _handle_cluster_deletion(
    cluster_name: str,
    cluster_type: Optional[str] = None,
    force: bool = False,
) -> None:
    """Handle cluster deletion logic."""
    await delete_cluster_by_name(cluster_name, cluster_type, force)