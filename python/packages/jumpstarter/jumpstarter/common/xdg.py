"""Minimal XDG base-directory helpers (stdlib only) — replaces the xdg-base-dirs dependency.

Follows the XDG Base Directory spec for the two paths Jumpstarter needs: the config home and
the runtime dir (with a temp-dir fallback, since the runtime dir is optional per spec).
"""

import os
import tempfile
from pathlib import Path


def xdg_config_home() -> Path:
    """``$XDG_CONFIG_HOME`` or ``~/.config``."""
    value = os.environ.get("XDG_CONFIG_HOME")
    return Path(value) if value else Path.home() / ".config"


def xdg_runtime_dir() -> Path:
    """``$XDG_RUNTIME_DIR`` or the system temp dir (the runtime dir has no spec'd fallback)."""
    value = os.environ.get("XDG_RUNTIME_DIR")
    return Path(value) if value else Path(tempfile.gettempdir())
