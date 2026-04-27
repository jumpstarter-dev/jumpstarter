"""Locate ST-LINK USB mass storage mounts."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_STLINK_MARKER_FILES = frozenset({"MBED.HTM", "DETAILS.TXT"})

_KNOWN_VOLUME_PREFIXES = (
    "NOD_",
    "DIS_",
    "NUCLEO",
)


def looks_like_stlink_volume(path: Path) -> bool:
    """Return True if ``path`` looks like an ST-LINK USB mass storage root."""
    if not path.is_dir():
        return False

    if any((path / name).is_file() for name in _STLINK_MARKER_FILES):
        return True

    name_upper = path.name.upper()
    return any(name_upper.startswith(prefix) for prefix in _KNOWN_VOLUME_PREFIXES)


def _parse_proc_mounts(text: str) -> list[Path]:
    out: list[Path] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        mnt = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), parts[1])
        out.append(Path(mnt))
    return out


def _mount_points_linux() -> list[Path]:
    try:
        return _parse_proc_mounts(Path("/proc/mounts").read_text(encoding="utf-8"))
    except OSError:
        return []


def _mount_points_darwin() -> list[Path]:
    try:
        return [p for p in Path("/Volumes").iterdir() if p.is_dir()]
    except OSError:
        return []


def iter_mount_candidates() -> list[Path]:
    if sys.platform == "linux":
        return _mount_points_linux()
    if sys.platform == "darwin":
        return _mount_points_darwin()
    return []


def find_stlink_mount(volume_name: str | None = None) -> Path | None:
    """Find a single ST-LINK mass storage mount point.

    If ``volume_name`` is given, match by volume directory name (case-insensitive).
    Otherwise auto-detect using marker files and known prefixes.
    """
    candidates = iter_mount_candidates()

    if volume_name:
        for path in candidates:
            if path.name.upper() == volume_name.upper():
                return path
        return None

    matches = [p for p in candidates if looks_like_stlink_volume(p)]
    return matches[0] if len(matches) == 1 else None


def find_all_stlink_mounts() -> list[Path]:
    """Return all mount points that look like ST-LINK volumes."""
    return sorted(
        (p for p in iter_mount_candidates() if looks_like_stlink_volume(p)),
        key=lambda p: str(p),
    )
