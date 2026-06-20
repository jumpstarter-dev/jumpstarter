import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.forward import rust_command


@click.group(cls=AliasedGroup)
def get():
    """Get Jumpstarter Kubernetes objects"""


# All of `admin get` runs on the Rust core (forwarded via FFI): client/exporter/lease read the
# resources from the controller's namespace; cluster/clusters enumerate local kubeconfig clusters
# (kind/minikube/remote detection) via the jumpstarter-cluster crate.
get.add_command(rust_command(["admin", "get", "client"], "Display the client objects in the cluster."))
get.add_command(rust_command(["admin", "get", "exporter"], "Display the exporter objects in the cluster."))
get.add_command(rust_command(["admin", "get", "lease"], "Display the lease objects in the cluster."))
get.add_command(rust_command(["admin", "get", "cluster"], "Get information about a specific cluster or list all."))
get.add_command(rust_command(["admin", "get", "clusters"], "List all Kubernetes clusters with Jumpstarter status."))
