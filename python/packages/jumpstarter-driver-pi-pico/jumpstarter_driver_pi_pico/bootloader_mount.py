"""Locate Raspberry Pi Pico BOOTSEL USB MSC mounts without picotool."""

from __future__ import annotations

import string
import sys
from pathlib import Path

# Either file is present on Pico-series BOOTSEL volumes (Raspberry Pi docs).
_UF2_MARKER_FILES = frozenset({"INFO_UF2.TXT", "INDEX.HTM"})


def looks_like_pico_boot_volume(path: Path) -> bool:
    """Return True if ``path`` looks like a Pico BOOTSEL USB MSC root."""
    if not path.is_dir():
        return False
    return any((path / name).is_file() for name in _UF2_MARKER_FILES)


def _parse_proc_mounts_mount_points(text: str) -> list[Path]:
    """Parse Linux ``/proc/mounts`` lines into mount-point paths (field 2, unescaped)."""
    out: list[Path] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        # Mount point is the second field; spaces appear as \040 in /proc/mounts.
        mnt = parts[1].replace("\\040", " ")
        out.append(Path(mnt))
    return out


def _mount_points_linux() -> list[Path]:
    try:
        return _parse_proc_mounts_mount_points(Path("/proc/mounts").read_text(encoding="utf-8"))
    except OSError:
        return []


def _mount_points_darwin() -> list[Path]:
    try:
        return [p for p in Path("/Volumes").iterdir() if p.is_dir()]
    except OSError:
        return []


def _mount_points_windows() -> list[Path]:
    return [Path(f"{letter}:/") for letter in string.ascii_uppercase]


def iter_bootloader_mount_candidates() -> list[Path]:
    """Return filesystem paths to scan for a Pico BOOTSEL volume (marker files at root).

    Unlike picotool (USB protocol), UF2 flashing needs a host path to the mounted FAT
    volume. We discover it by finding mount points whose root contains ``INFO_UF2.TXT``
    or ``INDEX.HTM`` — no volume *name* configuration required.
    """
    if sys.platform == "linux":
        return _mount_points_linux()
    if sys.platform == "darwin":
        return _mount_points_darwin()
    if sys.platform == "win32":
        return _mount_points_windows()
    return []


def find_all_bootloader_mounts() -> list[Path]:
    """Return all mount points whose root looks like a Pico BOOTSEL volume."""
    matches: list[Path] = []
    for path in iter_bootloader_mount_candidates():
        if looks_like_pico_boot_volume(path):
            matches.append(path)
    return sorted(matches, key=lambda p: str(p))
