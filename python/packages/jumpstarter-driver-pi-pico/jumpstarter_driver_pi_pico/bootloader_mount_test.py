from pathlib import Path

from .bootloader_mount import (
    _parse_proc_mounts_mount_points,
    find_all_bootloader_mounts,
    looks_like_pico_boot_volume,
)


def test_looks_like_pico_boot_volume(tmp_path: Path):
    assert looks_like_pico_boot_volume(tmp_path) is False
    (tmp_path / "INFO_UF2.TXT").write_text("Model: RP2040\n")
    assert looks_like_pico_boot_volume(tmp_path) is True


def test_parse_proc_mounts_escapes_spaces():
    text = "/dev/sda1 /media/foo\\040bar vfat rw 0 0\n"
    mount_points = _parse_proc_mounts_mount_points(text)
    assert mount_points == [Path("/media/foo bar")]


def test_find_all_bootloader_mount(monkeypatch, tmp_path: Path):
    vol = tmp_path / "anything"
    vol.mkdir()
    (vol / "INDEX.HTM").write_text("<html></html>")
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.bootloader_mount.iter_bootloader_mount_candidates",
        lambda: [vol],
    )
    assert find_all_bootloader_mounts() == [vol]


def test_find_all_bootloader_mount_none(monkeypatch, tmp_path: Path):
    vol = tmp_path / "empty"
    vol.mkdir()
    monkeypatch.setattr(
        "jumpstarter_driver_pi_pico.bootloader_mount.iter_bootloader_mount_candidates",
        lambda: [vol],
    )
    assert find_all_bootloader_mounts() == []
