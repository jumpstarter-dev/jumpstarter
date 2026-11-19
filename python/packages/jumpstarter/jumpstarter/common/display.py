"""Shared helpers for display preferences (colors, icons).

Centralises reading of the ``NO_COLOR`` and ``NO_ICONS`` environment
variables so that all display-related code (CLI status icons, shell
prompts) shares a single source of truth.

The two variables are deliberately kept independent:

* ``NO_COLOR`` (see https://no-color.org/) only asks for ANSI color
  sequences to be omitted from shell prompts. It does not affect
  whether emoji/icons are shown.
* ``NO_ICONS`` asks for ASCII characters instead of emoji/Unicode
  icons in status output and shell prompts. It does not affect color.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["DisplayOptions", "display_options"]


@dataclass(frozen=True)
class DisplayOptions:
    """Display preferences derived from the environment.

    Attributes:
        no_color: ``NO_COLOR`` is set (ANSI color sequences should be omitted).
        no_icons: ``NO_ICONS`` is set (ASCII-only output is requested).
    """

    no_color: bool = False
    no_icons: bool = False


def display_options() -> DisplayOptions:
    """Read ``NO_COLOR`` / ``NO_ICONS`` from the environment once.

    Both variables follow the "set to any value, including empty"
    convention, so presence rather than truthiness matters.
    """
    return DisplayOptions(
        no_color=os.environ.get("NO_COLOR") is not None,
        no_icons=os.environ.get("NO_ICONS") is not None,
    )
