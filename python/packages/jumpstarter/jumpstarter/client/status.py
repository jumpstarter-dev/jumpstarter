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
    * ``NO_COLOR`` environment variable is set (spirit: plain text output).
    * ``stdout`` is not a TTY (output piped to a file / another process).
    * ``TERM`` is not set or does not match a known emoji-capable prefix
      (e.g. ``linux``, ``vt100``, ``dumb``, ``ansi`` all fall back to ASCII).
    """
    if os.environ.get("NO_COLOR") is not None:
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    return term.startswith(_EMOJI_TERM_PREFIXES)


# Central mapping:  status -> (emoji, ascii, description)
# Keep the help text in ``STATUS_HELP_TEXT`` in sync when editing this dict.
STATUS_ICONS: dict[ExporterStatus | None, tuple[str, str, str]] = {
    ExporterStatus.AVAILABLE: ("⚪", "+", "available"),
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
    ASCII characters (respects ``NO_COLOR``, non-TTY output, and
    terminals without known emoji support).
    """
    emoji_idx = 0 if _use_emoji() else 1
    return STATUS_ICONS.get(status, _FALLBACK)[emoji_idx]


# Static help text kept next to the mapping so updates stay in sync.
STATUS_HELP_TEXT = "Status icons: + available, x offline, ~ leased, * hook running, ! hook failed, ? unknown"
