from typing import Optional

import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.callbacks import ClickCallback, ForceClickCallback, SilentWithConfirmCallback
from jumpstarter_cli_common.forward import rust_command
from jumpstarter_cli_common.opt import (
    NameOutputType,
    opt_output_name_only,
)
from jumpstarter_kubernetes import delete_cluster_by_name
from jumpstarter_kubernetes.exceptions import JumpstarterKubernetesError


@click.group(cls=AliasedGroup)
def delete():
    """Delete Jumpstarter Kubernetes objects"""


# `delete client` / `delete exporter` run on the Rust core (forwarded via FFI): it deletes the
# cluster object and, with --delete, the local config. The `delete cluster` subcommand (local
# kind/minikube teardown) has no Rust equivalent and stays native Python.
delete.add_command(rust_command(["admin", "delete", "client"], "Delete a client object in the cluster."))
delete.add_command(rust_command(["admin", "delete", "exporter"], "Delete an exporter object in the cluster."))


@delete.command("cluster")
@click.argument("name", type=str, required=False, default="jumpstarter-lab")
@click.option("--kind", is_flag=False, flag_value="kind", default=None, help="Delete a local Kind cluster")
@click.option(
    "--minikube",
    is_flag=False,
    flag_value="minikube",
    default=None,
    help="Delete a local Minikube cluster",
)
@click.option(
    "--force",
    is_flag=True,
    help="Skip confirmation prompt and force deletion",
)
@opt_output_name_only
@blocking
async def delete_cluster(
    name: str,
    kind: Optional[str],
    minikube: Optional[str],
    force: bool,
    output: NameOutputType,
):
    """Delete a Kubernetes cluster (auto-detects Kind or Minikube)"""

    # Determine cluster type from options
    cluster_type = None
    if kind is not None:
        cluster_type = "kind"
    elif minikube is not None:
        cluster_type = "minikube"

    # Create appropriate callback based on output mode and force flag
    if output is not None:
        # For --output=name, use silent callback that still prompts for confirmation
        callback = ForceClickCallback(silent=True) if force else SilentWithConfirmCallback()
    else:
        # For normal output, use regular callbacks
        callback = ForceClickCallback(silent=False) if force else ClickCallback(silent=False)

    try:
        await delete_cluster_by_name(name, cluster_type, force, callback)
        if output is not None:
            # For name-only output, just print the cluster name
            click.echo(name)
    except JumpstarterKubernetesError as e:
        # Convert library exceptions to CLI exceptions
        raise click.ClickException(str(e)) from e
