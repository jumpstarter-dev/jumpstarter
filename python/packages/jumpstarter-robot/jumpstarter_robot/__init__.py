"""Robot Framework libraries for Jumpstarter.

- ``jumpstarter_robot.Jumpstarter``: sessions, leases and cross-driver keywords
- ``jumpstarter_robot.Session``: declare a named session (used by generated resources)
- ``jumpstarter_robot.Driver``: the keywords of one driver of the tree

``jmp robot generate`` (or ``jmp-robot generate``) writes a client resource
that imports them for a given exporter.

The libraries load on first use, so importing ``jumpstarter_robot.cli`` (which
``jmp`` does at start) does not load Robot Framework.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .driver_library import Driver
    from .library import Jumpstarter
    from .session_library import Session

    JumpstarterLibrary = Jumpstarter

_EXPORTS = {
    "Driver": ".driver_library",
    "Jumpstarter": ".library",
    "JumpstarterLibrary": ".library",
    "Session": ".session_library",
}

__all__ = ["Driver", "Jumpstarter", "JumpstarterLibrary", "Session"]


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module, __name__), "Jumpstarter" if name == "JumpstarterLibrary" else name)
    globals()[name] = value
    return value
