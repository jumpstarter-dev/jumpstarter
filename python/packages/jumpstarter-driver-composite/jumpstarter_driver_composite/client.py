import logging
from dataclasses import dataclass

import click
from rich import traceback

from jumpstarter.client import DriverClient
from jumpstarter.client.base import StubDriverClient
from jumpstarter.client.decorators import driver_click_group


def _opt_log_level_callback(ctx, param, value):
    traceback.install()

    # Nested CompositeClient groups each expose --log-level. Click invokes the
    # callback with None when the option is omitted, which must not reset a
    # level already set on a parent (e.g. `j --log-level DEBUG qnx power on`).
    if value is None:
        return

    log_level = value.upper()

    # Update the root logger level to ensure all loggers inherit it
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Driver clients set an explicit level on their loggers, so updating root
    # alone is not enough — bump every existing logger too.
    for logger_name in logging.Logger.manager.loggerDict:
        logger = logging.getLogger(logger_name)
        logger.setLevel(log_level)


@dataclass(kw_only=True)
class CompositeClient(DriverClient):
    def __getattr__(self, name):
        try:
            return self.children[name]
        except KeyError:
            raise AttributeError(name) from None

    def close(self):
        for v in self.children.values():
            if hasattr(v, "close"):
                v.close()

    def cli(self):
        @driver_click_group(self)
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

        for k, v in self.children.items():
            if isinstance(v, StubDriverClient):
                continue
            if hasattr(v, "cli"):
                base.add_command(v.cli(), k)  # ty: ignore[call-non-callable]

        return base
