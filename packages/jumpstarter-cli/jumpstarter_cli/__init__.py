import logging

import asyncclick as click
from jumpstarter_cli_admin import admin
from jumpstarter_cli_common import AliasedGroup, opt_log_level, version

from .config import config
from .create import create
from .delete import delete
from .get import get
from .j import j
from .login import login
from .run import run
from .shell import shell
from .update import update


@click.group(cls=AliasedGroup)
@opt_log_level
def jmp(log_level):
    """The Jumpstarter CLI"""

    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


jmp.add_command(create)
jmp.add_command(delete)
jmp.add_command(update)
jmp.add_command(get)
jmp.add_command(shell)
jmp.add_command(run)
jmp.add_command(login)
jmp.add_command(config)

jmp.add_command(admin)
jmp.add_command(version)

__all__ = ["jmp", "j"]

if __name__ == "__main__":
    jmp()
