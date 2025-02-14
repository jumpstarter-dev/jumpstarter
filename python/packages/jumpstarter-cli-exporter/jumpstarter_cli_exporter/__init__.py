import logging
from typing import Optional

import asyncclick as click
from jumpstarter_cli_common import AliasedGroup, opt_log_level, version

from .exporter import exporter_shell, run_exporter
from .exporter_config import create_exporter_config, delete_exporter_config, edit_exporter_config, list_exporter_configs
from .exporter_login import exporter_login


@click.group(cls=AliasedGroup)
@opt_log_level
def exporter(log_level: Optional[str]):
    """Jumpstarter exporter CLI tool"""
    if log_level:
        logging.basicConfig(level=log_level.upper())
    else:
        logging.basicConfig(level=logging.INFO)


exporter.add_command(create_exporter_config)
exporter.add_command(delete_exporter_config)
exporter.add_command(edit_exporter_config)
exporter.add_command(list_exporter_configs)
exporter.add_command(run_exporter)
exporter.add_command(exporter_login)
exporter.add_command(exporter_shell)
exporter.add_command(version)

if __name__ == "__main__":
    exporter()
