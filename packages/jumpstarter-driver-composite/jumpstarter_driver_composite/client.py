import logging
from dataclasses import dataclass

import click
from rich import traceback

from jumpstarter.client import DriverClient


def _opt_log_level_callback(ctx, param, value):
    traceback.install()

    # Set the log level
    log_level = value.upper() if value else "INFO"

    # Update the root logger level to ensure all loggers inherit it
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Update all existing loggers to use the new level
    for logger_name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)


@dataclass(kw_only=True)
class CompositeClient(DriverClient):
    def __getattr__(self, name):
        return self.children[name]

    def close(self):
        for _, v in self.children.items():
            if hasattr(v, "close"):
                v.close()

    def cli(self):
        @click.group
        @click.option(
            "--log-level",
            "log_level",
            type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]),
            help="Set the log level",
            expose_value=False,
            callback=_opt_log_level_callback,
        )
        def base():
            """Generic composite device"""
            pass

        for k, v in self.children.items():
            if hasattr(v, "cli"):
                base.add_command(v.cli(), k)

        return base
