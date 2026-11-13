"""
Client-side decorators, one module per frontend.

- :mod:`.cli`: Click helpers for ``DriverClient.cli()``
- :mod:`.robot`: Robot Framework keyword decorators

The Click helpers are re-exported here, where they always lived.
"""

from . import cli, robot
from .cli import DriverClickGroup, driver_click_command, driver_click_group

__all__ = ["DriverClickGroup", "cli", "driver_click_command", "driver_click_group", "robot"]
