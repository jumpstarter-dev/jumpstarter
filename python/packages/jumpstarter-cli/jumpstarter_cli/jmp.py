import click
from jumpstarter_cli_admin import admin
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.opt import opt_log_level
from jumpstarter_cli_common.version import version
from jumpstarter_cli_driver import driver

from .auth import auth
from .completion import completion
from .config import config
from .create import create
from .delete import delete
from .get import get
from .codegen import codegen
from .proto import proto
from .login import login
from .run import run
from .shell import shell
from .update import update
from .validate import validate


@click.group(cls=AliasedGroup)
@opt_log_level
def jmp():
    """The Jumpstarter CLI"""


jmp.add_command(auth)
jmp.add_command(completion)
jmp.add_command(create)
jmp.add_command(delete)
jmp.add_command(update)
jmp.add_command(get)
jmp.add_command(shell)
jmp.add_command(run)
jmp.add_command(login)
jmp.add_command(config)

jmp.add_command(codegen)
jmp.add_command(driver)
jmp.add_command(proto)
jmp.add_command(proto, "interface")  # backward compat alias
jmp.add_command(validate)
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
