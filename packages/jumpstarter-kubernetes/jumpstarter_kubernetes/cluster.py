import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple

import click
from jumpstarter_cli_common.version import get_client_version

from .clusters import V1Alpha1ClusterInfo, V1Alpha1ClusterList, V1Alpha1JumpstarterInstance
from .controller import get_latest_compatible_controller_version
from .install import helm_installed


def minikube_installed(minikube: str) -> bool:
    """Check if Minikube is installed and available in the PATH."""
    return shutil.which(minikube) is not None


def kind_installed(kind: str) -> bool:
    """Check if Kind is installed and available in the PATH."""
    return shutil.which(kind) is not None


async def run_command(cmd: list[str]) -> Tuple[int, str, str]:
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


async def kind_cluster_exists(kind: str, cluster_name: str) -> bool:
    """Check if a Kind cluster exists."""
    if not kind_installed(kind):
        return False

    try:
        returncode, _, _ = await run_command([kind, "get", "kubeconfig", "--name", cluster_name])
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


async def delete_kind_cluster(kind: str, cluster_name: str) -> bool:
    """Delete a Kind cluster."""
    if not kind_installed(kind):
        raise RuntimeError(f"{kind} is not installed or not found in PATH.")

    if not await kind_cluster_exists(kind, cluster_name):
        return True  # Already deleted, consider it successful

    returncode = await run_command_with_output([kind, "delete", "cluster", "--name", cluster_name])

    if returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to delete Kind cluster '{cluster_name}'")


async def create_minikube_cluster(
    minikube: str, cluster_name: str, extra_args: Optional[list[str]] = None, force_recreate: bool = False
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


async def create_kind_cluster(
    kind: str, cluster_name: str, extra_args: Optional[list[str]] = None, force_recreate: bool = False
) -> bool:
    """Create a Kind cluster."""
    if extra_args is None:
        extra_args = []

    if not kind_installed(kind):
        raise RuntimeError(f"{kind} is not installed or not found in PATH.")

    # Check if cluster already exists
    cluster_exists = await kind_cluster_exists(kind, cluster_name)

    if cluster_exists:
        if not force_recreate:
            raise RuntimeError(f"Kind cluster '{cluster_name}' already exists.")
        else:
            if not await delete_kind_cluster(kind, cluster_name):
                return False

    cluster_config = """kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      "service-node-port-range": "3000-32767"
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 80
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011
    hostPort: 8083
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
"""

    command = [kind, "create", "cluster", "--name", cluster_name, "--config=/dev/stdin"]
    command.extend(extra_args)

    kind_process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE)

    await kind_process.communicate(input=cluster_config.encode())

    if kind_process.returncode == 0:
        return True
    else:
        raise RuntimeError(f"Failed to create Kind cluster '{cluster_name}'")


def _detect_container_runtime() -> str:
    """Detect available container runtime for Kind"""
    if shutil.which("docker"):
        return "docker"
    elif shutil.which("podman"):
        return "podman"
    elif shutil.which("nerdctl"):
        return "nerdctl"
    else:
        raise click.ClickException(
            "No supported container runtime found in PATH. Kind requires docker, podman, or nerdctl."
        )


async def _detect_kind_provider(cluster_name: str) -> tuple[str, str]:
    """Detect Kind provider and return (runtime, node_name)"""
    runtime = _detect_container_runtime()

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

    # Fallback to standard naming
    if cluster_name == "kind":
        return runtime, "kind-control-plane"
    else:
        return runtime, f"{cluster_name}-control-plane"


async def _inject_certs_via_ssh(cluster_name: str, custom_certs: str) -> None:
    """Inject certificates via SSH (fallback method for VMs or other backends)"""
    cert_path = Path(custom_certs)
    if not cert_path.exists():
        raise click.ClickException(f"Certificate file not found: {custom_certs}")

    try:
        # Try using docker exec with SSH-like approach
        node_name = f"{cluster_name}-control-plane"
        if cluster_name == "kind":
            node_name = "kind-control-plane"

        # Copy cert file to a temp location in the container
        temp_cert_path = f"/tmp/custom-ca-{os.getpid()}.crt"

        # Read cert content and write it to the container
        with open(cert_path, "r") as f:
            cert_content = f.read()

        # Write cert content to container
        write_cmd = ["docker", "exec", node_name, "sh", "-c", f"cat > {temp_cert_path}"]
        process = await asyncio.create_subprocess_exec(
            *write_cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate(input=cert_content.encode())

        if process.returncode != 0:
            raise RuntimeError(f"Failed to write certificate: {stderr.decode()}")

        # Move cert to proper location
        mv_cmd = ["docker", "exec", node_name, "mv", temp_cert_path, "/usr/local/share/ca-certificates/custom-ca.crt"]
        returncode, _, stderr = await run_command(mv_cmd)
        if returncode != 0:
            raise RuntimeError(f"Failed to move certificate: {stderr}")

        # Update CA certificates
        update_cmd = ["docker", "exec", node_name, "update-ca-certificates"]
        returncode, _, stderr = await run_command(update_cmd)
        if returncode != 0:
            raise RuntimeError(f"Failed to update CA certificates: {stderr}")

        # Restart containerd
        restart_cmd = ["docker", "exec", node_name, "systemctl", "restart", "containerd"]
        returncode, _, stderr = await run_command(restart_cmd)
        if returncode != 0:
            # Try alternative restart methods
            restart_cmd2 = ["docker", "exec", node_name, "pkill", "-HUP", "containerd"]
            returncode2, _, _ = await run_command(restart_cmd2)
            if returncode2 != 0:
                click.echo("Warning: Could not restart containerd, certificates may not be fully applied")

        click.echo("Successfully injected custom CA certificates via SSH method")

    except Exception as e:
        raise click.ClickException(f"Failed to inject certificates via SSH method: {e}") from e


async def _inject_certs_in_kind(custom_certs: str, cluster_name: str) -> None:
    """Inject custom CA certificates into a running Kind cluster"""
    cert_path = Path(custom_certs)
    if not cert_path.exists():
        raise click.ClickException(f"Certificate file not found: {custom_certs}")

    click.echo(f"Injecting custom CA certificates into Kind cluster '{cluster_name}'...")

    try:
        # First, try to detect the Kind provider and node name
        runtime, container_name = await _detect_kind_provider(cluster_name)

        click.echo(f"Detected Kind runtime: {runtime}, node: {container_name}")

        # Try direct container approach first
        try:
            # Copy certificate bundle to the Kind container
            copy_cmd = [
                runtime,
                "cp",
                str(cert_path),
                f"{container_name}:/usr/local/share/ca-certificates/custom-ca.crt",
            ]
            returncode, _, stderr = await run_command(copy_cmd)
            if returncode != 0:
                raise RuntimeError(f"Failed to copy certificates: {stderr}")

            # Update CA certificates in the container
            update_cmd = [runtime, "exec", container_name, "update-ca-certificates"]
            returncode, _, stderr = await run_command(update_cmd)
            if returncode != 0:
                raise RuntimeError(f"Failed to update CA certificates: {stderr}")

            # Restart containerd to apply changes
            restart_cmd = [runtime, "exec", container_name, "systemctl", "restart", "containerd"]
            returncode, _, stderr = await run_command(restart_cmd)
            if returncode != 0:
                # Try alternative restart methods for different container runtimes
                click.echo("Trying alternative containerd restart method...")
                restart_cmd2 = [runtime, "exec", container_name, "pkill", "-HUP", "containerd"]
                returncode2, _, _ = await run_command(restart_cmd2)
                if returncode2 != 0:
                    click.echo("Warning: Could not restart containerd, certificates may not be fully applied")

            click.echo("Successfully injected custom CA certificates into Kind cluster")
            return

        except RuntimeError as e:
            click.echo(f"Direct container method failed: {e}")
            click.echo("Trying SSH-based fallback method...")

            # Fallback to SSH-based injection
            await _inject_certs_via_ssh(cluster_name, custom_certs)
            return

    except Exception as e:
        raise click.ClickException(f"Failed to inject certificates into Kind cluster: {e}") from e


async def _prepare_minikube_certs(custom_certs: str) -> str:
    """Prepare custom CA certificates for Minikube by copying to ~/.minikube/certs/"""
    cert_path = Path(custom_certs)
    if not cert_path.exists():
        raise click.ClickException(f"Certificate file not found: {custom_certs}")

    # Always copy certificates to minikube certs directory for --embed-certs to work
    minikube_certs_dir = Path.home() / ".minikube" / "certs"
    minikube_certs_dir.mkdir(parents=True, exist_ok=True)

    # Copy the certificate bundle to minikube certs directory
    dest_cert_path = minikube_certs_dir / "custom-ca.crt"

    click.echo(f"Copying custom CA certificates to {dest_cert_path}...")
    shutil.copy2(cert_path, dest_cert_path)

    return str(dest_cert_path)


async def get_ip_generic(cluster_type: Optional[str], minikube: str, cluster_name: str) -> str:
    """Get IP address for cluster type"""
    from jumpstarter.common.ipaddr import get_ip_address, get_minikube_ip

    if cluster_type == "minikube":
        if not minikube_installed(minikube):
            raise click.ClickException("minikube is not installed (or not in your PATH)")
        try:
            ip = await get_minikube_ip(cluster_name, minikube)
        except Exception as e:
            raise click.ClickException(f"Could not determine Minikube IP address.\n{e}") from e
    else:
        ip = get_ip_address()
        if ip == "0.0.0.0":
            raise click.ClickException("Could not determine IP address, use --ip <IP> to specify an IP address")

    return ip


async def _configure_endpoints(
    cluster_type: Optional[str],
    minikube: str,
    cluster_name: str,
    ip: Optional[str],
    basedomain: Optional[str],
    grpc_endpoint: Optional[str],
    router_endpoint: Optional[str],
) -> tuple[str, str, str, str]:
    """Configure endpoints for Jumpstarter installation"""
    if ip is None:
        ip = await get_ip_generic(cluster_type, minikube, cluster_name)
    if basedomain is None:
        basedomain = f"jumpstarter.{ip}.nip.io"
    if grpc_endpoint is None:
        grpc_endpoint = f"grpc.{basedomain}:8082"
    if router_endpoint is None:
        router_endpoint = f"router.{basedomain}:8083"

    return ip, basedomain, grpc_endpoint, router_endpoint


async def _install_jumpstarter_helm_chart(
    chart: str,
    name: str,
    namespace: str,
    basedomain: str,
    grpc_endpoint: str,
    router_endpoint: str,
    mode: str,
    version: str,
    kubeconfig: Optional[str],
    context: Optional[str],
    helm: str,
    ip: str,
) -> None:
    """Install Jumpstarter Helm chart"""
    from .install import install_helm_chart

    click.echo(f'Installing Jumpstarter service v{version} in namespace "{namespace}" with Helm\n')
    click.echo(f"Chart URI: {chart}")
    click.echo(f"Chart Version: {version}")
    click.echo(f"IP Address: {ip}")
    click.echo(f"Basedomain: {basedomain}")
    click.echo(f"Service Endpoint: {grpc_endpoint}")
    click.echo(f"Router Endpoint: {router_endpoint}")
    click.echo(f"gRPC Mode: {mode}\n")

    await install_helm_chart(
        chart, name, namespace, basedomain, grpc_endpoint, router_endpoint, mode, version, kubeconfig, context, helm
    )

    click.echo(f'Installed Helm release "{name}" in namespace "{namespace}"')


async def _detect_existing_cluster_type(cluster_name: str) -> Optional[Literal["kind"] | Literal["minikube"]]:
    """Detect which type of cluster exists with the given name"""
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
        raise click.ClickException(
            f'Both Kind and Minikube clusters named "{cluster_name}" exist. '
            "Please specify --kind or --minikube to choose which one to delete."
        )
    elif kind_exists:
        return "kind"
    elif minikube_exists:
        return "minikube"
    else:
        return None


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
    kind: str, cluster_name: str, kind_extra_args: str, force_recreate_cluster: bool, extra_certs: Optional[str] = None
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
        if extra_certs:
            await _inject_certs_in_kind(extra_certs, cluster_name)

    except RuntimeError as e:
        if "already exists" in str(e) and not force_recreate_cluster:
            click.echo(f'Kind cluster "{cluster_name}" already exists, continuing...')
            # Still inject certificates if cluster exists and custom_certs provided
            if extra_certs:
                await _inject_certs_in_kind(extra_certs, cluster_name)
        else:
            if force_recreate_cluster:
                raise click.ClickException(f"Failed to recreate Kind cluster: {e}") from e
            else:
                raise click.ClickException(f"Failed to create Kind cluster: {e}") from e


async def _create_minikube_cluster(  # noqa: C901
    minikube: str,
    cluster_name: str,
    minikube_extra_args: str,
    force_recreate_cluster: bool,
    extra_certs: Optional[str] = None,
) -> None:
    if not minikube_installed(minikube):
        raise click.ClickException("minikube is not installed (or not in your PATH)")

    cluster_action = "Recreating" if force_recreate_cluster else "Creating"
    click.echo(f'{cluster_action} Minikube cluster "{cluster_name}"...')
    extra_args_list = minikube_extra_args.split() if minikube_extra_args.strip() else []

    # Prepare custom certificates for Minikube if provided
    if extra_certs:
        await _prepare_minikube_certs(extra_certs)
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


async def delete_cluster_by_name(cluster_name: str, cluster_type: Optional[str] = None, force: bool = False) -> None:  # noqa: C901
    """Delete a cluster by name, with auto-detection if type not specified"""

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
        detected_type = await _detect_existing_cluster_type(cluster_name)
        if detected_type is None:
            raise click.ClickException(f'No cluster named "{cluster_name}" found')
        cluster_type = detected_type
        click.echo(f'Auto-detected {cluster_type} cluster "{cluster_name}"')

    # Confirm deletion unless force is specified
    if not force:
        if not click.confirm(
            f'⚠️  WARNING: This will permanently delete the "{cluster_name}" {cluster_type} cluster and ALL its data. Continue?'  # noqa: E501
        ):
            click.echo("Cluster deletion cancelled.")
            return

    # Delete the cluster
    if cluster_type == "kind":
        await _delete_kind_cluster("kind", cluster_name)
    elif cluster_type == "minikube":
        await _delete_minikube_cluster("minikube", cluster_name)

    click.echo(f'Successfully deleted {cluster_type} cluster "{cluster_name}"')


async def create_cluster_and_install(
    cluster_type: Literal["kind"] | Literal["minikube"],
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
    """Create a cluster and optionally install Jumpstarter"""

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
        await _create_kind_cluster(kind, cluster_name, kind_extra_args, force_recreate_cluster, extra_certs)
    elif cluster_type == "minikube":
        await _create_minikube_cluster(minikube, cluster_name, minikube_extra_args, force_recreate_cluster, extra_certs)

    # Install Jumpstarter if requested
    if install_jumpstarter:
        if not helm_installed(helm):
            raise click.ClickException(f"helm is not installed (or not in your PATH): {helm}")

        # Configure endpoints
        actual_ip, actual_basedomain, actual_grpc, actual_router = await _configure_endpoints(
            cluster_type, minikube, cluster_name, ip, basedomain, grpc_endpoint, router_endpoint
        )

        # Get version if not specified
        if version is None:
            version = await get_latest_compatible_controller_version(get_client_version())

        # Install Helm chart
        await _install_jumpstarter_helm_chart(
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


# Backwards compatibility function
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


async def list_kubectl_contexts(kubectl: str = "kubectl") -> List[Dict[str, Any]]:
    """List all available kubectl contexts"""
    try:
        # Get config view with contexts
        cmd = [kubectl, "config", "view", "-o", "json"]
        returncode, stdout, stderr = await run_command(cmd)

        if returncode != 0:
            raise click.ClickException(f"Failed to get kubectl contexts: {stderr}")

        config = json.loads(stdout)
        contexts = []

        current_context = config.get("current-context", "")

        for ctx in config.get("contexts", []):
            context_name = ctx.get("name", "")
            context_info = ctx.get("context", {})
            cluster_name = context_info.get("cluster", "")
            user = context_info.get("user", "")
            namespace = context_info.get("namespace", "default")

            # Get cluster info
            cluster_info = {}
            for cluster in config.get("clusters", []):
                if cluster.get("name") == cluster_name:
                    cluster_info = cluster.get("cluster", {})
                    break

            contexts.append(
                {
                    "name": context_name,
                    "cluster": cluster_name,
                    "user": user,
                    "namespace": namespace,
                    "server": cluster_info.get("server", ""),
                    "is_current": context_name == current_context,
                }
            )

        return contexts

    except json.JSONDecodeError as e:
        raise click.ClickException(f"Failed to parse kubectl config: {e}") from e
    except Exception as e:
        raise click.ClickException(f"Error listing kubectl contexts: {e}") from e


async def detect_cluster_type(context_name: str, server_url: str, minikube: str = "minikube") -> str:  # noqa: C901
    """Detect if cluster is Kind, Minikube, or Remote"""
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
    import re

    minikube_pattern_1 = re.search(r"192\.168\.\d+\.\d+:(8443|443)", server_url)
    minikube_pattern_2 = re.search(r"172\.\d+\.\d+\.\d+:(8443|443)", server_url)
    if minikube_pattern_1 or minikube_pattern_2:
        # Try to verify it's actually minikube by checking if any minikube cluster exists
        try:
            # Get list of minikube profiles
            cmd = [minikube, "profile", "list", "-o", "json"]
            returncode, stdout, _ = await run_command(cmd)
            if returncode == 0:
                import json

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


async def check_jumpstarter_installation(  # noqa: C901
    context: str, namespace: Optional[str] = None, helm: str = "helm", kubectl: str = "kubectl"
) -> V1Alpha1JumpstarterInstance:
    """Check if Jumpstarter is installed in the cluster"""
    result_data = {
        "installed": False,
        "version": None,
        "namespace": None,
        "chart_name": None,
        "status": None,
        "has_crds": False,
        "error": None,
        "basedomain": None,
        "controller_endpoint": None,
        "router_endpoint": None,
    }

    try:
        # Check for Helm installation first
        helm_cmd = [helm, "list", "--all-namespaces", "-o", "json", "--kube-context", context]
        returncode, stdout, _ = await run_command(helm_cmd)

        if returncode == 0:
            releases = json.loads(stdout)
            for release in releases:
                # Look for Jumpstarter chart
                if "jumpstarter" in release.get("chart", "").lower():
                    result_data["installed"] = True
                    result_data["version"] = release.get("app_version") or release.get("chart", "").split("-")[-1]
                    result_data["namespace"] = release.get("namespace")
                    result_data["chart_name"] = release.get("name")
                    result_data["status"] = release.get("status")

                    # Try to get Helm values to extract basedomain and endpoints
                    try:
                        values_cmd = [
                            helm,
                            "get",
                            "values",
                            release.get("name"),
                            "-n",
                            release.get("namespace"),
                            "-o",
                            "json",
                            "--kube-context",
                            context,
                        ]
                        values_returncode, values_stdout, _ = await run_command(values_cmd)

                        if values_returncode == 0:
                            values = json.loads(values_stdout)

                            # Extract basedomain
                            basedomain = values.get("global", {}).get("baseDomain")
                            if basedomain:
                                result_data["basedomain"] = basedomain
                                # Construct default endpoints from basedomain
                                result_data["controller_endpoint"] = f"grpc.{basedomain}:8082"
                                result_data["router_endpoint"] = f"router.{basedomain}:8083"

                            # Check for explicit endpoints in values
                            controller_config = values.get("jumpstarter-controller", {}).get("grpc", {})
                            if controller_config.get("endpoint"):
                                result_data["controller_endpoint"] = controller_config["endpoint"]
                            if controller_config.get("routerEndpoint"):
                                result_data["router_endpoint"] = controller_config["routerEndpoint"]

                    except (json.JSONDecodeError, RuntimeError):
                        # Failed to get Helm values, but we still have basic info
                        pass

                    break

        # Check for Jumpstarter CRDs as secondary verification
        try:
            crd_cmd = [kubectl, "--context", context, "get", "crd", "-o", "json"]
            returncode, stdout, _ = await run_command(crd_cmd)

            if returncode == 0:
                crds = json.loads(stdout)
                jumpstarter_crds = []
                for item in crds.get("items", []):
                    name = item.get("metadata", {}).get("name", "")
                    if "jumpstarter.dev" in name:
                        jumpstarter_crds.append(name)

                if jumpstarter_crds:
                    result_data["has_crds"] = True
                    if not result_data["installed"]:
                        # CRDs exist but no Helm release found - manual installation?
                        result_data["installed"] = True
                        result_data["version"] = "unknown"
                        result_data["namespace"] = namespace or "unknown"
                        result_data["status"] = "manual-install"
        except RuntimeError:
            pass  # CRD check failed, continue with Helm results

    except json.JSONDecodeError as e:
        result_data["error"] = f"Failed to parse Helm output: {e}"
    except RuntimeError as e:
        result_data["error"] = f"Failed to check Helm releases: {e}"
    except Exception as e:
        result_data["error"] = f"Unexpected error: {e}"

    return V1Alpha1JumpstarterInstance(**result_data)


async def get_cluster_info(
    context: str,
    kubectl: str = "kubectl",
    helm: str = "helm",
    minikube: str = "minikube",
    check_connectivity: bool = False,
) -> V1Alpha1ClusterInfo:
    """Get comprehensive cluster information"""
    try:
        contexts = await list_kubectl_contexts(kubectl)
        context_info = None

        for ctx in contexts:
            if ctx["name"] == context:
                context_info = ctx
                break

        if not context_info:
            return V1Alpha1ClusterInfo(
                name=context,
                cluster="unknown",
                server="unknown",
                user="unknown",
                namespace="unknown",
                is_current=False,
                type="remote",
                accessible=False,
                jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
                error=f"Context '{context}' not found",
            )

        # Detect cluster type
        cluster_type = await detect_cluster_type(context_info["name"], context_info["server"], minikube)

        # Check if cluster is accessible
        try:
            version_cmd = [kubectl, "--context", context, "version", "-o", "json"]
            returncode, stdout, _ = await run_command(version_cmd)
            cluster_accessible = returncode == 0
            cluster_version = None

            if cluster_accessible:
                try:
                    version_info = json.loads(stdout)
                    cluster_version = version_info.get("serverVersion", {}).get("gitVersion", "unknown")
                except (json.JSONDecodeError, KeyError):
                    cluster_version = "unknown"
        except RuntimeError:
            cluster_accessible = False
            cluster_version = None

        # Check Jumpstarter installation
        if cluster_accessible:
            jumpstarter_info = await check_jumpstarter_installation(context, None, helm, kubectl)
        else:
            jumpstarter_info = V1Alpha1JumpstarterInstance(installed=False, error="Cluster not accessible")

        return V1Alpha1ClusterInfo(
            name=context_info["name"],
            cluster=context_info["cluster"],
            server=context_info["server"],
            user=context_info["user"],
            namespace=context_info["namespace"],
            is_current=context_info["is_current"],
            type=cluster_type,
            accessible=cluster_accessible,
            version=cluster_version,
            jumpstarter=jumpstarter_info,
        )

    except Exception as e:
        return V1Alpha1ClusterInfo(
            name=context,
            cluster="unknown",
            server="unknown",
            user="unknown",
            namespace="unknown",
            is_current=False,
            type="remote",
            accessible=False,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            error=f"Failed to get cluster info: {e}",
        )


async def list_clusters(
    cluster_type_filter: str = "all",
    kubectl: str = "kubectl",
    helm: str = "helm",
    kind: str = "kind",
    minikube: str = "minikube",
    check_connectivity: bool = False,
) -> V1Alpha1ClusterList:
    """List all Kubernetes clusters with Jumpstarter status"""
    try:
        contexts = await list_kubectl_contexts(kubectl)
        cluster_infos = []

        for context in contexts:
            cluster_info = await get_cluster_info(context["name"], kubectl, helm, minikube, check_connectivity)

            # Filter by type if specified
            if cluster_type_filter != "all" and cluster_info.type != cluster_type_filter:
                continue

            cluster_infos.append(cluster_info)

        return V1Alpha1ClusterList(items=cluster_infos)

    except Exception as e:
        # Return empty list with error in the first cluster
        error_cluster = V1Alpha1ClusterInfo(
            name="error",
            cluster="error",
            server="error",
            user="error",
            namespace="error",
            is_current=False,
            type="remote",
            accessible=False,
            jumpstarter=V1Alpha1JumpstarterInstance(installed=False),
            error=f"Failed to list clusters: {e}",
        )
        return V1Alpha1ClusterList(items=[error_cluster])
