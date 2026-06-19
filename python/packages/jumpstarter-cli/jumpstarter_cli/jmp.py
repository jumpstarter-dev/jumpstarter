import click
from jumpstarter_cli_admin import admin
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.opt import opt_log_level
from jumpstarter_cli_common.version import version
from jumpstarter_cli_driver import driver

from ._forward import rust_command
from .completion import completion
from .login import login
from .run import run


@click.group(cls=AliasedGroup)
@opt_log_level
def jmp():
    """The Jumpstarter CLI"""


# Controller/lease/auth/config commands run on the Rust core (forwarded via FFI). The Python
# entrypoint stays for consistency; Rust does the parsing/output/exit codes.
jmp.add_command(rust_command("shell", "Acquire a lease and open a shell connected to an exporter."))
jmp.add_command(rust_command("create", "Create a resource."))
jmp.add_command(rust_command("delete", "Delete resources."))
jmp.add_command(rust_command("update", "Update a resource."))
jmp.add_command(rust_command("get", "Display one or many resources."))
jmp.add_command(rust_command("auth", "Authentication and token management commands."))
jmp.add_command(rust_command("config", "Modify jumpstarter config files."))

# Native Python commands (driver-dependent, or not yet ported).
jmp.add_command(completion)
jmp.add_command(run)
jmp.add_command(login)

jmp.add_command(driver)
jmp.add_command(admin)
jmp.add_command(version)

try:
    from jumpstarter_mcp.cli import mcp
except ModuleNotFoundError as exc:
    if exc.name != "jumpstarter_mcp":
        raise
else:
    jmp.add_command(mcp)

if __name__ == "__main__":
    jmp()
