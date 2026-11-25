"""Keywords that work on any driver path, merged into the ``Jumpstarter`` library.

Each module defines one class whose methods are Robot Framework keywords
(decorated with ``robot.api.deco.keyword``).
"""

from __future__ import annotations


def load_all() -> list[type]:
    """The keyword set classes, in the order their keywords are documented."""
    from .adb import AdbKeywords
    from .appium import AppiumKeywords
    from .contexts import ContextKeywords

    return [ContextKeywords, AppiumKeywords, AdbKeywords]
