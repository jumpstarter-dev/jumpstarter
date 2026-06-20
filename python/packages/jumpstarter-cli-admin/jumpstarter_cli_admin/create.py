import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.forward import rust_command


@click.group(cls=AliasedGroup)
def create():
    """Create Jumpstarter Kubernetes objects"""


# All of `admin create` runs on the Rust core (forwarded via FFI): client/exporter talk to the
# controller's namespace; cluster provisions a local kind/minikube cluster (jumpstarter-cluster
# crate) and installs the Jumpstarter operator.
create.add_command(rust_command(["admin", "create", "client"], "Create a client object in the cluster."))
create.add_command(rust_command(["admin", "create", "exporter"], "Create an exporter object in the cluster."))
create.add_command(rust_command(["admin", "create", "cluster"], "Create a local kind/minikube cluster."))
