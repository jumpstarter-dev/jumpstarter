"""Curated keyword sets, merged into the ``Jumpstarter`` library.

Each module defines one class whose methods are Robot Framework keywords
(decorated with ``robot.api.deco.keyword``). Modules import optional
dependencies (SSHLibrary, adbutils, Appium) only when a keyword runs, so
every keyword exists, with documentation, even when its extra is not
installed.
"""

from __future__ import annotations

import importlib

# module name → keyword set class name
KEYWORD_SETS = {
    "console": "ConsoleKeywords",
    "contexts": "ContextKeywords",
    "media": "MediaKeywords",
    "appium": "AppiumKeywords",
    "ssh": "SshKeywords",
    "adb": "AdbKeywords",
    "ios": "IosKeywords",
    "connect": "ConnectKeywords",
}


def load_all() -> list[type]:
    """Import every keyword set that exists, returning their classes in order."""
    classes = []
    for module_name, class_name in KEYWORD_SETS.items():
        qualified = f"{__name__}.{module_name}"
        try:
            module = importlib.import_module(qualified)
        except ModuleNotFoundError as e:
            if e.name == qualified:
                continue
            raise
        classes.append(getattr(module, class_name))
    return classes
