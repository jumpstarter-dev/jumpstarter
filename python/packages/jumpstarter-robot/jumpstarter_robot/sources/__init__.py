"""Where a device tree comes from.

Every source returns a ``jumpstarter_robot.tree.DeviceTree``, so one renderer
serves them all:

- ``from_exporter_config(path_or_alias)``: a local exporter config, offline
- ``from_report(client)``: a connected exporter (live, includes driver-built children)
"""

from __future__ import annotations

from ._common import SourceError
from .exporter_config import from_exporter_config
from .report import from_report

__all__ = ["SourceError", "from_exporter_config", "from_report"]
