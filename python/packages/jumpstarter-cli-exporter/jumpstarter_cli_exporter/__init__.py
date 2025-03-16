import logging
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import AliasedGroup, opt_log_level, version

from .config import config
from .exporter import exporter_shell, run_exporter
from .exporter_login import exporter_login


@click.group(cls=AliasedGroup)
@opt_log_level
def exporter(log_level: Optional[str]):
    """Jumpstarter exporter CLI tool"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


exporter.add_command(config)
exporter.add_command(run_exporter)
exporter.add_command(exporter_login)
exporter.add_command(exporter_shell)
exporter.add_command(version)

if __name__ == "__main__":
    exporter()
