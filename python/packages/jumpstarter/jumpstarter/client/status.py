"""Exporter status display: icons, ASCII fallbacks, and help text.

This module centralises the mapping between ``ExporterStatus`` values and
the visual indicators shown in CLI output.  It is deliberately kept
separate from ``grpc.py`` so that display logic does not leak into the
gRPC data-model layer.
"""

from __future__ import annotations

import os
import sys

from jumpstarter.common import ExporterStatus
from jumpstarter.common.display import display_options

_EMOJI_TERM_PREFIXES = (
    "xterm",
    "screen",
    "tmux",
    "rxvt",
    "alacritty",
    "kitty",
    "wezterm",
    "foot",
    "ghostty",
    "contour",
    "rio",
)
"""Terminal type prefixes whose modern implementations reliably render emoji."""


def _use_emoji() -> bool:
    """Return True when the output terminal is likely to support emoji.

    Falls back to ASCII indicators when any of the following is true:
    * ``NO_ICONS`` environment variable is set (ASCII-only output is
      requested, regardless of terminal capabilities).
    * ``stdout`` is not a TTY (output piped to a file / another process).
    * ``TERM`` is not set or does not match a known emoji-capable prefix
      (e.g. ``linux``, ``vt100``, ``dumb``, ``ansi`` all fall back to ASCII).

    ``NO_COLOR`` is intentionally not consulted here: per the
    `NO_COLOR convention <https://no-color.org/>`_ it only asks for ANSI
    color sequences to be omitted, not for icons/emoji to be replaced.
    Use ``NO_ICONS`` to control that.
    """
    opts = display_options()
    if opts.no_icons:
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    return term.startswith(_EMOJI_TERM_PREFIXES)


# Central mapping:  status -> (emoji, ascii, description)
# Keep the help text in ``STATUS_HELP_TEXT`` in sync when editing this dict.
STATUS_ICONS: dict[ExporterStatus | None, tuple[str, str, str]] = {
    ExporterStatus.AVAILABLE: ("🟢", "+", "available"),
    ExporterStatus.OFFLINE: ("❌", "x", "offline"),
    ExporterStatus.BEFORE_LEASE_HOOK: ("⚙️", "*", "hook running"),
    ExporterStatus.AFTER_LEASE_HOOK: ("⚙️", "*", "hook running"),
    ExporterStatus.LEASE_READY: ("🔒", "~", "leased"),
    ExporterStatus.BEFORE_LEASE_HOOK_FAILED: ("❗", "!", "hook failed"),
    ExporterStatus.AFTER_LEASE_HOOK_FAILED: ("❗", "!", "hook failed"),
    None: ("❓", "?", "unknown"),
}

_FALLBACK = STATUS_ICONS[None]


def status_icon(status: ExporterStatus | None) -> str:
    """Return a single-character icon for *status*.

    Uses emoji when the terminal supports it, otherwise falls back to
    ASCII characters (respects ``NO_ICONS``, non-TTY output, and
    terminals without known emoji support).
    """
    emoji_idx = 0 if _use_emoji() else 1
    return STATUS_ICONS.get(status, _FALLBACK)[emoji_idx]


def status_help_text() -> str:
    """Return a one-line legend for the status icons.

    Picks emoji or ASCII indicators based on terminal capabilities so
    the ``--help`` output matches what the user would actually see.
    """
    emoji_idx = 0 if _use_emoji() else 1
    # Deduplicate entries that share the same icon and description
    seen: set[tuple[str, str]] = set()
    parts: list[str] = []
    for icon_emoji, icon_ascii, desc in STATUS_ICONS.values():
        icon = icon_emoji if emoji_idx == 0 else icon_ascii
        key = (icon, desc)
        if key not in seen:
            seen.add(key)
            parts.append(f"{icon}  {desc}")
    return "Status icons: " + ", ".join(parts)
