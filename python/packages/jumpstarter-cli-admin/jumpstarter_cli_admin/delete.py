import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.forward import rust_command


@click.group(cls=AliasedGroup)
def delete():
    """Delete Jumpstarter Kubernetes objects"""


# All of `admin delete` runs on the Rust core (forwarded via FFI): client/exporter delete the
# controller object (and, with --delete, the local config); cluster tears down a local
# kind/minikube cluster (jumpstarter-cluster crate).
delete.add_command(rust_command(["admin", "delete", "client"], "Delete a client object in the cluster."))
delete.add_command(rust_command(["admin", "delete", "exporter"], "Delete an exporter object in the cluster."))
delete.add_command(rust_command(["admin", "delete", "cluster"], "Delete a local Kubernetes cluster."))
