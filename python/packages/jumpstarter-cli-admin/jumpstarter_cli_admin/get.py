import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.blocking import blocking
from jumpstarter_cli_common.forward import rust_command
from jumpstarter_cli_common.opt import (
    OutputType,
    opt_output_all,
)
from jumpstarter_cli_common.print import model_print
from jumpstarter_kubernetes import (
    get_cluster_info,
    list_clusters,
)


@click.group(cls=AliasedGroup)
def get():
    """Get Jumpstarter Kubernetes objects"""


# `get client` / `get exporter` / `get lease` run on the Rust core (forwarded via FFI): it lists
# the resources from the cluster and renders the typed table (incl. `get exporter --devices`).
# The `cluster` / `clusters` subcommands (local kind/minikube/remote detection) have no Rust
# equivalent and stay native Python.
get.add_command(rust_command(["admin", "get", "client"], "Display the client objects in the cluster."))
get.add_command(rust_command(["admin", "get", "exporter"], "Display the exporter objects in the cluster."))
get.add_command(rust_command(["admin", "get", "lease"], "Display the lease objects in the cluster."))


@get.command("cluster")
@click.argument("name", type=str, required=False, default=None)
@click.option(
    "--type", type=click.Choice(["kind", "minikube", "remote", "all"]), default="all", help="Filter clusters by type"
)
@click.option("--kubectl", type=str, help="Path or name of kubectl executable", default="kubectl")
@click.option("--minikube", type=str, help="Path or name of minikube executable", default="minikube")
@opt_output_all
@blocking
async def get_cluster(name, type: str, kubectl: str, minikube: str, output: OutputType):
    """Get information about a specific cluster or list all clusters"""
    try:
        if name is not None:
            # Get specific cluster by context name
            cluster_info = await get_cluster_info(name, kubectl, minikube)

            # Check if the cluster context was found
            if cluster_info.error and "not found" in cluster_info.error:
                raise click.ClickException(f'Kubernetes context "{name}" not found')

            model_print(cluster_info, output)
        else:
            # List all clusters if no name provided
            cluster_list = await list_clusters(type, kubectl, minikube)
            model_print(cluster_list, output)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error getting cluster info: {e}") from e


@get.command("clusters")
@click.option(
    "--type", type=click.Choice(["kind", "minikube", "remote", "all"]), default="all", help="Filter clusters by type"
)
@click.option("--kubectl", type=str, help="Path or name of kubectl executable", default="kubectl")
@click.option("--minikube", type=str, help="Path or name of minikube executable", default="minikube")
@opt_output_all
@blocking
async def get_clusters(type: str, kubectl: str, minikube: str, output: OutputType):
    """List all Kubernetes clusters with Jumpstarter status"""
    try:
        cluster_list = await list_clusters(type, kubectl, minikube)

        # Use model_print for all output formats
        model_print(cluster_list, output)
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Error listing clusters: {e}") from e
