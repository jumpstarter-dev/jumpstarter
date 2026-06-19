import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.forward import rust_command


@click.group(cls=AliasedGroup)
def rotate():
    """Rotate credentials for Jumpstarter Kubernetes objects"""


# `rotate client` runs on the Rust core (forwarded via FFI): it rotates the cluster token and,
# with --save, updates the local client config with the new token.
rotate.add_command(rust_command(["admin", "rotate", "client"], "Rotate the token for a client object."))
