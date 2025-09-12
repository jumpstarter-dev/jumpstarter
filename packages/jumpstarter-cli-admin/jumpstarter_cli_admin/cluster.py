import shutil
from pathlib import Path
from typing import Literal, Optional

import click
from jumpstarter_kubernetes import (
    create_kind_cluster,
    create_minikube_cluster,
    delete_kind_cluster,
    delete_minikube_cluster,
    kind_installed,
    minikube_installed,
)
from jumpstarter_kubernetes.cluster import run_command


def _detect_container_runtime() -> str:
    """Detect available container runtime (docker or podman) for Kind"""
    if shutil.which("docker"):
        return "docker"
    elif shutil.which("podman"):
        return "podman"
    else:
        raise click.ClickException("Neither docker nor podman found in PATH. Kind requires a container runtime.")


async def _inject_certs_in_kind(custom_certs: str, cluster_name: str) -> None:
    """Inject custom CA certificates into a running Kind cluster"""
    runtime = _detect_container_runtime()
    container_name = f"kind-{cluster_name}"

    if cluster_name == "kind":
        container_name = "kind-control-plane"
    else:
        container_name = f"{cluster_name}-control-plane"

    cert_path = Path(custom_certs)
    if not cert_path.exists():
        raise click.ClickException(f"Certificate file not found: {custom_certs}")

    click.echo(f"Injecting custom CA certificates into Kind cluster '{cluster_name}'...")

    try:
        # Copy certificate bundle to the Kind container
        copy_cmd = [runtime, "cp", str(cert_path), f"{container_name}:/usr/local/share/ca-certificates/custom-ca.crt"]
        returncode, _, stderr = await run_command(copy_cmd)
        if returncode != 0:
            raise click.ClickException(f"Failed to copy certificates to Kind container: {stderr}")

        # Update CA certificates in the container
        update_cmd = [runtime, "exec", container_name, "update-ca-certificates"]
        returncode, _, stderr = await run_command(update_cmd)
        if returncode != 0:
            raise click.ClickException(f"Failed to update CA certificates in Kind container: {stderr}")

        # Restart containerd to apply changes
        restart_cmd = [runtime, "exec", container_name, "systemctl", "restart", "containerd"]
        returncode, _, stderr = await run_command(restart_cmd)
        if returncode != 0:
            raise click.ClickException(f"Failed to restart containerd in Kind container: {stderr}")

        click.echo("Successfully injected custom CA certificates into Kind cluster")

    except RuntimeError as e:
        raise click.ClickException(f"Failed to inject certificates into Kind cluster: {e}") from e


async def _prepare_minikube_certs(custom_certs: str) -> str:
    """Prepare custom CA certificates for Minikube by copying to ~/.minikube/certs/"""
    cert_path = Path(custom_certs)
    if not cert_path.exists():
        raise click.ClickException(f"Certificate file not found: {custom_certs}")

    minikube_certs_dir = Path.home() / ".minikube" / "certs"
    minikube_certs_dir.mkdir(parents=True, exist_ok=True)

    # Copy the certificate bundle to minikube certs directory
    dest_cert_path = minikube_certs_dir / "custom-ca.crt"

    click.echo(f"Copying custom CA certificates to {dest_cert_path}...")
    shutil.copy2(cert_path, dest_cert_path)

    return str(dest_cert_path)


def _auto_detect_cluster_type() -> Literal["kind"] | Literal["minikube"]:
    """Auto-detect available cluster type, preferring Kind over Minikube"""
    if kind_installed("kind"):
        return "kind"
    elif minikube_installed("minikube"):
        return "minikube"
    else:
        raise click.ClickException(
            "Neither Kind nor Minikube is installed. Please install one of them:\n"
            "  • Kind: https://kind.sigs.k8s.io/docs/user/quick-start/\n"
            "  • Minikube: https://minikube.sigs.k8s.io/docs/start/"
        )


def _validate_cluster_type(kind: Optional[str], minikube: Optional[str]) -> Literal["kind"] | Literal["minikube"]:
    if kind and minikube:
        raise click.ClickException('You can only select one local cluster type "kind" or "minikube"')

    if kind is not None:
        return "kind"
    elif minikube is not None:
        return "minikube"
    else:
        # Auto-detect cluster type when neither is specified
        return _auto_detect_cluster_type()


async def _create_kind_cluster(
    kind: str, cluster_name: str, kind_extra_args: str, force_recreate_cluster: bool, custom_certs: Optional[str] = None
) -> None:
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
        if custom_certs:
            await _inject_certs_in_kind(custom_certs, cluster_name)

    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Kind cluster "{cluster_name}" already exists, continuing...')
            # Still inject certificates if cluster exists and custom_certs provided
            if custom_certs:
                await _inject_certs_in_kind(custom_certs, cluster_name)
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Kind cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Kind cluster: {e}") from e


async def _create_minikube_cluster(
    minikube: str,
    cluster_name: str,
    minikube_extra_args: str,
    force_recreate_cluster: bool,
    custom_certs: Optional[str] = None,
) -> None:
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Minikube cluster "{cluster_name}"...')
    extra_args_list = minikube_extra_args.split() if minikube_extra_args.strip() else []

    # Prepare custom certificates for Minikube if provided
    if custom_certs:
        await _prepare_minikube_certs(custom_certs)
        # Add --embed-certs flag to ensure certificates are embedded
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


async def _delete_kind_cluster(kind: str, cluster_name: str) -> None:
    if not kind_installed(kind):
        raise click.ClickException("kind is not installed (or not in your PATH)")

    click.echo(f'Deleting Kind cluster "{cluster_name}"...')
    try:
        await delete_kind_cluster(kind, cluster_name)
        click.echo(f'Successfully deleted Kind cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Kind cluster: {e}") from e


async def _delete_minikube_cluster(minikube: str, cluster_name: str) -> None:
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    click.echo(f'Deleting Minikube cluster "{cluster_name}"...')
    try:
        await delete_minikube_cluster(minikube, cluster_name)
        click.echo(f'Successfully deleted Minikube cluster "{cluster_name}"')
    except RuntimeError as e:
        raise click.ClickException(f"Failed to delete Minikube cluster: {e}") from e


async def _handle_cluster_creation(
    create_cluster: bool,
    cluster_type: Literal["kind"] | Literal["minikube"],
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
    custom_certs: Optional[str] = None,
) -> None:
    if not create_cluster:
        return

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

    if cluster_type == "kind":
        await _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster, custom_certs)
    elif cluster_type == "minikube":
        await _create_minikube_cluster(
            minikube, cluster_name, minikube_extra_args, force_recreate_cluster, custom_certs
        )


async def _handle_cluster_deletion(kind: Optional[str], minikube: Optional[str], cluster_name: str) -> None:
    if kind is None and minikube is None:
        return  # No cluster type specified, nothing to delete

    cluster_type = _validate_cluster_type(kind, minikube)

    if not click.confirm(
        f'⚠️  WARNING: This will permanently delete the "{cluster_name}" {cluster_type} cluster and ALL its data. Continue?'  # noqa: E501
    ):
        click.echo("Cluster deletion cancelled.")
        return

    if cluster_type == "kind":
        await _delete_kind_cluster(kind or "kind", cluster_name)
    elif cluster_type == "minikube":
        await _delete_minikube_cluster(minikube or "minikube", cluster_name)


async def create_cluster_only(
    cluster_type: Literal["kind"] | Literal["minikube"],
    force_recreate_cluster: bool,
    cluster_name: str,
    kind_extra_args: str,
    minikube_extra_args: str,
    kind: str,
    minikube: str,
    custom_certs: Optional[str] = None,
) -> None:
    """Create a cluster without installing Jumpstarter"""

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

    if cluster_type == "kind":
        await _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster, custom_certs)
    elif cluster_type == "minikube":
        await _create_minikube_cluster(
            minikube, cluster_name, minikube_extra_args, force_recreate_cluster, custom_certs
        )
