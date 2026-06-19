import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.forward import rust_command


@click.group("import", cls=AliasedGroup)
def import_res():
    """Import configs from a Kubernetes cluster"""


# `import client` / `import exporter` run on the Rust core (forwarded via FFI): it fetches the
# resource credentials + cluster CA and writes the local client/exporter config — no Python
# pydantic config models.
import_res.add_command(rust_command(["admin", "import", "client"], "Import a client config from the cluster."))
import_res.add_command(rust_command(["admin", "import", "exporter"], "Import an exporter config from the cluster."))
