from typing import Optional

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.callbacks import ClickCallback
from jumpstarter_cli_common.forward import rust_command
from jumpstarter_cli_common.opt import (
    OutputType,
    opt_context,
    opt_kubeconfig,
    opt_nointeractive,
    opt_output_all,
)
from jumpstarter_kubernetes import (
    create_cluster_and_install,
    validate_cluster_type_selection,
)
from jumpstarter_kubernetes.exceptions import JumpstarterKubernetesError


@click.group(cls=AliasedGroup)
def create():
    """Create Jumpstarter Kubernetes objects"""


# `create client` / `create exporter` run on the Rust core (forwarded via FFI): it talks to the
# cluster, fetches credentials + CA, and writes the local config. The `create cluster` subcommand
# (local kind/minikube/k3s provisioning) has no Rust equivalent and stays native Python.
create.add_command(rust_command(["admin", "create", "client"], "Create a client object in the cluster."))
create.add_command(rust_command(["admin", "create", "exporter"], "Create an exporter object in the cluster."))


@create.command("cluster")
@click.argument("name", type=str, required=False, default="jumpstarter-lab")
@click.option("--kind", is_flag=False, flag_value="kind", default=None, help="Create a local Kind cluster")
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Create a local Minikube cluster",
)
@click.option(
    "--k3s",
    type=str,
    default=None,
    help="Create a k3s cluster on a remote host via SSH (e.g., --k3s user@host)",
)
@click.option(
    "--force-recreate",
    is_flag=True,
    help="Force recreate the cluster if it already exists (WARNING: This will destroy all data in the cluster)",
)
@click.option("--kind-extra-args", type=str, help="Extra arguments for the Kind cluster creation", default="")
@click.option("--minikube-extra-args", type=str, help="Extra arguments for the Minikube cluster creation", default="")
@click.option(
    "--extra-certs",
    type=click.Path(exists=True, readable=True, dir_okay=False, resolve_path=True),
    help="Path to custom CA certificate bundle file to inject into the cluster",
)
@click.option(
    "--skip-install",
    is_flag=True,
    help="Skip installing Jumpstarter after creating the cluster",
)
@click.option(
    "--operator-installer",
    type=str,
    default=None,
    help="Path or URL to the operator installer YAML (auto-detected from version if not specified)",
)
@click.option(
    "-n", "--namespace", type=str, help="Namespace to install Jumpstarter components in", default="jumpstarter-lab"
)
@click.option("-i", "--ip", type=str, help="IP address of your host machine", default=None)
@click.option("-b", "--basedomain", type=str, help="Base domain of the Jumpstarter service", default=None)
@click.option("-g", "--grpc-endpoint", type=str, help="The gRPC endpoint to use for the Jumpstarter API", default=None)
@click.option("-r", "--router-endpoint", type=str, help="The gRPC endpoint to use for the router", default=None)
@click.option("-v", "--version", help="The version of the service to install", default=None)
@opt_kubeconfig
@opt_context
@opt_nointeractive
@opt_output_all
@blocking
async def create_cluster(
    name: str,
    kind: Optional[str],
    minikube: Optional[str],
    k3s: Optional[str],
    force_recreate: bool,
    kind_extra_args: str,
    minikube_extra_args: str,
    extra_certs: Optional[str],
    skip_install: bool,
    operator_installer: Optional[str],
    namespace: str,
    ip: Optional[str],
    basedomain: Optional[str],
    grpc_endpoint: Optional[str],
    router_endpoint: Optional[str],
    version: Optional[str],
    kubeconfig: Optional[str],
    context: Optional[str],
    nointeractive: bool,
    output: OutputType,
):
    """Create a Kubernetes cluster for running Jumpstarter"""
    cluster_type = validate_cluster_type_selection(kind, minikube, k3s)

    if output is None:
        if kind is None and minikube is None and k3s is None:
            click.echo(f"Auto-detected {cluster_type} as the cluster type")
        if skip_install:
            click.echo(f'Creating {cluster_type} cluster "{name}"...')
        else:
            click.echo(f'Creating {cluster_type} cluster "{name}" and installing Jumpstarter...')

    # Auto-detect version if not specified and installing Jumpstarter
    if not skip_install and version is None:
        from jumpstarter_cli_common.version import get_client_version
        from jumpstarter_kubernetes import get_latest_compatible_controller_version

        version = await get_latest_compatible_controller_version(get_client_version())

    # Create callback for library functions
    # Use silent mode when JSON/YAML output is requested
    callback = ClickCallback(silent=(output is not None))

    try:
        await create_cluster_and_install(
            cluster_type,
            force_recreate,
            name,
            kind_extra_args,
            minikube_extra_args,
            kind or "kind",
            minikube or "minikube",
            extra_certs,
            install_jumpstarter=not skip_install,
            namespace=namespace,
            version=version,
            kubeconfig=kubeconfig,
            context=context,
            ip=ip,
            basedomain=basedomain,
            grpc_endpoint=grpc_endpoint,
            router_endpoint=router_endpoint,
            callback=callback,
            k3s_ssh_host=k3s,
            operator_installer=operator_installer,
        )
    except JumpstarterKubernetesError as e:
        # Convert library exceptions to CLI exceptions
        raise click.ClickException(str(e)) from e

    if output is None:
        if skip_install:
            click.echo(f'Cluster "{name}" is ready for Jumpstarter installation.')
        else:
            click.echo(f'Cluster "{name}" created and Jumpstarter installed successfully!')
